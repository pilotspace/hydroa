"""SqlAlchemyPlanEntitlementResolver — infra adapter for the PlanEntitlementResolver port
(plan-enforcement TASK.md §3, M8, FROZEN @ v1).

ONE query, read-only, in-process. Named consumer: seat-billing (wave-2).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.tenants.domain.entitlements import ResolvedEntitlements, resolve_entitlements

_QUERY = text(
    "SELECT t.budget_usd_monthly, t.plan_id, p.budget_usd_monthly_default, "
    "p.model_allowlist, p.feature_flags "
    "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid"
)


def _as_decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _as_json_list(value: Any) -> list[str] | None:
    """Defensive JSONB parse — asyncpg may hand back a list or a JSON-encoded str
    depending on driver/query path (same quirk ApiKeyRepository.get_by_id already
    guards for on guardrail_configs)."""
    if value is None:
        return None
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


class SqlAlchemyPlanEntitlementResolver:
    """Concrete PlanEntitlementResolver: one SELECT, read-only, in-process only."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, tenant_id: uuid.UUID) -> ResolvedEntitlements:
        async with self._session_factory() as session:
            row = (await session.execute(_QUERY, {"tid": str(tenant_id)})).fetchone()

        if row is None:
            # Unknown tenant — resolve to the same "no data" shape as unplanned.
            return resolve_entitlements(
                tenant_budget_usd_monthly=None,
                plan_id=None,
                plan_budget_usd_monthly_default=None,
                plan_model_allowlist=None,
                plan_feature_flags=None,
            )

        tenant_budget, plan_id, plan_budget_default, plan_allowlist_raw, plan_flags_raw = row

        return resolve_entitlements(
            tenant_budget_usd_monthly=_as_decimal(tenant_budget),
            plan_id=plan_id,
            plan_budget_usd_monthly_default=_as_decimal(plan_budget_default),
            plan_model_allowlist=_as_json_list(plan_allowlist_raw),
            plan_feature_flags=_as_json_list(plan_flags_raw) if plan_id is not None else None,
        )


__all__ = ["SqlAlchemyPlanEntitlementResolver"]
