"""ReportScheduleGenerator — monthly, unattended, server-side Art. 12 bundle generation
(compliance-report-center TASK.md §3 M15-M23 — FROZEN @ v1).

CONTRACT:
  - generate_due_schedules() -> dict[str, int]: SELECT tenant_report_schedules WHERE
    enabled=true AND next_run_at <= now(); per tenant, IN ISOLATION (own try/except,
    mirrors RetentionSweeper's per-tenant ZDR-pass iteration — never a batched
    cross-tenant query, never a shared buffer, so one tenant's bundle is generated
    from that tenant's own rows only, never leaking into another's):
      1. is_zdr(tenant_id) FIRST (M17) — true -> SKIP entirely: no bundle assembled,
         no object written, no compliance_report_runs row inserted;
         last_run_status='skipped_zdr', last_run_at/next_run_at still advance
         (self-healing — the very next due tick after ZDR is disabled generates
         normally, no manual re-trigger).
      2. else compute the previous COMPLETED calendar month (mirrors
         InvoiceGenerator._month_start/_next_month/_prev_month exactly) and assemble
         the bundle IN-PROCESS from the SAME 3 repositories the frozen
         GET /admin/compliance/art12-bundle route reads (AuditRepository,
         LogsRepository, UsageRepository) — NEVER an HTTP self-call.
      3. ObjectStore.put() BEFORE the DB insert (mirrors artifacts' create() ordering:
         object first, then row — a failed put never leaves a dangling row). An
         ObjectStoreUnavailableError (or an unconfigured store) is a FAIL-OPEN,
         PER-TENANT skip: logged and swallowed, the schedule row is left COMPLETELY
         UNCHANGED (last_run_status is NOT set to 'success', next_run_at does NOT
         advance) so the very next tick retries the SAME due period (R13,
         self-healing — never a silent partial write).
      4. INSERT compliance_report_runs, ON CONFLICT (tenant_id, period_start) DO
         NOTHING (M16, mirrors InvoiceGenerator's own idempotent-insert idiom
         verbatim) — a restart-during-tick, an overlapping tick, or a re-run never
         double-inserts (R14).
      5. UPDATE tenant_report_schedules SET last_run_at/last_run_status='success'/
         next_run_at.
      6. fire-and-forget SYSTEM-scoped record_audit (M20) — tenant_id=None,
         actor=None, the real tenant named in metadata (mirrors
         RetentionSweeper._emit_zdr_purge_audit's exact resolution of the
         audit_missing_actor invariant — a background job has no human actor).
    Each tenant's whole block is wrapped in a bounded asyncio.timeout AND a broad
    try/except (mirrors RetentionSweeper.sweep_once's per-pass isolation) — one
    tenant's failure (timeout, DB error, anything) NEVER blocks another tenant's
    tick or crashes the loop; a failure leaves the schedule row UNCHANGED (same
    self-healing retry-next-tick behavior as an object-store outage).
  - run_forever(interval_seconds, *, _sleep): background loop; swallows all
    non-CancelledError exceptions; propagates CancelledError for clean shutdown.
    _sleep is injectable for deterministic tests (mirrors RetentionSweeper/
    InvoiceGenerator verbatim).
  - should_start_report_schedule_generator(settings) -> bool: interval > 0.

SAFETY (the load-bearing security rule, M17): the ZDR check happens BEFORE anything
else in the per-tenant block — before any repository read, before any bytes are
assembled, before any object-store call. A ZDR tenant's bundle is NEVER assembled,
NEVER touches the object store, NEVER gets a DB row — skip, never degrade-and-persist.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository
from gateway.compliance.infrastructure.orm import ComplianceReportRunRow
from gateway.core.errors import ProblemError
from gateway.core.ids import uuid7
from gateway.logs.infrastructure.logs_repository import LogsRepository
from gateway.objectstore.errors import ObjectStoreUnavailableError
from gateway.objectstore.port import ObjectStore
from gateway.tenants.application.entitlements import check_plan_feature
from gateway.tenants.application.retention_policy import is_zdr
from gateway.tenants.infrastructure.orm import TenantRow
from gateway.usage.infrastructure.usage_repository import UsageRepository

_log = logging.getLogger(__name__)

_PER_TENANT_TIMEOUT_SECONDS = 60.0
_PAGE_LIMIT = 5000
_FORMAT_VERSION = "1"

_PLAN_FEATURE_NOTE = (
    "tenant plan does not include logs_explorer; audit_events and usage_lineage are unaffected"
)

_SELECT_DUE_SCHEDULES = text(
    """
    SELECT tenant_id, day_of_month
      FROM tenant_report_schedules
     WHERE enabled = true
       AND next_run_at IS NOT NULL
       AND next_run_at <= now()
    """
)


class _Settings(Protocol):
    """Minimal protocol for settings consumed by ReportScheduleGenerator."""

    compliance_report_schedule_interval_seconds: int


def should_start_report_schedule_generator(settings: _Settings) -> bool:
    """Default-safe guard: start the generator loop only when interval > 0."""
    return settings.compliance_report_schedule_interval_seconds > 0


# ---------------------------------------------------------------------------
# Month/period arithmetic — LOCAL copies mirroring InvoiceGenerator's own
# _month_start/_next_month/_prev_month/_as_naive_utc verbatim (this codebase's
# convention: never a cross-bounded-context import of a `_`-private helper).
# ---------------------------------------------------------------------------


def _as_naive_utc(dt: datetime.datetime) -> datetime.datetime:
    if dt.tzinfo is not None:
        return dt.astimezone(datetime.UTC).replace(tzinfo=None)
    return dt


def _month_start(dt: datetime.datetime) -> datetime.datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month(dt: datetime.datetime) -> datetime.datetime:
    dt = _month_start(dt)
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1)
    return dt.replace(month=dt.month + 1)


def _prev_month(dt: datetime.datetime) -> datetime.datetime:
    dt = _month_start(dt)
    if dt.month == 1:
        return dt.replace(year=dt.year - 1, month=12)
    return dt.replace(month=dt.month - 1)


def previous_completed_month(now: datetime.datetime) -> tuple[datetime.datetime, datetime.datetime]:
    """(period_start, period_end) for the calendar month immediately before `now`'s
    month — mirrors InvoiceGenerator's own month-close computation exactly."""
    now = _as_naive_utc(now)
    period_end = _month_start(now)
    period_start = _prev_month(period_end)
    return period_start, period_end


