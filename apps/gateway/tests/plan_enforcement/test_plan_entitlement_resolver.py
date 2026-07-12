"""RED suite: M8 — SqlAlchemyPlanEntitlementResolver, the read-only in-process port
(TASK.md §3, FROZEN @ v1). Named consumer: seat-billing (wave-2).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import assign_plan, seed_plan, set_tenant_budget


async def _seed_tenant(db_session: AsyncSession, *, name: str) -> str:
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await db_session.commit()
    return str(tid)


async def test_resolver_matches_explicit_beats_default_precedence(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.tenants.infrastructure.plan_entitlement_resolver import (
        SqlAlchemyPlanEntitlementResolver,
    )

    tenant_id = await _seed_tenant(db_session, name="ResolverCo")
    plan_id = await seed_plan(db_session, name="team", budget_usd_monthly_default="500.00")
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=tenant_id, budget="100.00")

    resolver = SqlAlchemyPlanEntitlementResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result.effective_budget_usd_monthly == Decimal("100.00")
    assert result.plan_id == uuid.UUID(plan_id)


async def test_resolver_falls_back_to_plan_default_when_no_explicit_budget(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.tenants.infrastructure.plan_entitlement_resolver import (
        SqlAlchemyPlanEntitlementResolver,
    )

    tenant_id = await _seed_tenant(db_session, name="ResolverDefaultCo")
    plan_id = await seed_plan(db_session, name="starter", budget_usd_monthly_default="50.00")
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)

    resolver = SqlAlchemyPlanEntitlementResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result.effective_budget_usd_monthly == Decimal("50.00")


async def test_resolver_unplanned_tenant_is_unlimited_and_read_only(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.tenants.infrastructure.plan_entitlement_resolver import (
        SqlAlchemyPlanEntitlementResolver,
    )

    tenant_id = await _seed_tenant(db_session, name="ResolverUnplannedCo")

    row_before = (
        await db_session.execute(
            text("SELECT plan_id, budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        )
    ).fetchone()

    resolver = SqlAlchemyPlanEntitlementResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result.effective_budget_usd_monthly is None
    assert result.plan_id is None
    assert result.plan_feature_flags == frozenset()
    assert result.plan_model_allowlist is None

    row_after = (
        await db_session.execute(
            text("SELECT plan_id, budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        )
    ).fetchone()
    assert row_after == row_before, "resolve() must never write anything"


async def test_resolver_carries_plan_allowlist_and_feature_flags(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.tenants.infrastructure.plan_entitlement_resolver import (
        SqlAlchemyPlanEntitlementResolver,
    )

    tenant_id = await _seed_tenant(db_session, name="ResolverFullCo")
    plan_id = await seed_plan(
        db_session,
        name="enterprise",
        model_allowlist=["gpt-4o-mini"],
        feature_flags=["batch", "realtime"],
    )
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)

    resolver = SqlAlchemyPlanEntitlementResolver(session_factory=app.state.sessionmaker)
    result = await resolver.resolve(uuid.UUID(tenant_id))

    assert result.plan_model_allowlist == ["gpt-4o-mini"]
    assert result.plan_feature_flags == frozenset({"batch", "realtime"})
