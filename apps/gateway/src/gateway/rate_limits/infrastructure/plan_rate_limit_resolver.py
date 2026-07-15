"""PlanRateLimitResolver — infra adapter resolving a tenant's effective plan rpm/tpm
ceiling (plan-rate-enforcement TASK.md §3, M2, FROZEN @ v1).

ONE query per resolve() call — mirrors RedisBudgetGuard._fetch_budget's own "one SELECT
per request, MVP" hot-path precedent (budgets/infrastructure/redis_guard.py). Unlike that
guard, THIS resolver has no caller-side fail-open try/except to lean on (there is no
"check()" wrapper analogous to RedisBudgetGuard.check()), so it owns its OWN safety net:
resolve() NEVER raises — any DB error resolves to ResolvedRate(None, None), the same
"completely inert" shape as an unplanned tenant or one whose plan sets no rpm/tpm default
(M5 — availability over enforcement; this is not a security gate).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.tenants.domain.entitlements import resolve_entitlements

_log = logging.getLogger(__name__)

# §3 CONTRACT's exact query — selects BOTH tenant overrides and plan defaults in one
# round-trip via the same tenants LEFT JOIN plans shape RedisBudgetGuard._fetch_budget /
# assert_seat_available / check_plan_feature already use.
_QUERY = text(
    "SELECT t.rpm_limit, t.tpm_limit, p.rpm_limit_default, p.tpm_limit_default "
    "FROM tenants t LEFT JOIN plans p ON t.plan_id = p.id WHERE t.id = :tid"
)


@dataclass(frozen=True, slots=True)
class ResolvedRate:
    """Resolved (rpm, tpm) tenant-layer ceiling — §3 CONTRACT's `ResolvedRate`.

    Either field None = unlimited on that dimension (no tenant-scoped window is ever
    checked for it) — computed independently, mirrors ResolvedEntitlements' own
    per-dimension independence.
    """

    rpm: int | None
    tpm: int | None


class PlanRateLimitResolver:
    """Concrete resolver: one SELECT, fail-open on ANY error (M5) — resolve() never raises."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def resolve(self, tenant_id: uuid.UUID) -> ResolvedRate:
        """Return the tenant's effective (rpm, tpm) ceiling.

        Unknown tenant, or all four of tenant-override/plan-default NULL -> (None, None)
        (M2 — completely inert, no tenant window ever checked for that dimension).
        A DB error at any point resolves to (None, None) too and is logged, never
        propagated — this resolver's own safety net (M5); it has no caller-side
        try/except wrapper to rely on, unlike RedisBudgetGuard.check().
        """
        try:
            async with self._session_factory() as session:
                row = (await session.execute(_QUERY, {"tid": str(tenant_id)})).fetchone()
        except Exception as exc:
            _log.warning(
                "plan_rate_limit_resolver.resolve failed (swallowed — fail open)",
                exc_info=exc,
                extra={"tenant_id": str(tenant_id)},
            )
            return ResolvedRate(rpm=None, tpm=None)

        if row is None:
            return ResolvedRate(rpm=None, tpm=None)

        tenant_rpm, tenant_tpm, plan_rpm_default, plan_tpm_default = row
        resolved = resolve_entitlements(
            tenant_budget_usd_monthly=None,
            plan_id=None,
            plan_budget_usd_monthly_default=None,
            plan_model_allowlist=None,
            plan_feature_flags=None,
            tenant_rpm_limit=tenant_rpm,
            plan_rpm_limit_default=plan_rpm_default,
            tenant_tpm_limit=tenant_tpm,
            plan_tpm_limit_default=plan_tpm_default,
        )
        return ResolvedRate(rpm=resolved.effective_rpm_limit, tpm=resolved.effective_tpm_limit)


__all__ = ["PlanRateLimitResolver", "ResolvedRate"]
