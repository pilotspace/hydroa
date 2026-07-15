"""RED suite: M2/M5 — PlanRateLimitResolver, the new hot-path resolver
(plan-rate-enforcement TASK.md §3, FROZEN @ v1).

Real Postgres (tests/conftest.py's `app`/`db_session`). RED until
gateway.rate_limits.infrastructure.plan_rate_limit_resolver does not exist
(ModuleNotFoundError).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import assign_plan, seed_plan, set_tenant_rate_limits


async def _seed_tenant(db_session: AsyncSession, *, name: str) -> str:
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await db_session.commit()
    return str(tid)


async def test_unplanned_tenant_resolves_none(app: Any, db_session: AsyncSession) -> None:
    """§2 Scenario 'unplanned tenant is inert' — plan_id IS NULL, no tenant override ->
    resolve() returns (None, None) and never writes anything. Covers: M2, M5.
    """
    from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import (
        PlanRateLimitResolver,
        ResolvedRate,
    )

    tenant_id = await _seed_tenant(db_session, name="UnplannedRateCo")

    resolver = PlanRateLimitResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result == ResolvedRate(rpm=None, tpm=None)


async def test_unknown_tenant_resolves_none(app: Any) -> None:
    """An unknown tenant_id (no row at all) resolves the same inert (None, None) shape as
    unplanned — mirrors SqlAlchemyPlanEntitlementResolver's own unknown-tenant precedent.
    Covers: M2, M5.
    """
    from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import (
        PlanRateLimitResolver,
        ResolvedRate,
    )

    resolver = PlanRateLimitResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.uuid4())

    assert result == ResolvedRate(rpm=None, tpm=None)


async def test_resolver_applies_tenant_override_and_plan_default_precedence(
    app: Any, db_session: AsyncSession
) -> None:
    """A tenant assigned a plan with rpm/tpm defaults, PLUS its own tenant rpm override,
    resolves tenant-override-wins-rpm / plan-default-fills-tpm-gap — exercising the real
    one-query SELECT end to end (not just the pure resolve_entitlements unit). Covers: M1
    (integration), M2.
    """
    from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import (
        PlanRateLimitResolver,
        ResolvedRate,
    )

    tenant_id = await _seed_tenant(db_session, name="OverrideRateCo")
    plan_id = await seed_plan(
        db_session, name="team-rate", rpm_limit_default=600, tpm_limit_default=400000
    )
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)
    await set_tenant_rate_limits(db_session, tenant_id=tenant_id, rpm_limit=10, tpm_limit=None)

    resolver = PlanRateLimitResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result == ResolvedRate(rpm=10, tpm=400000)


async def test_resolver_db_error_fails_open(app: Any) -> None:
    """§2 Scenario 'resolver DB error fails open' — the resolver's SELECT raising ANY
    exception resolves to (None, None) and never propagates. Covers: M5.
    """
    from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import (
        PlanRateLimitResolver,
        ResolvedRate,
    )

    class _BrokenSessionFactory:
        def __call__(self) -> Any:
            raise ConnectionError("DB unavailable (test double)")

    resolver = PlanRateLimitResolver(session_factory=_BrokenSessionFactory())  # type: ignore[arg-type]
    result = await resolver.resolve(uuid.uuid4())

    assert result == ResolvedRate(rpm=None, tpm=None)
