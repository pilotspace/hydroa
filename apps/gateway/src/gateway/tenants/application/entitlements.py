"""check_plan_feature — the shared M6 feature-gate helper (plan-enforcement TASK.md §3,
FROZEN @ v1).

ONE query per call (mirrors RedisBudgetGuard's own "one SELECT per request, MVP"
acceptance) — these are admin/config-write/query paths, NOT the hot proxy path, so a
per-call SELECT is the right trade-off (no caching layer, always-fresh read, same
always-live-read property every other tenant-level toggle in this codebase already has).

Call sites (4, each an ADDITIVE precondition on an existing shipped/frozen endpoint):
  PUT /admin/batch-policy            -> check_plan_feature(session, tenant_id, "batch")
  PUT /admin/guardrails (ml_moderation key only)
                                      -> check_plan_feature(session, tenant_id, "ml_moderation")
  GET /admin/logs, /admin/logs/{id}  -> check_plan_feature(session, tenant_id, "logs_explorer")
  WS  /v1/realtime/relay connect     -> check_plan_feature(session, tenant_id, "realtime")
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.error_catalog import PLAN_FEATURE_NOT_ENABLED
from gateway.tenants.domain.entitlements import resolve_entitlements
from gateway.tenants.domain.errors import SeatCapExceededError

_QUERY = text(
    "SELECT t.plan_id, p.feature_flags, p.name "
    "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid"
)

# plan-seat-cap TASK.md §3 (FROZEN @ v1, M2) — the ONE locked query. FOR UPDATE OF t
# so a concurrent admission targeting the SAME tenant serializes through this SELECT.
_SEAT_LOCK_QUERY = text(
    "SELECT t.seat_cap, t.plan_id, p.seat_cap, p.name "
    "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id "
    "WHERE t.id = :tid FOR UPDATE OF t"
)
_SEAT_COUNT_QUERY = text(
    "SELECT COUNT(*) FROM users WHERE tenant_id = :tid AND deactivated_at IS NULL"
)


def _as_flag_set(value: Any) -> frozenset[str]:
    """Defensive JSONB parse — same dict/str driver quirk guarded elsewhere in this repo
    (ApiKeyRepository.get_by_id's guardrail_configs precedent)."""
    if value is None:
        return frozenset()
    if isinstance(value, list):
        return frozenset(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return frozenset()
        return frozenset(parsed) if isinstance(parsed, list) else frozenset()
    return frozenset()


async def check_plan_feature(session: AsyncSession, tenant_id: uuid.UUID, feature: str) -> None:
    """Raise ERR_PLAN_FEATURE_NOT_ENABLED (403) iff the tenant has an assigned plan whose
    feature_flags does NOT list `feature`. A tenant with plan_id IS NULL is COMPLETELY
    unaffected (M7 — grandfathered-unlimited): returns silently, no query result even
    consulted beyond plan_id itself.
    """
    row = (await session.execute(_QUERY, {"tid": str(tenant_id)})).fetchone()
    if row is None or row[0] is None:
        # Unknown tenant or unplanned (plan_id IS NULL) — M7, inert, no gate at all.
        return

    plan_id, feature_flags_raw, plan_name = row
    flags = _as_flag_set(feature_flags_raw)
    if feature in flags:
        return

    raise PLAN_FEATURE_NOT_ENABLED.exc(
        extra={
            "upgrade_hint": {
                "plan_id": str(plan_id),
                "plan_name": plan_name,
                "feature": feature,
            }
        }
    )


async def assert_seat_available(session: AsyncSession, tenant_id: uuid.UUID) -> None:
    """Raise SeatCapExceededError iff admitting one more ACTIVE member would meet-or-
    exceed the tenant's effective seat cap (plan-seat-cap TASK.md §3 M2, FROZEN @ v1).

    MUST be called inside the caller's OWN already-open transaction, strictly BEFORE its
    member-creating INSERT (M3) — the `FOR UPDATE OF t` lock below is held only for the
    remainder of THAT transaction, so two concurrent callers targeting the SAME tenant
    serialize through this SELECT rather than racing a read-then-write gap.

    An unknown tenant (row is None) is treated the same as unplanned/uncapped — the
    caller's OWN downstream INSERT (e.g. an IntegrityError on an FK) is the correct place
    for an "unknown tenant" failure, not this helper.
    """
    row = (await session.execute(_SEAT_LOCK_QUERY, {"tid": str(tenant_id)})).fetchone()
    if row is None:
        return

    tenant_seat_cap, plan_id, plan_seat_cap_default, plan_name = row
    effective_seat_cap = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=plan_id,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_seat_cap=tenant_seat_cap,
        plan_seat_cap_default=plan_seat_cap_default,
    ).effective_seat_cap
    if effective_seat_cap is None:
        # Grandfathered-unlimited (M8/M9) — NO count query ever issued.
        return

    current_seats = (
        await session.execute(_SEAT_COUNT_QUERY, {"tid": str(tenant_id)})
    ).scalar_one()
    if current_seats >= effective_seat_cap:
        raise SeatCapExceededError(
            plan_id=plan_id,
            plan_name=plan_name,
            seat_cap=effective_seat_cap,
            current_seats=current_seats,
        )


__all__ = ["assert_seat_available", "check_plan_feature"]
