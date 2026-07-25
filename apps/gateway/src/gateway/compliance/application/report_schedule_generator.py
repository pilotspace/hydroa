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
      3. is_zdr(tenant_id) RECHECKED on a fresh session (cheap early-exit only, NOT
         load-bearing by itself — see step 4): bundle assembly is a couple of DB
         round-trips: a tenant that flipped during that window skips here, before
         ever touching the object store.
      4. audit-remediation v2 (closes CRIT#2-adjacent HOLE 1 + HOLE 2 found in
         adversarial re-verification of the original M17 v2 CR — see TASK.md
         remediation notes): the ZDR decision, the compliance_report_runs INSERT,
         and the ObjectStore.put() are made ATOMIC in ONE DB transaction, gated by
         `SELECT tenants.zdr_enabled ... FOR UPDATE` (_is_zdr_locked) taken at the
         START of that transaction:
           - ZDR true  -> skip immediately. NO ObjectStore.put() is EVER attempted
             for this tenant — there is structurally nothing to orphan (this is
             what actually closes HOLE 1: the prior design's failure mode was a
             best-effort cleanup-DELETE of an ALREADY-WRITTEN object failing and
             being swallowed, silently orphaning it forever since every reclaim path
             in RetentionSweeper is row-driven and no row was ever written for that
             object_key; the new design never reaches PUT for a ZDR tenant in the
             first place, so that failure mode cannot occur).
           - ZDR false -> INSERT (uncommitted, ON CONFLICT (tenant_id, period_start)
             DO NOTHING, M16/R14) -> PUT the object (ONLY when the INSERT actually
             inserted a fresh row — a conflicting/duplicate tick has nothing to
             reference a new object with, so the PUT is skipped rather than writing
             an unreferenced object) -> UPDATE tenant_report_schedules SET
             last_run_status='success' -> COMMIT.
             A PUT failure (ObjectStoreUnavailableError) propagates out of the open
             transaction, rolling EVERYTHING in it back — no row, no object — and is
             caught one level up: last_run_status is left COMPLETELY UNCHANGED (R13,
             self-healing retry-next-tick, unchanged from the pre-remediation
             contract).
         The FOR UPDATE row lock is held for the transaction's whole lifetime, so a
         CONCURRENT PUT /admin/retention-policy {zdr_enabled: true} for the SAME
         tenant blocks on ordinary Postgres row-lock contention until this
         transaction resolves — it can never land strictly BETWEEN the ZDR decision
         and the INSERT (this is what closes HOLE 2: the prior design's recheck and
         INSERT ran in SEPARATE, non-atomic transactions/sessions with no lock held
         across the gap between them).
      5. fire-and-forget SYSTEM-scoped record_audit (M20) — tenant_id=None,
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

SAFETY (the load-bearing security rule, M17 + audit-remediation v2): the ZDR check
happens BEFORE anything else in the per-tenant block — before any repository read,
before any bytes are assembled. The FINAL, load-bearing ZDR check (step 4 above) is
made ATOMIC with the object-store PUT and the DB INSERT via a single transaction and
a `SELECT ... FOR UPDATE` row lock — a ZDR tenant's bundle is NEVER assembled into a
persisted object, NEVER gets a DB row — skip, never degrade-and-persist, and never a
window where the decision and the write can be observed to disagree.
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
from gateway.tenants.application.retention_policy import is_zdr, is_zdr_locked
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


