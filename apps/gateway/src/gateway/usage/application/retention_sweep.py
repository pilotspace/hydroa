"""RetentionSweeper — periodic bounded DELETE of aged time-series rows.

tenant-retention-zdr TASK.md §3 (FROZEN @ v1) extension — ADDITIVE to the frozen v1
contract below, never a rewrite of it. The 3 original unconditional DELETEs (usage_records
/ alert_events / audit_events) stay byte-identical for every tenant with no override —
they are a separate, untouched code path. Three NEW passes are layered alongside them in
sweep_once():
  1. A per-tenant window-cutoff pass over the 5 newly-swept payload tables (artifacts,
     conversations[+conversation_messages via CASCADE], memories, batch_job_items,
     video_generation_jobs), PLUS compliance_report_runs (compliance-report-center
     §3 M21) — EVERY tenant is touched: the cutoff is
     COALESCE(tenants.retention_window_days, retention_tenant_window_ceiling_days), i.e.
     a tenant with no override (column IS NULL) is swept at the operator ceiling, byte-
     identical to what tenants/application/retention_policy.py effective_window_days()
     already REPORTS for that tenant via GET /admin/retention-policy (CRIT fix: before
     this, the query itself required retention_window_days IS NOT NULL, so a default-
     posture tenant's data was NEVER purged even though the API claimed a finite
     effective window — see TASK.md remediation notes).
  2. A per-tenant SHORTENING pass over usage_records/alert_events — only tenants with an
     override AND only down to min(tenant_window, operator_default); the operator's own
     per-table default remains an outer bound the original unconditional pass already
     enforces for everyone, so a tenant can only end up with EARLIER deletion, never later.
  3. An unconditional (no-cutoff) per-tenant ZDR purge pass across the 5 payload tables
     PLUS request_logs (payload-capture-store — a ZDR tenant's captured raw PII payloads
     must never survive independently of the 5 original tables) for every tenant with
     zdr_enabled=true, PLUS ObjectStore.delete() for every s3-backed artifact and a
     bounded Redis SCAN+DEL over that tenant's resp-cache:/vec-cache: namespaces.
     Self-healing: re-runs every tick for as long as zdr_enabled=true, so a mid-purge
     crash is recovered automatically at the next tick (no job-tracking table).
  audit_events is NEVER touched by any of the three new passes (R4) — effective_audit_window
  logic (below) is completely unchanged.

CONTRACT (FROZEN @ data-retention-controls v1 — TASK.md §3):
  - sweep_once() -> dict[str, int]: for each enabled table, DELETE rows older than
    the per-table window in bounded batches; returns {table: rows_deleted}.
  - run_forever(interval_seconds, *, _sleep): background loop; swallows all errors;
    cancellable via asyncio.CancelledError propagation. _sleep is injectable for tests.
  - should_start_retention_sweep(settings) -> bool: interval>0 AND at least one
    operator-level window knob>0 (settings-only; blind to per-tenant DB state).
  - should_start_retention_sweep_with_zdr(settings, session_factory=None) -> bool:
    superset of the above — ALSO True when at least one tenant has zdr_enabled=true,
    even if every operator-level window knob is 0 (remediation for the MED finding:
    a ZDR-only deployment must still get the self-healing ZDR purge pass to run).
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

from gateway.objectstore.errors import ObjectStoreUnavailableError

# zdr-retention-inventory-extension TASK.md §3 M2 — THE inventory. Imported (not
# re-declared) so the sweeper and every guard read ONE object: a second, equal-but-
# separate list is precisely the drift that let vector_store_chunks, eval_cases,
# eval_case_results, finetune_jobs and finetune_job_events go unpurged (R:SECOND_INVENTORY).
from gateway.tenants.application.retention_policy import ZDR_PURGE_TABLES

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
    # tenant-retention-zdr TASK.md §3 — the operator-wide ceiling a tenant's own
    # retention_window_days may never exceed; ALSO the fallback cutoff for the 5 (+
    # compliance_report_runs) newly-swept payload tables when a tenant's own column is
    # NULL (default posture) — see effective_window_days in tenants/application/
    # retention_policy.py, which this sweep must stay byte-identical with.
    retention_tenant_window_ceiling_days: int


def should_start_retention_sweep(settings: _Settings) -> bool:
    """The default-ON guard: start the sweeper when interval>0 AND at least one
    operator-level window knob is positive.

    A zero (or negative) interval disables the sweeper entirely. When the interval is
    positive, at least one per-table window must also be positive for the sweep to be
    meaningful. If all windows are 0 (all tables opted out), there is nothing to sweep —
    from a PURE SETTINGS point of view. This function cannot see per-tenant DB state
    (e.g. a tenant with zdr_enabled=true while every operator knob is 0); see
    should_start_retention_sweep_with_zdr for the DB-aware variant that also accounts
    for that case.

    getattr(..., 0) on the two newer knobs (retention_request_logs_days,
    retention_tenant_window_ceiling_days) keeps this backward-compatible with any
    pre-existing minimal settings-like object that predates those fields.
    """
    if settings.retention_check_interval_seconds <= 0:
        return False
    return (
        settings.retention_usage_records_days > 0
        or settings.retention_alert_events_days > 0
        or settings.retention_audit_events_days > 0
        or getattr(settings, "retention_request_logs_days", 0) > 0
        or getattr(settings, "retention_tenant_window_ceiling_days", 0) > 0
    )


async def should_start_retention_sweep_with_zdr(
    settings: _Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> bool:
    """DB-aware superset of should_start_retention_sweep.

    should_start_retention_sweep is a pure-settings guard: it cannot see that some
    tenant currently has zdr_enabled=true, so a deployment with every operator-level
    window knob left at 0 (retention entirely opted-out at the operator level) would
    never start the sweeper at all — even though a ZDR tenant's unconditional purge
    pass (_sweep_zdr_purge_pass) is supposed to self-heal every tick regardless of any
    window knob. This wraps the settings-only gate with one extra check: also start
    when at least one tenant has zdr_enabled=true.

    session_factory is optional — None honest-degrades to the settings-only answer
    (never raises; a caller that cannot supply a session_factory still gets the
    pre-existing behavior). A DB error during the extra check is logged and swallowed,
    falling back to the settings-only answer (fail-open on STARTING an already fail-
    open, idempotent background sweep — never fail-closed on a broken DB check here,
    since should_start_retention_sweep(settings) already covers the primary case).
    """
    if settings.retention_check_interval_seconds <= 0:
        return False
    if should_start_retention_sweep(settings):
        return True
    if session_factory is None:
        return False
    try:
        async with session_factory() as session:
            row = (
                await session.execute(
                    text("SELECT 1 FROM tenants WHERE zdr_enabled = true LIMIT 1")
                )
            ).first()
        return row is not None
    except Exception as exc:
        _log.warning(
            "retention_sweep: should_start_retention_sweep_with_zdr DB check failed"
            " (swallowed, falling back to settings-only answer): %s",
            exc,
            exc_info=exc,
        )
        return False


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


# ---------------------------------------------------------------------------
# tenant-retention-zdr TASK.md §3 — ADDITIVE SQL (new passes only; the three
# templates above are never rewritten in place — see module docstring).
# ---------------------------------------------------------------------------

# Pass 1 (per-tenant window-cutoff, 4 of the 5 new payload tables — artifacts is
# special-cased below because an s3-backed row needs an ObjectStore.delete() call
# before its DB row is safe to drop). EVERY tenant is touched: the cutoff is
# COALESCE(tn.retention_window_days, :operator_ceiling_days) — a tenant with no
# override (column IS NULL) falls back to the operator-wide ceiling, matching
# tenants/application/retention_policy.py effective_window_days() exactly (CRIT fix:
# the old `WHERE tn.retention_window_days IS NOT NULL` guard meant a default-posture
# tenant was NEVER purged even though the API reported a finite effective window).

_DELETE_CONVERSATIONS_WINDOW = text(
    """
    DELETE FROM conversations
     WHERE id IN (
           SELECT c.id FROM conversations c
           JOIN tenants tn ON tn.id = c.tenant_id
            WHERE c.updated_at < now() - (
                  COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

_DELETE_MEMORIES_WINDOW = text(
    """
    DELETE FROM memories
     WHERE id IN (
           SELECT m.id FROM memories m
           JOIN tenants tn ON tn.id = m.tenant_id
            WHERE m.created_at < now() - (
                  COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

_DELETE_BATCH_ITEMS_WINDOW = text(
    """
    DELETE FROM batch_job_items
     WHERE id IN (
           SELECT i.id FROM batch_job_items i
           JOIN tenants tn ON tn.id = i.tenant_id
            WHERE i.created_at < now() - (
                  COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

_DELETE_VIDEO_JOBS_WINDOW = text(
    """
    DELETE FROM video_generation_jobs
     WHERE id IN (
           SELECT v.id FROM video_generation_jobs v
           JOIN tenants tn ON tn.id = v.tenant_id
            WHERE v.created_at < now() - (
                  COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

# Pass 1 — stored_responses (responses-state-store PLAN.md §3 M8; additive, same
# tenant-window-cutoff discipline as conversations above, keyed on created_at). A ZDR
# tenant's rows are additionally row-deleted unconditionally by the ZDR purge pass below
# (self-healing for the flip-ZDR-on-later case).

_DELETE_STORED_RESPONSES_WINDOW = text(
    """
    DELETE FROM stored_responses
     WHERE id IN (
           SELECT s.id FROM stored_responses s
           JOIN tenants tn ON tn.id = s.tenant_id
            WHERE s.created_at < now() - (
                  COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

# Pass 1 — artifacts (object-store-aware; SELECT candidates, delete objects, then delete rows).

_SELECT_ARTIFACTS_WINDOW = text(
    """
    SELECT a.id, a.object_key, a.storage_backend FROM artifacts a
    JOIN tenants tn ON tn.id = a.tenant_id
     WHERE a.created_at < now() - (
           COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
           )
     LIMIT :batch
    """
)

_DELETE_ARTIFACTS_BY_ID = text("DELETE FROM artifacts WHERE id = ANY(:ids)")

# Pass 1 — files (files-uploads-api PLAN.md §3; object-store-aware, mirrors artifacts:
# SELECT candidates, delete objects, then delete rows). files uses created_at for the
# age cutoff and the same COALESCE(tenant window, operator ceiling) discipline.

_SELECT_FILES_WINDOW = text(
    """
    SELECT f.id, f.object_key, f.storage_backend FROM files f
    JOIN tenants tn ON tn.id = f.tenant_id
     WHERE f.created_at < now() - (
           COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
           )
     LIMIT :batch
    """
)

_DELETE_FILES_BY_ID = text("DELETE FROM files WHERE id = ANY(:ids)")

_SELECT_FILES_ZDR_TENANT = text(
    "SELECT id, object_key, storage_backend FROM files WHERE tenant_id = :tid LIMIT :batch"
)

# Pass 2 (per-tenant SHORTENING, the 2 pre-existing swept tables). Cutoff is
# min(tenant_window, operator_default) — the operator's own per-table default (bound
# in Python, passed as :operator_default) remains an outer bound; this pass can only
# ever find rows the ORIGINAL unconditional pass above would not already have deleted
# when the tenant's window is >= the operator default (a pure no-op in that case).

_DELETE_USAGE_TENANT_SHORTEN = text(
    """
    DELETE FROM usage_records
     WHERE id IN (
           SELECT u.id FROM usage_records u
           JOIN tenants tn ON tn.id = u.tenant_id
            WHERE tn.retention_window_days IS NOT NULL
              AND u.created_at < now() - (
                    LEAST(tn.retention_window_days, :operator_default) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

_DELETE_ALERT_TENANT_SHORTEN = text(
    """
    DELETE FROM alert_events
     WHERE id IN (
           SELECT al.id FROM alert_events al
           JOIN tenants tn ON tn.id = al.tenant_id
            WHERE tn.retention_window_days IS NOT NULL
              AND al.created_at < now() - (
                    LEAST(tn.retention_window_days, :operator_default) * interval '1 day'
                  )
            LIMIT :batch
     )
    """
)

# alert_events has no tenant_id column of its own in the pre-existing schema (it is an
# operator-wide table) — see the guard in _sweep_tenant_shorten_pass below, which skips
# this template entirely (alert_events is never tenant-scoped, so a per-tenant shortening
# pass cannot apply to it; kept only for symmetry/documentation of what was considered).

# Pass 3 (unconditional per-tenant ZDR purge, no cutoff — every row for every
# zdr_enabled=true tenant). Implemented as a per-tenant loop (see the _ZDR_TENANT
# variants below _SELECT_ZDR_TENANT_IDS) rather than one bulk all-tenants DELETE, so
# each table's rows-deleted count is attributable to a single tenant for the
# tenant-scoped record_audit(action="data.purge") event TASK.md §3 M10 requires.

_SELECT_ZDR_TENANT_IDS = text("SELECT id FROM tenants WHERE zdr_enabled = true")

# Per-tenant variants of the ZDR purge queries — used by _sweep_zdr_purge_pass so
# each table's rows-deleted count is attributable to ONE tenant, enabling the tenant-
# scoped record_audit(action="data.purge") event TASK.md §3 M10 requires (the bulk
# all-tenants-at-once queries above cannot report a per-tenant rowcount).

_SELECT_ARTIFACTS_ZDR_TENANT = text(
    "SELECT id, object_key, storage_backend FROM artifacts WHERE tenant_id = :tid LIMIT :batch"
)
_DELETE_CONVERSATIONS_ZDR_TENANT = text(
    "DELETE FROM conversations WHERE id IN"
    " (SELECT id FROM conversations WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_MEMORIES_ZDR_TENANT = text(
    "DELETE FROM memories WHERE id IN (SELECT id FROM memories WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_BATCH_ITEMS_ZDR_TENANT = text(
    "DELETE FROM batch_job_items WHERE id IN"
    " (SELECT id FROM batch_job_items WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_VIDEO_JOBS_ZDR_TENANT = text(
    "DELETE FROM video_generation_jobs WHERE id IN"
    " (SELECT id FROM video_generation_jobs WHERE tenant_id = :tid LIMIT :batch)"
)
# stored_responses (responses-state-store PLAN.md §3 M8) — a ZDR tenant's stored
# response payloads must be row-DELETED unconditionally every tick (not scrubbed —
# no payload-NULL state exists), self-healing the flip-ZDR-on-later case.
_DELETE_STORED_RESPONSES_ZDR_TENANT = text(
    "DELETE FROM stored_responses WHERE id IN"
    " (SELECT id FROM stored_responses WHERE tenant_id = :tid LIMIT :batch)"
)

# request_logs (payload-capture-store) — a ZDR tenant's captured raw request/response
# PII payloads must be purged unconditionally alongside the 5 original payload tables,
# not merely subject to the separate operator-level retention_request_logs_days window
# (MED fix: the ZDR purge pass previously omitted this table entirely).
_DELETE_REQUEST_LOGS_ZDR_TENANT = text(
    "DELETE FROM request_logs WHERE id IN"
    " (SELECT id FROM request_logs WHERE tenant_id = :tid LIMIT :batch)"
)


# ---------------------------------------------------------------------------
# compliance-report-center TASK.md §3 M21 — ADDITIVE SQL (sweeps compliance_report_runs
# like artifacts; every row is s3-backed by construction, §3 CONTRACT — no
# storage_backend column/branch needed, unlike artifacts' inline-vs-s3 split).
# ---------------------------------------------------------------------------

_SELECT_COMPLIANCE_REPORTS_WINDOW = text(
    """
    SELECT c.id, c.object_key FROM compliance_report_runs c
    JOIN tenants tn ON tn.id = c.tenant_id
     WHERE c.generated_at < now() - (
           COALESCE(tn.retention_window_days, :operator_ceiling_days) * interval '1 day'
           )
     LIMIT :batch
    """
)

_DELETE_COMPLIANCE_REPORTS_BY_ID = text("DELETE FROM compliance_report_runs WHERE id = ANY(:ids)")

_SELECT_COMPLIANCE_REPORTS_ZDR_TENANT = text(
    "SELECT id, object_key FROM compliance_report_runs WHERE tenant_id = :tid LIMIT :batch"
)


# ---------------------------------------------------------------------------
# zdr-retention-inventory-extension TASK.md §3 — the five payload tables the pre-task
# hand-enumeration silently missed (deep-review P0 artifact 6816985f).
#
# Each carries its OWN explicit tenant_id-scoped DELETE. NONE of them is left to an FK
# ON DELETE CASCADE from its container (R:CASCADE_RELIANCE): the containers deliberately
# SURVIVE a ZDR purge (vector_stores / vector_store_files / eval_sets / eval_runs carry
# ids, names and status, not payload — A2), so a cascade would never fire for three of
# these, and for finetune_job_events — whose parent IS deleted wholesale (A3) — a cascade
# would still leave a re-parented or denormalized event row behind. Explicit, always.
# ---------------------------------------------------------------------------

_DELETE_VECTOR_STORE_CHUNKS_ZDR_TENANT = text(
    "DELETE FROM vector_store_chunks WHERE id IN"
    " (SELECT id FROM vector_store_chunks WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_EVAL_CASES_ZDR_TENANT = text(
    "DELETE FROM eval_cases WHERE id IN"
    " (SELECT id FROM eval_cases WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_EVAL_CASE_RESULTS_ZDR_TENANT = text(
    "DELETE FROM eval_case_results WHERE id IN"
    " (SELECT id FROM eval_case_results WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_FINETUNE_JOB_EVENTS_ZDR_TENANT = text(
    "DELETE FROM finetune_job_events WHERE id IN"
    " (SELECT id FROM finetune_job_events WHERE tenant_id = :tid LIMIT :batch)"
)
_DELETE_FINETUNE_JOBS_ZDR_TENANT = text(
    "DELETE FROM finetune_jobs WHERE id IN"
    " (SELECT id FROM finetune_jobs WHERE tenant_id = :tid LIMIT :batch)"
)


# ---------------------------------------------------------------------------
# The ZDR purge DISPATCH (A18: the inventory names WHICH tables, this names HOW).
#
# Two kinds of table, and conflating them is a data-loss defect in either direction:
#   BLOB-BACKED (artifacts, files, compliance_report_runs) — the bytes live in the
#     object store under an `object_key` that exists only ON the row. They keep their
#     existing object-store-aware purgers: delete the blob FIRST, and DEFER the row when
#     the store is unreachable so the next tick can retry. A generic row-DELETE here
#     drops the only handle on those bytes — a permanent orphan (R:BLOB_ORPHAN).
#   PLAIN — the payload is IN the row, so one bounded tenant_id-scoped DELETE is the
#     whole purge.
# ---------------------------------------------------------------------------

_ZDR_BLOB_BACKED_TABLES: frozenset[str] = frozenset(
    {"artifacts", "files", "compliance_report_runs"}
)

_ZDR_PLAIN_DELETES: dict[str, Any] = {
    "conversations": _DELETE_CONVERSATIONS_ZDR_TENANT,
    "memories": _DELETE_MEMORIES_ZDR_TENANT,
    "batch_job_items": _DELETE_BATCH_ITEMS_ZDR_TENANT,
    "video_generation_jobs": _DELETE_VIDEO_JOBS_ZDR_TENANT,
    "stored_responses": _DELETE_STORED_RESPONSES_ZDR_TENANT,
    "request_logs": _DELETE_REQUEST_LOGS_ZDR_TENANT,
    "vector_store_chunks": _DELETE_VECTOR_STORE_CHUNKS_ZDR_TENANT,
    "eval_cases": _DELETE_EVAL_CASES_ZDR_TENANT,
    "eval_case_results": _DELETE_EVAL_CASE_RESULTS_ZDR_TENANT,
    "finetune_job_events": _DELETE_FINETUNE_JOB_EVENTS_ZDR_TENANT,
    "finetune_jobs": _DELETE_FINETUNE_JOBS_ZDR_TENANT,
}

_ZDR_REGISTERED_TABLES: frozenset[str] = frozenset(_ZDR_PLAIN_DELETES) | _ZDR_BLOB_BACKED_TABLES

# FAIL LOUD AT IMPORT, never at 03:00 in a sweep nobody reads: a name declared in the
# inventory with no purger registered would be silently skipped every tick — a green
# structural guard over a table that is never actually purged, which is the same class of
# lie this task exists to kill. Both directions are checked, because a purger for a table
# that left the inventory is dead code that never runs. Deterministic (pure module
# constants), so this can only ever fire in CI, never differentially in production.
_ZDR_UNREGISTERED = tuple(sorted(set(ZDR_PURGE_TABLES) - _ZDR_REGISTERED_TABLES))
_ZDR_ORPHANED_PURGERS = tuple(sorted(_ZDR_REGISTERED_TABLES - set(ZDR_PURGE_TABLES)))
if _ZDR_UNREGISTERED or _ZDR_ORPHANED_PURGERS:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "ZDR purge dispatch is out of sync with retention_policy.ZDR_PURGE_TABLES: "
        f"declared-but-unpurgeable={_ZDR_UNREGISTERED or ()}, "
        f"purger-without-inventory-row={_ZDR_ORPHANED_PURGERS or ()}. "
        "Every inventory table needs either a _ZDR_PLAIN_DELETES entry or a branch in "
        "RetentionSweeper._purge_zdr_table_for_tenant."
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
        redis: Any = None,
        object_store: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        # tenant-retention-zdr TASK.md §3 — both optional (default None) so every EXISTING
        # RetentionSweeper(session_factory=..., settings=...) call site (main.py's own
        # pre-task wiring, test_retention_sweep.py's _make_sweeper) is unaffected. When
        # None, the ZDR Redis-namespace purge / s3-artifact purge sub-passes honest-degrade
        # to a no-op (logged), never crash the sweep.
        self._redis = redis
        self._object_store = object_store

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

    async def _emit_zdr_purge_audit(
        self,
        *,
        tenant_id: Any,
        table: str,
        rows_deleted: int,
    ) -> None:
        """Fire-and-forget: emit a data.purge audit event scoped (via metadata) to one
        tenant (fail-open).

        tenant-retention-zdr TASK.md §3 M10 ("system-level for the sweeper-driven bulk
        purge") + M7 scenario ("each table's purge (>0 rows deleted) emits a
        record_audit(action='data.purge') event scoped to that tenant"): the pre-existing
        audit_missing_actor invariant (audit/domain/audit_event.py — a FROZEN, unrelated
        contract) requires that any AuditEvent with tenant_id set carries an actor
        (actor_user_id or actor_key_id) — the background sweeper has neither. Reconciled
        as: tenant_id=None (a genuinely "system-level" event, mirroring
        _emit_purge_audit's own existing convention for the 3 original tables) while
        metadata still names the tenant explicitly, so "scoped to that tenant" holds as
        a queryable/auditable fact without violating the actor invariant. Swallows all
        exceptions.
        """
        try:
            import uuid as _uuid

            from gateway.audit.application.audit_writer import record_audit
            from gateway.audit.domain.audit_event import AuditEvent

            event = AuditEvent(
                id=_uuid.uuid4(),
                tenant_id=None,  # system event (sweeper has no actor) — see docstring
                actor_user_id=None,
                actor_email=None,
                action="data.purge",
                target_type="retention",
                target_id=table,
                result="success",
                metadata={
                    "tenant_id": str(tenant_id),
                    "table": table,
                    "reason": "zdr_enabled",
                    "rows_deleted": rows_deleted,
                },
                created_at=datetime.datetime.now(datetime.UTC),
            )
            await record_audit(self._session_factory, event)
        except Exception as exc:
            _log.warning(
                "retention_sweep: zdr purge audit emit failed for tenant=%s table=%s"
                " (swallowed): %s",
                tenant_id,
                table,
                exc,
                exc_info=exc,
            )

    # -----------------------------------------------------------------------
    # tenant-retention-zdr TASK.md §3 — ADDITIVE helpers (new passes only).
    # -----------------------------------------------------------------------

    async def _delete_batched_extra(
        self,
        table: str,
        delete_sql: Any,
        extra_params: dict[str, Any],
        batch_size: int,
    ) -> int:
        """Like _delete_batched, but for a query with NO single Python-computed cutoff
        (the cutoff, if any, is computed per-row inside the SQL via a JOIN to tenants).
        Never raises — failures are logged and returned as 0 (fail-open, per-pass).
        """
        total = 0
        try:
            while True:
                async with self._session_factory() as session:
                    async with session.begin():
                        cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                            delete_sql, {**extra_params, "batch": batch_size}
                        )
                        batch_deleted = cursor.rowcount or 0
                total += batch_deleted
                if batch_deleted < batch_size:
                    break
        except Exception as exc:
            _log.warning(
                "retention_sweep: batched extra delete from %s failed (swallowed): %s",
                table,
                exc,
                exc_info=exc,
            )
            return total
        return total

    async def _purge_artifacts_batch(
        self,
        select_sql: Any,
        batch_size: int,
        extra_params: dict[str, Any] | None = None,
    ) -> int:
        """SELECT candidate artifact rows, delete their s3 objects (if any), THEN delete
        the DB rows that were safe to drop. An s3-backed row whose object delete raises
        ObjectStoreUnavailableError is DEFERRED (its DB row is left in place) rather than
        dropped while its bytes may still be unreachable-but-present — the next sweep
        tick retries it (design-for-failure scenario, TASK.md §2).

        extra_params: additional bind params merged in (e.g. {"tid": tenant_id} for the
        per-tenant ZDR variant) — None for the un-scoped window-cutoff variant.
        """
        total = 0
        params: dict[str, Any] = {**(extra_params or {}), "batch": batch_size}
        try:
            while True:
                async with self._session_factory() as session:
                    rows = (await session.execute(select_sql, params)).all()
                if not rows:
                    break
                deletable_ids: list[Any] = []
                for row_id, object_key, storage_backend in rows:
                    if storage_backend == "s3" and object_key:
                        if self._object_store is None:
                            # Honest-degrade: no object store configured — cannot verify
                            # the bytes are gone, so defer this row rather than orphan
                            # an object with a dangling row-free key.
                            continue
                        try:
                            await self._object_store.delete(object_key)
                        except ObjectStoreUnavailableError as exc:
                            _log.warning(
                                "retention_sweep: artifact object delete failed for "
                                "key=%s (deferred to next tick): %s",
                                object_key,
                                exc,
                            )
                            continue
                    deletable_ids.append(row_id)
                if deletable_ids:
                    async with self._session_factory() as session:
                        async with session.begin():
                            cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                                _DELETE_ARTIFACTS_BY_ID, {"ids": deletable_ids}
                            )
                            total += cursor.rowcount or 0
                if len(rows) < batch_size or not deletable_ids:
                    # Fewer than a full batch was found (nothing more pending), OR every
                    # candidate in this batch was deferred (no progress possible right
                    # now) — stop rather than re-select the same deferred rows forever.
                    break
        except Exception as exc:
            _log.warning(
                "retention_sweep: artifact purge pass failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return total
        return total

    async def _purge_files_batch(
        self,
        select_sql: Any,
        batch_size: int,
        extra_params: dict[str, Any] | None = None,
    ) -> int:
        """files-uploads-api PLAN.md §3 — SELECT candidate files rows, delete their s3
        objects (if any), THEN delete the DB rows that were safe to drop. Mirrors
        _purge_artifacts_batch verbatim (an s3-object-delete failure DEFERS the row to the
        next tick rather than orphaning bytes; an inline row has no object to delete).

        extra_params: additional bind params merged in ({"operator_ceiling_days": ...} for
        the window variant, {"tid": tenant_id} for the per-tenant ZDR variant)."""
        total = 0
        params: dict[str, Any] = {**(extra_params or {}), "batch": batch_size}
        try:
            while True:
                async with self._session_factory() as session:
                    rows = (await session.execute(select_sql, params)).all()
                if not rows:
                    break
                deletable_ids: list[Any] = []
                for row_id, object_key, storage_backend in rows:
                    if storage_backend == "s3" and object_key:
                        if self._object_store is None:
                            continue
                        try:
                            await self._object_store.delete(object_key)
                        except ObjectStoreUnavailableError as exc:
                            _log.warning(
                                "retention_sweep: file object delete failed for "
                                "key=%s (deferred to next tick): %s",
                                object_key,
                                exc,
                            )
                            continue
                    deletable_ids.append(row_id)
                if deletable_ids:
                    async with self._session_factory() as session:
                        async with session.begin():
                            cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                                _DELETE_FILES_BY_ID, {"ids": deletable_ids}
                            )
                            total += cursor.rowcount or 0
                if len(rows) < batch_size or not deletable_ids:
                    break
        except Exception as exc:
            _log.warning(
                "retention_sweep: file purge pass failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return total
        return total

    async def _purge_compliance_reports_batch(
        self,
        select_sql: Any,
        batch_size: int,
        extra_params: dict[str, Any] | None = None,
    ) -> int:
        """compliance-report-center TASK.md §3 M21 — SELECT candidate
        compliance_report_runs rows, delete their s3 objects, THEN delete the DB rows
        that were safe to drop. Mirrors _purge_artifacts_batch verbatim (an
        object-delete failure DEFERS the row to the next tick, never orphans bytes) —
        every row here is s3-backed by construction (§3 CONTRACT: object_key is
        NOT NULL, no inline-BYTEA branch exists for this table)."""
        total = 0
        params: dict[str, Any] = {**(extra_params or {}), "batch": batch_size}
        try:
            while True:
                async with self._session_factory() as session:
                    rows = (await session.execute(select_sql, params)).all()
                if not rows:
                    break
                deletable_ids: list[Any] = []
                for row_id, object_key in rows:
                    if object_key:
                        if self._object_store is None:
                            # Honest-degrade: no object store configured — defer
                            # rather than orphan an object with a dangling key.
                            continue
                        try:
                            await self._object_store.delete(object_key)
                        except ObjectStoreUnavailableError as exc:
                            _log.warning(
                                "retention_sweep: compliance report object delete "
                                "failed for key=%s (deferred to next tick): %s",
                                object_key,
                                exc,
                            )
                            continue
                    deletable_ids.append(row_id)
                if deletable_ids:
                    async with self._session_factory() as session:
                        async with session.begin():
                            cursor: CursorResult[Any] = await session.execute(  # type: ignore[assignment]
                                _DELETE_COMPLIANCE_REPORTS_BY_ID, {"ids": deletable_ids}
                            )
                            total += cursor.rowcount or 0
                if len(rows) < batch_size or not deletable_ids:
                    break
        except Exception as exc:
            _log.warning(
                "retention_sweep: compliance report purge pass failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return total
        return total

    async def _sweep_new_payload_window_pass(self, batch_size: int) -> dict[str, int]:
        """Per-tenant window-cutoff pass over the 5 newly-swept payload tables + the
        compliance_report_runs table.

        EVERY tenant is touched: the cutoff is COALESCE(tenants.retention_window_days,
        retention_tenant_window_ceiling_days) — a tenant with no override (column IS
        NULL) is swept at the operator ceiling (CRIT fix — see module docstring §1 and
        _Settings.retention_tenant_window_ceiling_days).

        Read directly off self._settings (NOT getattr(..., 0)): this field is a
        required member of the _Settings Protocol above and every real Settings
        instance always carries it (config.py default=365) — a getattr-with-0
        fallback would be a silent mass-immediate-purge footgun if the attribute were
        EVER absent (a NULL tenant's cutoff would become "now", i.e. immediately
        eligible for every default-posture tenant on every tick, with no error and no
        log). Fail loud (AttributeError) instead of fail-silent-and-destructive.
        """
        operator_ceiling_days = self._settings.retention_tenant_window_ceiling_days
        ceiling_params = {"operator_ceiling_days": operator_ceiling_days}
        results: dict[str, int] = {}
        results["artifacts"] = await self._purge_artifacts_batch(
            _SELECT_ARTIFACTS_WINDOW, batch_size, extra_params=ceiling_params
        )
        results["conversations"] = await self._delete_batched_extra(
            "conversations", _DELETE_CONVERSATIONS_WINDOW, ceiling_params, batch_size
        )
        results["memories"] = await self._delete_batched_extra(
            "memories", _DELETE_MEMORIES_WINDOW, ceiling_params, batch_size
        )
        results["batch_job_items"] = await self._delete_batched_extra(
            "batch_job_items", _DELETE_BATCH_ITEMS_WINDOW, ceiling_params, batch_size
        )
        results["video_generation_jobs"] = await self._delete_batched_extra(
            "video_generation_jobs", _DELETE_VIDEO_JOBS_WINDOW, ceiling_params, batch_size
        )
        # responses-state-store PLAN.md §3 M8 — additive, same tenant-window-cutoff
        # discipline as conversations above (keyed on created_at).
        results["stored_responses"] = await self._delete_batched_extra(
            "stored_responses", _DELETE_STORED_RESPONSES_WINDOW, ceiling_params, batch_size
        )
        # files-uploads-api PLAN.md §3 — additive, same tenant-window-cutoff discipline as
        # artifacts above (object-store-aware).
        results["files"] = await self._purge_files_batch(
            _SELECT_FILES_WINDOW, batch_size, extra_params=ceiling_params
        )
        # compliance-report-center TASK.md §3 M21 — additive, same tenant-window-
        # cutoff discipline as artifacts above.
        results["compliance_report_runs"] = await self._purge_compliance_reports_batch(
            _SELECT_COMPLIANCE_REPORTS_WINDOW, batch_size, extra_params=ceiling_params
        )
        return results

    async def _sweep_tenant_shorten_pass(self, batch_size: int) -> dict[str, int]:
        """Per-tenant SHORTENING pass over usage_records/alert_events.

        Cutoff = min(tenant_window, operator_default) — a pure no-op for a tenant whose
        override is >= the operator default (the original unconditional pass above
        already deleted everything past that point for EVERY tenant). Only runs for a
        table whose operator default is itself > 0 (an operator-disabled table stays
        fully un-swept regardless of any tenant override — the operator's "0 = off" is
        the stronger signal).
        """
        results: dict[str, int] = {}
        usage_default = self._settings.retention_usage_records_days
        if usage_default > 0:
            results["usage_records"] = await self._delete_batched_extra(
                "usage_records",
                _DELETE_USAGE_TENANT_SHORTEN,
                {"operator_default": usage_default},
                batch_size,
            )
        alert_default = self._settings.retention_alert_events_days
        if alert_default > 0:
            results["alert_events"] = await self._delete_batched_extra(
                "alert_events",
                _DELETE_ALERT_TENANT_SHORTEN,
                {"operator_default": alert_default},
                batch_size,
            )
        return results

    async def _purge_zdr_table_for_tenant(
        self, table: str, tid_params: dict[str, Any], batch_size: int
    ) -> int:
        """Purge ONE inventory table for ONE ZDR tenant; returns rows deleted.

        The dispatch A18 demands: the inventory tuple says WHICH tables are purged, this
        says HOW. The three blob-backed tables keep their existing object-store-aware
        purgers verbatim (blob first, row DEFERRED while the store is unreachable —
        R:BLOB_ORPHAN); everything else is one bounded tenant_id-scoped DELETE.

        Never raises: each purger already swallows-and-logs its own failures, so one
        unreachable table cannot abort the rest of a tenant's purge (the pre-existing
        fail-open-per-pass discipline).
        """
        if table == "artifacts":
            return await self._purge_artifacts_batch(
                _SELECT_ARTIFACTS_ZDR_TENANT, batch_size, extra_params=tid_params
            )
        if table == "files":
            return await self._purge_files_batch(
                _SELECT_FILES_ZDR_TENANT, batch_size, extra_params=tid_params
            )
        if table == "compliance_report_runs":
            return await self._purge_compliance_reports_batch(
                _SELECT_COMPLIANCE_REPORTS_ZDR_TENANT, batch_size, extra_params=tid_params
            )
        delete_sql = _ZDR_PLAIN_DELETES.get(table)
        if delete_sql is None:  # pragma: no cover - the import-time check forbids this
            _log.error(
                "retention_sweep: ZDR inventory table %r has no registered purger and was "
                "SKIPPED — a ZDR tenant's payload is surviving the sweep; register it in "
                "_ZDR_PLAIN_DELETES or _purge_zdr_table_for_tenant",
                table,
            )
            return 0
        return await self._delete_batched_extra(table, delete_sql, tid_params, batch_size)

    async def _sweep_zdr_purge_pass(self, batch_size: int) -> dict[str, int]:
        """Unconditional (no-cutoff) per-tenant ZDR purge — every row, every sweep tick,
        for every tenant with zdr_enabled=true, across EVERY table in
        ``retention_policy.ZDR_PURGE_TABLES``, PLUS ObjectStore.delete() for s3-backed
        rows and a bounded Redis SCAN+DEL over that tenant's resp-cache:/vec-cache:
        namespaces. Self-healing: re-runs every tick for as long as zdr_enabled=true, so a
        mid-purge crash is recovered automatically at the next tick — no job-tracking
        table (TASK.md §1 Assumption).

        The table set is the IMPORTED tuple, never a local enumeration
        (zdr-retention-inventory-extension M2): the previous hand-written list here fell
        three payload stores behind the schema without a single test noticing
        (R:SECOND_INVENTORY). Adding a table is now one row in that tuple plus its purger
        registration, and the import-time check above refuses the half-done version.

        Iterates PER TENANT (rather than one bulk all-tenants DELETE) so each table's
        rows-deleted count is attributable to a single tenant — required by TASK.md §3
        M10 + the M7 scenario: "each table's purge (>0 rows deleted) emits a
        record_audit(action='data.purge') event scoped to that tenant".
        """
        results: dict[str, int] = {}
        audit_tasks: set[asyncio.Task[None]] = set()

        def _fire_audit(tenant_id: Any, table: str, deleted: int) -> None:
            if deleted <= 0:
                return
            results[table] = results.get(table, 0) + deleted
            _t = asyncio.create_task(
                self._emit_zdr_purge_audit(tenant_id=tenant_id, table=table, rows_deleted=deleted)
            )
            audit_tasks.add(_t)
            _t.add_done_callback(audit_tasks.discard)

        try:
            async with self._session_factory() as session:
                tenant_rows = (await session.execute(_SELECT_ZDR_TENANT_IDS)).all()
        except Exception as exc:
            _log.warning(
                "retention_sweep: zdr tenant lookup failed (swallowed): %s", exc, exc_info=exc
            )
            tenant_rows = []

        for (tenant_id,) in tenant_rows:
            tid_params = {"tid": str(tenant_id)}
            for table in ZDR_PURGE_TABLES:
                _fire_audit(
                    tenant_id,
                    table,
                    await self._purge_zdr_table_for_tenant(table, tid_params, batch_size),
                )

        await self._purge_zdr_redis_namespaces()
        return results

    async def _purge_zdr_redis_namespaces(self) -> int:
        """Bounded SCAN+DEL of resp-cache:{tid}:* and vec-cache:{tid}:* for every
        zdr_enabled=true tenant. NEVER uses KEYS (blocking, unbounded). No-op (logged)
        when no redis client was wired at construction time. Never raises.
        """
        if self._redis is None:
            return 0
        total_deleted = 0
        try:
            async with self._session_factory() as session:
                tenant_rows = (await session.execute(_SELECT_ZDR_TENANT_IDS)).all()
            tenant_ids = [str(r[0]) for r in tenant_rows]
            for tid in tenant_ids:
                for prefix in ("resp-cache", "vec-cache"):
                    pattern = f"{prefix}:{tid}:*"
                    cursor: int = 0
                    while True:
                        cursor, keys = await self._redis.scan(
                            cursor=cursor, match=pattern, count=500
                        )
                        if keys:
                            await self._redis.delete(*keys)
                            total_deleted += len(keys)
                        if not cursor:
                            break
        except Exception as exc:
            _log.warning(
                "retention_sweep: zdr redis-namespace purge failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return total_deleted
        return total_deleted

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

        # ── tenant-retention-zdr TASK.md §3 — ADDITIVE passes ───────────────────────
        # Each pass is independently fail-open (isolated try/except) so one pass's
        # failure never prevents the others, or the 3 original passes above, from
        # completing. Every entry is ACCUMULATED into `results` (a table can receive
        # contributions from more than one pass, e.g. artifacts from both the window
        # pass and the ZDR purge pass).
        batch_size = self._settings.retention_batch_size or 1000

        try:
            for table, deleted in (await self._sweep_new_payload_window_pass(batch_size)).items():
                if deleted > 0:
                    results[table] = results.get(table, 0) + deleted
        except Exception as exc:
            _log.warning(
                "retention_sweep: new-payload window pass failed (swallowed): %s",
                exc,
                exc_info=exc,
            )

        try:
            for table, deleted in (await self._sweep_tenant_shorten_pass(batch_size)).items():
                if deleted > 0:
                    results[table] = results.get(table, 0) + deleted
        except Exception as exc:
            _log.warning(
                "retention_sweep: tenant-shortening pass failed (swallowed): %s",
                exc,
                exc_info=exc,
            )

        try:
            for table, deleted in (await self._sweep_zdr_purge_pass(batch_size)).items():
                if deleted > 0:
                    results[table] = results.get(table, 0) + deleted
        except Exception as exc:
            _log.warning(
                "retention_sweep: zdr purge pass failed (swallowed): %s",
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
