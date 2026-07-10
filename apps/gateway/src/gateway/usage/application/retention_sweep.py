"""RetentionSweeper — periodic bounded DELETE of aged time-series rows.

CONTRACT (FROZEN @ data-retention-controls v1 — TASK.md §3):
  - sweep_once() -> dict[str, int]: for each enabled table, DELETE rows older than
    the per-table window in bounded batches; returns {table: rows_deleted}.
  - run_forever(interval_seconds, *, _sleep): background loop; swallows all errors;
    cancellable via asyncio.CancelledError propagation. _sleep is injectable for tests.
  - should_start_retention_sweep(settings) -> bool: interval>0 AND at least one window>0.
  - effective_audit_window: max(retention_audit_events_days, retention_audit_floor_days).
  - AUDIT FLOOR: effective = max(audit_events_days, audit_floor_days) — the floor is
    inviolable; even if the operator sets audit_events_days=30, the effective window
    is max(30, floor_days). This prevents accidental deletion of audit data needed
    for compliance.
  - AUDIT PURGE: wraps DELETE in SET LOCAL app.audit_purge='on' (transaction-scoped
    GUC) to satisfy the immutability trigger. Each table batch runs in its own
    transaction; the GUC is scoped to that single transaction (SET LOCAL semantics).
  - FAIL-OPEN: sweep_once() failures are logged and returned as empty dict; the
    background loop never crashes. Each table's failure is isolated.
  - AUDITED: each purge that deletes > 0 rows emits record_audit(action="data.purge")
    as an asyncio.create_task() fire-and-forget (fail-open; uses system-level event).
  SAFETY:
    - NEVER deletes rows newer than now() - window.
    - Audit window ALWAYS >= floor (enforced in effective_audit_window property).
    - Batched: each DELETE is bounded to retention_batch_size rows; loops until done.
    - Audit purge uses a transaction-scoped GUC — no global state mutation.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

_log = logging.getLogger(__name__)


class _Settings(Protocol):
    """Minimal protocol for settings consumed by RetentionSweeper."""

    retention_check_interval_seconds: int
    retention_usage_records_days: int
    retention_alert_events_days: int
    retention_audit_events_days: int
    retention_audit_floor_days: int
    retention_batch_size: int
    # payload-capture-store additive field — request_logs mirrors usage_records exactly
    # (plain _delete_batched, no immutability trigger).
    retention_request_logs_days: int


def should_start_retention_sweep(settings: _Settings) -> bool:
    """The default-ON guard: start the sweeper when interval>0 AND at least one window>0.

    A zero (or negative) interval disables the sweeper entirely. When the interval is
    positive, at least one per-table window must also be positive for the sweep to be
    meaningful. If all windows are 0 (all tables opted out), there is nothing to sweep.
    """
    if settings.retention_check_interval_seconds <= 0:
        return False
    return (
        settings.retention_usage_records_days > 0
        or settings.retention_alert_events_days > 0
        or settings.retention_audit_events_days > 0
    )


def _naive_utc_cutoff(days: int) -> datetime.datetime:
    """Compute a naive UTC cutoff datetime N days in the past.

    asyncpg binds TIMESTAMP WITHOUT TIME ZONE columns as naive UTC datetimes —
    passing an aware datetime raises a type error at the asyncpg layer.
    The pattern `.replace(tzinfo=None)` mirrors recovery_sweep.py and reconciliation.py.
    """
    return (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=days)).replace(
        tzinfo=None
    )


# ---------------------------------------------------------------------------
# Batch DELETE SQL templates
# ---------------------------------------------------------------------------

# usage_records: simple age cutoff on created_at
_DELETE_USAGE_BATCH = text(
    """
    DELETE FROM usage_records
     WHERE id IN (
           SELECT id FROM usage_records
            WHERE created_at < :cutoff
            LIMIT :batch
     )
    """
)

# alert_events: simple age cutoff on created_at
_DELETE_ALERT_BATCH = text(
    """
    DELETE FROM alert_events
     WHERE id IN (
           SELECT id FROM alert_events
            WHERE created_at < :cutoff
            LIMIT :batch
     )
    """
)

# request_logs: simple age cutoff on created_at — mirrors usage_records exactly
# (plain _delete_batched, no immutability trigger — request_logs has no compliance
# requirement for immutability; the whole point of the store is to hold LESS-durable
# PII, payload-capture-store TASK.md §0 GROUND).
_DELETE_REQUEST_LOGS_BATCH = text(
    """
    DELETE FROM request_logs
     WHERE id IN (
           SELECT id FROM request_logs
            WHERE created_at < :cutoff
            LIMIT :batch
     )
    """
)

# audit_events: must run inside a transaction with SET LOCAL app.audit_purge='on'
_SET_AUDIT_PURGE_GUC = text("SET LOCAL app.audit_purge = 'on'")

_DELETE_AUDIT_BATCH = text(
    """
    DELETE FROM audit_events
     WHERE id IN (
           SELECT id FROM audit_events
            WHERE created_at < :cutoff
            LIMIT :batch
     )
    """
)


class RetentionSweeper:
    """Periodically delete aged rows from time-series tables in bounded batches.

    Tables: usage_records, alert_events, audit_events (each independently configurable).
    Audit table has a HARD floor: effective_audit_window = max(knob, floor).
    Audit deletes use a transaction-scoped GUC (SET LOCAL app.audit_purge='on') to
    bypass the immutability trigger while keeping ordinary-code immutability intact.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: _Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    @property
    def effective_audit_window(self) -> int:
        """Effective audit window in days (max of knob and floor).

        When audit_events_days > 0, the floor is applied: effective = max(days, floor).
        When audit_events_days == 0, the table is skipped entirely (no floor applied).
        """
        days = self._settings.retention_audit_events_days
        if days <= 0:
            return 0
        return max(days, self._settings.retention_audit_floor_days)

    async def _delete_batched(
        self,
        table: str,
        delete_sql: Any,
        cutoff: datetime.datetime,
        batch_size: int,
    ) -> int:
        """Delete aged rows in bounded batches; returns total rows deleted.

        Loops until a batch returns fewer rows than batch_size (meaning no more aged rows
        exist). Each batch runs in its own transaction for minimal lock duration.
        Never raises — failures are logged and returned as 0.
        """
        total = 0
        try:
            while True:
                async with self._session_factory() as session:
                    async with session.begin():
                        cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                            delete_sql, {"cutoff": cutoff, "batch": batch_size}
                        )
                        batch_deleted = cursor.rowcount or 0
                total += batch_deleted
                _log.debug(
                    "retention_sweep: deleted %d rows from %s (batch), total=%d",
                    batch_deleted,
                    table,
                    total,
                )
                if batch_deleted < batch_size:
                    break  # no more aged rows
        except Exception as exc:
            _log.warning(
                "retention_sweep: batch delete from %s failed (swallowed): %s",
                table,
                exc,
                exc_info=exc,
            )
            return 0
        return total

    async def _delete_audit_batched(
        self,
        cutoff: datetime.datetime,
        batch_size: int,
    ) -> int:
        """Delete aged audit rows via SET LOCAL GUC bypass; returns total rows deleted.

        Each batch runs in its own transaction with SET LOCAL app.audit_purge='on' scoped
        to that transaction. The GUC expires automatically at transaction commit — no
        global state is mutated. The immutability trigger checks this setting and allows
        the DELETE to proceed.
        """
        total = 0
        try:
            while True:
                async with self._session_factory() as session:
                    async with session.begin():
                        # SET LOCAL is transaction-scoped: expires at COMMIT/ROLLBACK
                        await session.execute(_SET_AUDIT_PURGE_GUC)
                        audit_cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                            _DELETE_AUDIT_BATCH, {"cutoff": cutoff, "batch": batch_size}
                        )
                        batch_deleted = audit_cursor.rowcount or 0
                total += batch_deleted
                _log.debug(
                    "retention_sweep: deleted %d audit_events rows (bypass batch), total=%d",
                    batch_deleted,
                    total,
                )
                if batch_deleted < batch_size:
                    break
        except Exception as exc:
            _log.warning(
                "retention_sweep: audit batch delete failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return 0
        return total

    async def _emit_purge_audit(
        self,
        *,
        table: str,
        cutoff: datetime.datetime,
        rows_deleted: int,
    ) -> None:
        """Fire-and-forget: emit a data.purge audit event (fail-open).

        Uses system-level (tenant_id=None, actor_user_id=None) AuditEvent — this is a
        platform-initiated purge, not a user action. Swallows all exceptions.
        """
        try:
            import uuid as _uuid

            from gateway.audit.application.audit_writer import record_audit
            from gateway.audit.domain.audit_event import AuditEvent

            event = AuditEvent(
                id=_uuid.uuid4(),
                tenant_id=None,  # system event
                actor_user_id=None,  # system event
                actor_email=None,
                action="data.purge",
                target_type="retention",
                target_id=table,
                result="success",
                metadata={
                    "table": table,
                    "cutoff_iso": cutoff.isoformat(),
                    "rows_deleted": rows_deleted,
                },
                created_at=datetime.datetime.now(datetime.UTC),
            )
            await record_audit(self._session_factory, event)
        except Exception as exc:
            _log.warning(
                "retention_sweep: audit emit failed for table=%s (swallowed): %s",
                table,
                exc,
                exc_info=exc,
            )

    async def sweep_once(self) -> dict[str, int]:
        """One sweep cycle: delete aged rows from all enabled tables.

        Returns {table_name: rows_deleted} for tables that were processed.
        Tables with window=0 are skipped (no entry in result dict).
        NEVER raises — any per-table failure is logged and counted as 0.
        """
        results: dict[str, int] = {}
        batch_size = self._settings.retention_batch_size or 1000
        # Background audit tasks: store references so GC cannot collect them before they
        # complete (RUF006). Fire-and-forget — we do not await; each is fail-open.
        _audit_tasks: set[asyncio.Task[None]] = set()

        try:
            # ── usage_records ──────────────────────────────────────────────
            usage_days = self._settings.retention_usage_records_days
            if usage_days > 0:
                cutoff = _naive_utc_cutoff(usage_days)
                deleted = await self._delete_batched(
                    "usage_records", _DELETE_USAGE_BATCH, cutoff, batch_size
                )
                results["usage_records"] = deleted
                if deleted > 0:
                    _t = asyncio.create_task(
                        self._emit_purge_audit(
                            table="usage_records",
                            cutoff=cutoff,
                            rows_deleted=deleted,
                        )
                    )
                    _audit_tasks.add(_t)
                    _t.add_done_callback(_audit_tasks.discard)
                    _log.info(
                        "retention_sweep: purged %d usage_records rows older than %s",
                        deleted,
                        cutoff.date(),
                    )

            # ── alert_events ───────────────────────────────────────────────
            alert_days = self._settings.retention_alert_events_days
            if alert_days > 0:
                cutoff = _naive_utc_cutoff(alert_days)
                deleted = await self._delete_batched(
                    "alert_events", _DELETE_ALERT_BATCH, cutoff, batch_size
                )
                results["alert_events"] = deleted
                if deleted > 0:
                    _t = asyncio.create_task(
                        self._emit_purge_audit(
                            table="alert_events",
                            cutoff=cutoff,
                            rows_deleted=deleted,
                        )
                    )
                    _audit_tasks.add(_t)
                    _t.add_done_callback(_audit_tasks.discard)
                    _log.info(
                        "retention_sweep: purged %d alert_events rows older than %s",
                        deleted,
                        cutoff.date(),
                    )

            # ── audit_events ───────────────────────────────────────────────
            effective_audit = self.effective_audit_window
            if effective_audit > 0:
                cutoff = _naive_utc_cutoff(effective_audit)
                deleted = await self._delete_audit_batched(cutoff, batch_size)
                results["audit_events"] = deleted
                if deleted > 0:
                    _t = asyncio.create_task(
                        self._emit_purge_audit(
                            table="audit_events",
                            cutoff=cutoff,
                            rows_deleted=deleted,
                        )
                    )
                    _audit_tasks.add(_t)
                    _t.add_done_callback(_audit_tasks.discard)
                    _log.info(
                        "retention_sweep: purged %d audit_events rows older than %s (floor=%d)",
                        deleted,
                        cutoff.date(),
                        self._settings.retention_audit_floor_days,
                    )

            # ── request_logs (payload-capture-store) ──────────────────────
            # getattr (not direct attribute access): request_logs_days is a NEW
            # additive field — a pre-existing settings-like object that predates this
            # task (e.g. a minimal test fake constructed against the OLD _Settings
            # shape) safely defaults to 0 (skip) rather than raising, mirroring this
            # codebase's own convention for backward-compatible additive fields
            # (e.g. getattr(row, "cache_enabled", False) throughout keys/repository.py).
            request_logs_days = getattr(self._settings, "retention_request_logs_days", 0)
            if request_logs_days > 0:
                cutoff = _naive_utc_cutoff(request_logs_days)
                deleted = await self._delete_batched(
                    "request_logs", _DELETE_REQUEST_LOGS_BATCH, cutoff, batch_size
                )
                results["request_logs"] = deleted
                if deleted > 0:
                    _t = asyncio.create_task(
                        self._emit_purge_audit(
                            table="request_logs",
                            cutoff=cutoff,
                            rows_deleted=deleted,
                        )
                    )
                    _audit_tasks.add(_t)
                    _t.add_done_callback(_audit_tasks.discard)
                    _log.info(
                        "retention_sweep: purged %d request_logs rows older than %s",
                        deleted,
                        cutoff.date(),
                    )

        except Exception as exc:
            _log.warning(
                "retention_sweep: sweep_once failed (swallowed): %s",
                exc,
                exc_info=exc,
            )

        return results

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: sweep_once() every interval_seconds.

        Swallows all non-CancelledError exceptions; propagates CancelledError for
        clean task cancellation from the lifespan shutdown. _sleep is injectable
        for deterministic testing.
        """
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise  # propagate — shutdown is cancelling us
            except Exception as exc:
                # Defence in depth — sweep_once already swallows, but guard the loop
                _log.warning(
                    "retention_sweep: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await _sleep(interval_seconds)