def compute_next_run_at(day_of_month: int, *, now: datetime.datetime) -> datetime.datetime:
    """The next future UTC-midnight timestamp on `day_of_month` strictly after `now`
    (day_of_month is CHECK-constrained 1-28, so `.replace(day=...)` never raises for
    any month — no Feb-29 edge case to handle)."""
    now = _as_naive_utc(now)
    candidate = now.replace(day=day_of_month, hour=0, minute=0, second=0, microsecond=0)
    if candidate <= now:
        candidate = _next_month(candidate).replace(day=day_of_month)
    return candidate


# ---------------------------------------------------------------------------
# In-process bundle assembly — reuses the SAME 3 repositories the frozen
# GET /admin/compliance/art12-bundle route reads, walking every page to
# completion (never an HTTP self-call).
# ---------------------------------------------------------------------------


async def _fetch_all(
    fetch_page: Callable[[tuple[Any, Any] | None, int], Awaitable[list[Any]]],
    *,
    page_limit: int = _PAGE_LIMIT,
) -> list[Any]:
    items: list[Any] = []
    cursor: tuple[Any, Any] | None = None
    while True:
        page = await fetch_page(cursor, page_limit + 1)
        has_more = len(page) > page_limit
        page = page[:page_limit]
        items.extend(page)
        if not has_more or not page:
            break
        last = page[-1]
        cursor = (last.created_at, last.id)
    return items


