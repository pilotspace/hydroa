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

_QUERY = text(
    "SELECT t.plan_id, p.feature_flags, p.name "
    "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid"
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


__all__ = ["check_plan_feature"]
