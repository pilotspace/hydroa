"""Tenant-scoped retention policy: per-tenant window override + Zero-Data-Retention (ZDR).

Contract (tenant-retention-zdr TASK.md §3 — FROZEN @ v1):
  - is_zdr(session, tenant_id) -> bool: fresh per-call read of tenants.zdr_enabled, never
    cached. Published read port for the `payload-capture-store` sibling task to consume
    once it grounds (TASK.md §0 "Ruled OUT" note / §7 Spec delta) — its shape is pinned.
  - raise_if_zdr(session, tenant_id): raises 403 ERR_ZDR_PAYLOAD_BLOCKED iff is_zdr() is
    True. Called as the FIRST line of each of the five gated repository choke points
    (ArtifactRepository.create, ConversationRepository.create/append_message,
    MemoryRepository.create, BatchJobRepository.create, VideoJobRepository.create) —
    fail-closed, checked BEFORE anything else in that call happens (§5 Safety rule).
  - effective_window_days(table, tenant_window_days, settings) -> int: the per-table
    effective retention window shown by GET /admin/retention-policy and consumed by the
    RetentionSweeper's new per-tenant passes.

Table vocabulary:
  usage_records / alert_events   — the 2 pre-existing swept tables (data-retention-
    controls v1). Each already has its OWN operator Settings knob; a tenant override can
    only SHORTEN below that knob, never lengthen past it, because the pre-existing
    unconditional sweep (RetentionSweeper's 3 original DELETEs) stays untouched and
    remains an outer bound regardless of any tenant override.
  artifacts / conversations / memories / batch_job_items / video_generation_jobs — the 5
    newly-swept payload tables. No prior operator per-table knob exists for them, so a
    tenant override (or, absent, the single operator ceiling) governs each standalone.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.error_catalog import ZDR_PAYLOAD_BLOCKED

# The newly-swept payload tables (tenant-retention-zdr TASK.md §3 M3). files-uploads-api
# PLAN.md §3 registers `files` as payload store #6 here — so GET /admin/retention-policy
# reports a finite effective window for it AND the RetentionSweeper actually purges it
# (both the per-tenant window pass and the ZDR purge pass sweep files; see
# usage/application/retention_sweep.py). Reporting a window without sweeping would be the
# exact CRIT honesty bug the sweeper's own docstring warns against.
NEW_PAYLOAD_TABLES: tuple[str, ...] = (
    "artifacts",
    "conversations",
    "memories",
    "batch_job_items",
    "video_generation_jobs",
    "files",
)

# The 2 pre-existing swept tables (data-retention-controls v1) — each carries its own
# operator Settings knob that a tenant override can only shorten below, never lengthen
# past (see module docstring).
EXISTING_SWEPT_TABLES: tuple[str, ...] = ("usage_records", "alert_events")

# Every table window_days can apply to (M3) — audit_events is deliberately excluded by
# construction (R4): no field, no code path, ever touches it.
ALL_SWEPT_TABLES: tuple[str, ...] = EXISTING_SWEPT_TABLES + NEW_PAYLOAD_TABLES

# ---------------------------------------------------------------------------
# The ZDR purge inventory (zdr-retention-inventory-extension TASK.md §3 M2 — FROZEN)
#
# THE one declaration of WHICH tables an unconditional ZDR purge empties for a
# zdr_enabled=true tenant. `usage/application/retention_sweep.py` imports THIS OBJECT
# and drives `_sweep_zdr_purge_pass` from it — there is deliberately no second list
# anywhere, because a hand-maintained enumeration inside the sweeper is exactly how the
# three newest payload stores (vector stores, evals, finetune) went unpurged for three
# releases (deep-review P0 artifact 6816985f, R:SECOND_INVENTORY).
#
# The tuple names WHICH tables are purged, never HOW (A18). Three of these names —
# artifacts, files, compliance_report_runs — are BLOB-BACKED: their bytes live in the
# object store under an `object_key` that exists only ON the row, so they keep their
# existing object-store-aware purgers (delete the blob first, DEFER the row when the
# store is unreachable). Driving them through a generic row-DELETE would strand those
# bytes where no later tick could ever find them (R:BLOB_ORPHAN). The dispatch that
# enforces this lives beside the sweeper, next to the SQL it selects between.
#
# ORDERING: children before their containers (finetune_job_events before finetune_jobs)
# so every child DELETE does its own work and is provably explicit rather than an FK
# ON DELETE CASCADE side effect (R:CASCADE_RELIANCE). No FK ordering is REQUIRED — every
# parent link here is ON DELETE CASCADE — but a purge that relies on the cascade cannot
# survive a re-parented or denormalized row.
#
# ADDING A TABLE: add the name here AND register its purger in
# retention_sweep._ZDR_PURGERS. The sweeper refuses to import with a name that has no
# purger (fail-loud at import, never a silently-skipped table), and
# tests/retention_zdr_inventory's structural guard fails for any tenant_id-carrying
# payload-shaped table that is in neither this tuple nor its named exemption list.
# ---------------------------------------------------------------------------
ZDR_PURGE_TABLES: tuple[str, ...] = (
    # --- the nine the pass already purged before this task ---------------------
    "artifacts",  # blob-backed
    "conversations",
    "memories",
    "batch_job_items",
    "video_generation_jobs",
    "stored_responses",
    "files",  # blob-backed
    "request_logs",
    "compliance_report_runs",  # blob-backed
    # --- the five the hand-enumeration silently missed (the P0) ----------------
    "vector_store_chunks",  # chunk text + embeddings
    "eval_cases",  # request bodies + assertions
    "eval_case_results",  # model response text
    "finetune_job_events",  # event payload (before its parent, see ORDERING)
    "finetune_jobs",  # hyperparameters + provider error text
)


class _RetentionSettings(Protocol):
    """Minimal protocol for the Settings fields this module reads."""

    retention_usage_records_days: int
    retention_alert_events_days: int
    retention_tenant_window_ceiling_days: int


async def is_zdr(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Fresh per-call read of tenants.zdr_enabled — never cached.

    Returns False for an unknown tenant_id (fail-open on lookup; the actual
    authorization decision for an unknown tenant is made elsewhere, e.g. the
    retention-policy router's own 404 TENANT_NOT_FOUND).
    """
    row = (
        await session.execute(
            text("SELECT zdr_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).first()
    return bool(row[0]) if row is not None else False


async def raise_if_zdr(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Raise 403 ERR_ZDR_PAYLOAD_BLOCKED iff the tenant currently has zdr_enabled=true.

    Fresh per-call — no caching beyond this one SELECT. Call as the FIRST line of every
    gated repository write (fail-closed ordering: block new writes before anything else
    in that call happens — TASK.md §5 Safety rule).

    NON-LOCKING by design (zdr-ingest-lock-heal §3 M4): the six gated choke points above
    call this on their hot write paths; a FOR UPDATE here would serialize every one of
    them on the tenant row. When the ZDR DECISION must be atomic with the write it gates
    — i.e. the payload lands after an AWAIT — use ``raise_if_zdr_locked`` instead.
    """
    if await is_zdr(session, tenant_id):
        raise ZDR_PAYLOAD_BLOCKED.exc()


# ---------------------------------------------------------------------------
# The LOCK-TAKING variant (zdr-ingest-lock-heal PLAN.md §3 — FROZEN @ v1)
#
# Additive: `is_zdr` / `raise_if_zdr` above are the FROZEN v1 surface and stay
# byte-identical. This pair exists for the write paths where the payload is
# persisted AFTER an await (a provider round-trip), so the entry-gate check and
# the write are separated by a window a tenant can flip `zdr_enabled` inside.
#
# WHY A LOCK AND NOT JUST A SECOND PLAIN READ: a plain SELECT under read-committed
# only observes a flip that COMMITTED BEFORE it ran — the read -> write-commit
# window stays open, and a flip landing there still persists payload at rest.
# This is not theoretical: it was HARD-STOPPED twice on this codebase (the
# stored-response persist path, and the vector-store ingest worker, whose first
# heal used a plain re-read and left a window wide enough for a bulk chunk
# insert). `FOR UPDATE` makes the decision and the write ATOMIC — a concurrent
# flip for the SAME tenant blocks on ordinary row-lock contention until this
# transaction resolves, so it can never interleave between them.
#
# The guarantee is SERIALIZATION, not clairvoyance: the flip lands either fully
# before the write (caught here, fail-closed) or fully after it (the rows are
# written, then removed by the RetentionSweeper's ZDR purge pass). It can never
# land strictly between.
#
# CALL IT INSIDE the same session/transaction that commits the write, BEFORE the
# write — the lock is held until that transaction commits or rolls back.
# ---------------------------------------------------------------------------


async def is_zdr_locked(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """LOCK-TAKING read of tenants.zdr_enabled (``SELECT ... FOR UPDATE``).

    The lock is scoped to the session's already-open (autobegun) transaction and
    held until it commits or rolls back. See the module comment above for when to
    prefer this over the plain ``is_zdr``.

    Returns False for an unknown tenant_id — parity with ``is_zdr``'s documented
    fail-open-on-lookup behavior (the authorization decision for an unknown tenant
    is made elsewhere).
    """
    row = (
        await session.execute(
            text("SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE"),
            {"tid": str(tenant_id)},
        )
    ).first()
    return bool(row[0]) if row is not None else False


async def raise_if_zdr_locked(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Raise 403 ERR_ZDR_PAYLOAD_BLOCKED iff ``is_zdr_locked`` is True.

    The atomic counterpart of ``raise_if_zdr``: call it inside the transaction that
    commits the payload write, immediately before that write.
    """
    if await is_zdr_locked(session, tenant_id):
        raise ZDR_PAYLOAD_BLOCKED.exc()


def effective_window_days(
    table: str,
    *,
    tenant_window_days: int | None,
    settings: _RetentionSettings,
) -> int:
    """The effective retention window (days) for one swept table (§3 CONTRACT).

    usage_records / alert_events: an existing operator per-table default is an upper
    bound the tenant override can only shorten — min(tenant_window, operator_default)
    when an override is set, else the operator_default alone (byte-identical to the
    pre-task default state).

    The five newly-swept payload tables have no prior operator per-table knob: the
    tenant override (or, absent, the single operator ceiling) governs standalone.
    """
    if table == "usage_records":
        operator_default = settings.retention_usage_records_days
    elif table == "alert_events":
        operator_default = settings.retention_alert_events_days
    else:
        operator_default = settings.retention_tenant_window_ceiling_days

    if tenant_window_days is None:
        return int(operator_default)
    if table in EXISTING_SWEPT_TABLES:
        return min(int(tenant_window_days), int(operator_default))
    return int(tenant_window_days)


def effective_window_map(
    *,
    tenant_window_days: int | None,
    settings: Any,
) -> dict[str, int]:
    """The full {table: effective_window_days} map for every swept table (M1's GET shape)."""
    return {
        table: effective_window_days(
            table, tenant_window_days=tenant_window_days, settings=settings
        )
        for table in ALL_SWEPT_TABLES
    }