async def _is_logs_explorer_entitled(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Boolean wrapper over the shared check_plan_feature gate — mirrors
    compliance/api/router.py's own `_is_logs_explorer_entitled` (a local copy per
    this codebase's `_`-private-helper convention, not a cross-module import)."""
    try:
        await check_plan_feature(session, tenant_id, "logs_explorer")
    except ProblemError:
        return False
    return True


def _audit_item(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": str(event.id),
        "actor_email": event.actor_email,
        "action": event.action,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "result": event.result,
        "metadata": event.metadata,
        "created_at": (
            event.created_at.isoformat()
            if hasattr(event.created_at, "isoformat")
            else str(event.created_at)
        ),
    }


def _log_item(log: Any) -> dict[str, Any]:
    return {
        "id": str(log.id),
        "key_id": str(log.key_id),
        "team_id": str(log.team_id) if log.team_id is not None else None,
        "model_id": log.model_id,
        "status_code": log.status_code,
        "stream": log.stream,
        "cached": log.cached,
        "scrub_status": log.scrub_status,
        "truncated": log.truncated,
        "cost_usd": log.cost_usd,
        "created_at": log.created_at.isoformat(),
        "latency_ms": log.latency_ms,
        "prompt_tokens": log.prompt_tokens,
        "completion_tokens": log.completion_tokens,
        "total_tokens": log.total_tokens,
    }


def _usage_item(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "key_id": str(row.key_id),
        "model_id": row.model_id,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "cost_usd": str(row.cost_usd),
        "cost_basis": row.cost_basis,
        "usage_source": row.usage_source,
        "tier_served": row.tier_served,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
    }


class ReportScheduleGenerator:
    """Generates + persists a due tenant's monthly Art. 12 bundle (M15-M23)."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        object_store: ObjectStore | None,
        settings: _Settings,
        per_tenant_timeout_seconds: float = _PER_TENANT_TIMEOUT_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._object_store = object_store
        self._settings = settings
        self._per_tenant_timeout_seconds = per_tenant_timeout_seconds

    async def _assemble_bundle(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        period_start: datetime.datetime,
        period_end: datetime.datetime,
    ) -> dict[str, Any]:
        row = (
            await session.execute(select(TenantRow).where(TenantRow.id == tenant_id))
        ).scalar_one_or_none()
        entitled = await _is_logs_explorer_entitled(session, tenant_id)

        audit_repo = AuditRepository(session)
        logs_repo = LogsRepository(session)
        usage_repo = UsageRepository(session)

        async def _fetch_audit(cursor: Any, limit: int) -> list[Any]:
            return await audit_repo.list_for_tenant_keyset(
                tenant_id, limit=limit, cursor=cursor, since=period_start, until=period_end
            )

        async def _fetch_usage(cursor: Any, limit: int) -> list[Any]:
            return await usage_repo.list_for_tenant_keyset(
                tenant_id, limit=limit, cursor=cursor, since=period_start, until=period_end
            )

        audit_events = await _fetch_all(_fetch_audit)
        usage_lineage = await _fetch_all(_fetch_usage)

        log_note: str | None = None
        if not entitled:
            log_note = _PLAN_FEATURE_NOTE
            request_log_metadata: list[Any] = []
        else:

            async def _fetch_logs(cursor: Any, limit: int) -> list[Any]:
                return await logs_repo.list_for_tenant_keyset(
                    tenant_id, limit=limit, cursor=cursor, since=period_start, until=period_end
                )

            request_log_metadata = await _fetch_all(_fetch_logs)

        generated_at = datetime.datetime.now(datetime.UTC)
        cover = {
            "bundle_id": str(uuid7()),
            "generated_at": generated_at.isoformat(),
            "tenant_id": str(tenant_id),
            "tenant_name": row.name if row is not None else "",
            "period": {"since": period_start.isoformat(), "until": period_end.isoformat()},
            "residency_pin": row.residency_region if row is not None else None,
            "zdr_state": {"enabled": False, "enabled_at": None},
            "retention_window_days": row.retention_window_days if row is not None else None,
            "guardrail_configs_snapshot": (
                dict(row.guardrail_configs) if row is not None and row.guardrail_configs else {}
            ),
            "default_tier": row.default_tier if row is not None else "standard",
            "format_version": _FORMAT_VERSION,
        }
        return {
            "cover": cover,
            "sections": {
                "audit_events": {
                    "items": [_audit_item(e) for e in audit_events],
                    "next_cursor": None,
                    "has_more": False,
                    "note": None,
                },
                "request_log_metadata": {
                    "items": [_log_item(rl) for rl in request_log_metadata],
                    "next_cursor": None,
                    "has_more": False,
                    "note": log_note,
                },
                "usage_lineage": {
                    "items": [_usage_item(u) for u in usage_lineage],
                    "next_cursor": None,
                    "has_more": False,
                    "note": None,
                },
            },
        }

    async def _emit_generated_audit(
        self,
        *,
        tenant_id: uuid.UUID,
        report_id: uuid.UUID,
        period_start: datetime.datetime,
        period_end: datetime.datetime,
    ) -> None:
        try:
            await record_audit(
                self._session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action="compliance.report_generated",
                    target_type="compliance_report_run",
                    target_id=str(report_id),
                    result="success",
                    metadata={
                        "tenant_id": str(tenant_id),
                        "period_start": period_start.isoformat(),
                        "period_end": period_end.isoformat(),
                    },
                    created_at=datetime.datetime.now(datetime.UTC),
                ),
            )
        except Exception as exc:
            _log.warning(
                "report_schedule_generator: generated-audit emit failed for tenant=%s"
                " (swallowed): %s",
                tenant_id,
                exc,
                exc_info=exc,
            )

    async def _emit_skipped_audit(self, *, tenant_id: uuid.UUID) -> None:
        try:
            await record_audit(
                self._session_factory,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=None,
                    actor_user_id=None,
                    actor_email=None,
                    action="compliance.report_generation_skipped",
                    target_type="tenant_report_schedule",
                    target_id=str(tenant_id),
                    result="success",
                    metadata={"tenant_id": str(tenant_id), "reason": "zdr_enabled"},
                    created_at=datetime.datetime.now(datetime.UTC),
                ),
            )
        except Exception as exc:
            _log.warning(
                "report_schedule_generator: skipped-audit emit failed for tenant=%s"
                " (swallowed): %s",
                tenant_id,
                exc,
                exc_info=exc,
            )

    async def generate_for_tenant(self, tenant_id: uuid.UUID, day_of_month: int) -> str:
        """Returns one of 'success' | 'skipped_zdr' | 'deferred' (object-store/failure —
        row left unchanged, never advances) — the caller counts each outcome."""
        now = datetime.datetime.now(datetime.UTC)

        # M17 — the load-bearing fail-closed gate: checked BEFORE anything else.
        async with self._session_factory() as zdr_session:
            tenant_is_zdr = await is_zdr(zdr_session, tenant_id)

        if tenant_is_zdr:
            next_run = compute_next_run_at(day_of_month, now=now)
            async with self._session_factory() as session, session.begin():
                await session.execute(
                    text(
                        "UPDATE tenant_report_schedules"
                        " SET last_run_at = :now, last_run_status = 'skipped_zdr',"
                        "     next_run_at = :next_run"
                        " WHERE tenant_id = :tid"
                    ),
                    {"now": _as_naive_utc(now), "next_run": next_run, "tid": tenant_id},
                )
            asyncio.ensure_future(self._emit_skipped_audit(tenant_id=tenant_id))  # noqa: RUF006
            return "skipped_zdr"

        period_start, period_end = previous_completed_month(now)

        async with self._session_factory() as session:
            bundle = await self._assemble_bundle(
                session, tenant_id=tenant_id, period_start=period_start, period_end=period_end
            )

        payload = json.dumps(bundle, default=str).encode("utf-8")
        report_id = uuid7()
        object_key = f"compliance-reports/{tenant_id}/{report_id}.json"

        if self._object_store is None:
            _log.warning(
                "report_schedule_generator: no object store configured for tenant=%s"
                " (deferred to next tick)",
                tenant_id,
            )
            return "deferred"
        try:
            await self._object_store.put(object_key, payload, "application/json")
        except ObjectStoreUnavailableError as exc:
            _log.warning(
                "report_schedule_generator: object store put failed for tenant=%s"
                " (deferred to next tick): %s",
                tenant_id,
                exc,
            )
            return "deferred"

        async with self._session_factory() as session, session.begin():
            insert_stmt = (
                pg_insert(ComplianceReportRunRow)
                .values(
                    id=report_id,
                    tenant_id=tenant_id,
                    period_start=period_start,
                    period_end=period_end,
                    generated_at=_as_naive_utc(now),
                    object_key=object_key,
                    size_bytes=len(payload),
                    format_version=_FORMAT_VERSION,
                    source="scheduled",
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "period_start"])
                .returning(ComplianceReportRunRow.id)
            )
            result = await session.execute(insert_stmt)
            inserted_id = result.scalar_one_or_none()

            next_run = compute_next_run_at(day_of_month, now=now)
            await session.execute(
                text(
                    "UPDATE tenant_report_schedules"
                    " SET last_run_at = :now, last_run_status = 'success',"
                    "     next_run_at = :next_run"
                    " WHERE tenant_id = :tid"
                ),
                {"now": _as_naive_utc(now), "next_run": next_run, "tid": tenant_id},
            )

        effective_report_id = inserted_id if inserted_id is not None else report_id
        asyncio.ensure_future(  # noqa: RUF006
            self._emit_generated_audit(
                tenant_id=tenant_id,
                report_id=effective_report_id,
                period_start=period_start,
                period_end=period_end,
            )
        )
        return "success"

    async def generate_due_schedules(self) -> dict[str, int]:
        """One tick: generate every due, non-ZDR tenant's bundle; skip every due ZDR
        tenant honestly. NEVER raises — any per-tenant failure is logged and counted
        (fail-open isolation, mirrors RetentionSweeper.sweep_once / InvoiceGenerator
        .sweep_once)."""
        async with self._session_factory() as session:
            due_rows = (await session.execute(_SELECT_DUE_SCHEDULES)).all()

        counts = {"success": 0, "skipped_zdr": 0, "deferred": 0, "failed": 0}
        for tenant_id, day_of_month in due_rows:
            try:
                async with asyncio.timeout(self._per_tenant_timeout_seconds):
                    outcome = await self.generate_for_tenant(tenant_id, day_of_month)
                counts[outcome] = counts.get(outcome, 0) + 1
            except Exception as exc:
                counts["failed"] += 1
                _log.warning(
                    "report_schedule_generator: generate_for_tenant failed for tenant=%s"
                    " (swallowed, schedule row left unchanged, retried next tick): %s",
                    tenant_id,
                    exc,
                    exc_info=exc,
                )
        return counts

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: generate_due_schedules() every interval_seconds. Swallows
        all non-CancelledError exceptions; propagates CancelledError for clean task
        cancellation from the lifespan shutdown (mirrors RetentionSweeper/
        InvoiceGenerator.run_forever verbatim)."""
        while True:
            try:
                await self.generate_due_schedules()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "report_schedule_generator: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await _sleep(interval_seconds)