#: LOCK-TAKING read of tenants.zdr_enabled, scoped to the CALLER's own already-open
#: transaction (the caller must be inside `async with session.begin()` — this does not
#: open one itself). Held until that transaction commits or rolls back, so a concurrent
#: zdr_enabled flip for the SAME tenant blocks on ordinary Postgres row-lock contention
#: and can never land strictly between this read and the compliance_report_runs INSERT
#: it gates (audit-remediation v2 — module docstring step 4).
#:
#: zdr-ingest-lock-heal CR v2 (2026-07-25, M3): this was the THIRD hand-copied instance
#: of the identical `SELECT zdr_enabled ... FOR UPDATE` statement. It is now an alias for
#: the SHARED primitive in tenants/application/retention_policy.py. The duplication was
#: not cosmetic — it drifted: the vector-store ingest worker needed the same lock, got a
#: hand-written PLAIN re-read instead, and shipped the exact TOCTOU each of these local
#: copies had independently documented. One definition site, reached by every path that
#: persists payload after an await.
#:
#: NOTE the shared `is_zdr` / `raise_if_zdr` remain the plain NON-locking reads used by
#: the six gated write choke points (ArtifactRepository.create, ConversationRepository.
#: create/append_message, MemoryRepository.create, BatchJobRepository.create,
#: VideoJobRepository.create, files) — adding FOR UPDATE there would take a row lock on
#: every one of those unrelated writes, serializing them per-tenant for no reason.
_is_zdr_locked = is_zdr_locked


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

    async def _record_zdr_skip(
        self, tenant_id: uuid.UUID, day_of_month: int, now: datetime.datetime
    ) -> str:
        """Persist a fail-closed ZDR skip for one tenant: advance the schedule row to
        last_run_status='skipped_zdr' (self-heals next tick), emit the skipped audit, and
        return 'skipped_zdr'. NOTHING of the tenant's data is written. Shared by the M17
        up-front gate AND the pre-persistence re-check so both close identically."""
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

    async def generate_for_tenant(self, tenant_id: uuid.UUID, day_of_month: int) -> str:
        """Returns one of 'success' | 'skipped_zdr' | 'deferred' (object-store/failure —
        row left unchanged, never advances) — the caller counts each outcome."""
        now = datetime.datetime.now(datetime.UTC)

        # M17 — the load-bearing fail-closed gate: checked BEFORE anything else.
        async with self._session_factory() as zdr_session:
            tenant_is_zdr = await is_zdr(zdr_session, tenant_id)

        if tenant_is_zdr:
            return await self._record_zdr_skip(tenant_id, day_of_month, now)

        period_start, period_end = previous_completed_month(now)

        async with self._session_factory() as session:
            bundle = await self._assemble_bundle(
                session, tenant_id=tenant_id, period_start=period_start, period_end=period_end
            )

        payload = json.dumps(bundle, default=str).encode("utf-8")
        report_id = uuid7()
        object_key = f"compliance-reports/{tenant_id}/{report_id}.json"

        # Cheap early-exit (step 3, NOT load-bearing by itself — the atomic block
        # below is what actually guarantees correctness): the up-front check and this
        # point are separated by bundle assembly (a couple of DB round-trips). A
        # tenant that flipped zdr_enabled=true in that window skips here, before ever
        # touching the object store — an optimization, not a security boundary.
        async with self._session_factory() as recheck_session:
            if await is_zdr(recheck_session, tenant_id):
                return await self._record_zdr_skip(tenant_id, day_of_month, now)

        if self._object_store is None:
            _log.warning(
                "report_schedule_generator: no object store configured for tenant=%s"
                " (deferred to next tick)",
                tenant_id,
            )
            return "deferred"

        # audit-remediation v2 (module docstring step 4 — closes HOLE 1 + HOLE 2): the
        # ZDR decision (_is_zdr_locked, SELECT ... FOR UPDATE), the compliance_report_
        # runs INSERT, and the ObjectStore.put() are ATOMIC in one transaction.
        #   - ZDR true  -> skip_zdr=True, NO put() is ever attempted — nothing to
        #     orphan, by construction (closes HOLE 1: the old code's failure mode was
        #     a cleanup-DELETE of an ALREADY-WRITTEN object failing and being
        #     swallowed with no DB row ever pointing at it for the sweep to reclaim).
        #   - ZDR false -> INSERT (ON CONFLICT DO NOTHING) -> put() ONLY when the
        #     INSERT actually inserted a fresh row (a conflicting/duplicate tick has
        #     no fresh row to reference a new object with) -> advance the schedule ->
        #     implicit COMMIT at the end of this block.
        # A put() failure (ObjectStoreUnavailableError) propagates out of the
        # `session.begin()` block, rolling the WHOLE transaction back (INSERT
        # included) — "no row, no object" — and is caught below: 'deferred', schedule
        # row left completely unchanged (R13, retried next tick).
        # The FOR UPDATE lock is held for the transaction's lifetime, so a concurrent
        # zdr_enabled flip for this tenant blocks on ordinary Postgres row-lock
        # contention until this transaction resolves — it cannot land strictly
        # between the decision and the INSERT (closes HOLE 2: the old recheck and
        # INSERT ran in separate, non-atomic sessions with no lock held across them).
        skip_zdr = False
        inserted_id: uuid.UUID | None = None
        try:
            async with self._session_factory() as session, session.begin():
                skip_zdr = await _is_zdr_locked(session, tenant_id)
                if not skip_zdr:
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

                    if inserted_id is not None:
                        # Inside the still-open, uncommitted transaction: a raise
                        # here propagates out of this `async with` block and rolls
                        # the INSERT back too — never "object without row".
                        await self._object_store.put(object_key, payload, "application/json")

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
        except ObjectStoreUnavailableError as exc:
            _log.warning(
                "report_schedule_generator: object store put failed for tenant=%s"
                " (transaction rolled back — no row, no object; deferred to next"
                " tick): %s",
                tenant_id,
                exc,
            )
            return "deferred"

        if skip_zdr:
            return await self._record_zdr_skip(tenant_id, day_of_month, now)

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
