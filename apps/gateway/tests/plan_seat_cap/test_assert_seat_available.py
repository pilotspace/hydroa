"""RED suite: assert_seat_available — the M2 admission-time locked check (TASK.md §3
CONTRACT M2/M8/M9, FROZEN @ v1).

Calls the application-layer helper DIRECTLY against real Postgres (row locking is not
meaningfully fakeable, per §5 Strategy step 2) — BEFORE any HTTP call site is wired, this
is the seam's own unit-level proof.

RED until `gateway.tenants.application.entitlements.assert_seat_available` /
`gateway.tenants.domain.errors.SeatCapExceededError` exist.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    active_user_count,
    assign_plan,
    capture_executed_sql,
    platform_tenant_id,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
)

pytestmark = pytest.mark.asyncio


async def test_unplanned_tenant_uncapped_no_count_query(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'An unplanned tenant is uncapped, no count query issued' — plan_id=NULL,
    tenants.seat_cap=NULL -> returns immediately after its one locked SELECT; no COUNT(*)
    query is issued against users."""
    from gateway.tenants.application.entitlements import assert_seat_available

    owner = await signup_owner(client)

    with capture_executed_sql(app) as statements:
        await assert_seat_available(db_session, uuid.UUID(owner["tenant_id"]))

    assert not any("users" in s.lower() and "count" in s.lower() for s in statements), statements


async def test_planned_tenant_both_seat_caps_null_is_uncapped(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'A planned tenant whose plan and tenant seat_cap are both NULL is
    uncapped' — plan 'enterprise' (seat_cap=NULL), tenants.seat_cap=NULL -> returns
    immediately, byte-identical to an unplanned tenant."""
    from gateway.tenants.application.entitlements import assert_seat_available

    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="enterprise-seatcap", seat_cap=None)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    with capture_executed_sql(app) as statements:
        await assert_seat_available(db_session, uuid.UUID(owner["tenant_id"]))

    assert not any("users" in s.lower() and "count" in s.lower() for s in statements), statements


async def test_platform_tenant_unconditionally_uncapped(
    app: object, db_session: AsyncSession
) -> None:
    """Scenario: 'The platform tenant is unconditionally uncapped' — kind='platform',
    plan_id permanently NULL by DB constraint -> returns immediately, no plan/seat_cap
    state could ever change this (M9)."""
    from gateway.tenants.application.entitlements import assert_seat_available

    tid = await platform_tenant_id(db_session)

    with capture_executed_sql(app) as statements:
        await assert_seat_available(db_session, tid)

    assert not any("users" in s.lower() and "count" in s.lower() for s in statements), statements


async def test_at_cap_raises_seat_cap_exceeded(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2 positive: a tenant already AT its effective cap raises SeatCapExceededError,
    carrying plan_id/plan_name/seat_cap/current_seats (structured, per §3 M2)."""
    from gateway.tenants.application.entitlements import assert_seat_available
    from gateway.tenants.domain.errors import SeatCapExceededError

    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="starter-seatcap-atcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    # owner is seat #1; top up to exactly 5.
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=4)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5

    with pytest.raises(SeatCapExceededError) as excinfo:
        await assert_seat_available(db_session, uuid.UUID(owner["tenant_id"]))

    err = excinfo.value
    assert err.seat_cap == 5
    assert err.current_seats == 5
    assert str(err.plan_id) == plan_id


async def test_under_cap_returns_none(client: httpx.AsyncClient, db_session: AsyncSession) -> None:
    """M2 positive: a tenant under its effective cap returns None (no raise)."""
    from gateway.tenants.application.entitlements import assert_seat_available

    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="starter-seatcap-undercap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=3)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 4

    result = await assert_seat_available(db_session, uuid.UUID(owner["tenant_id"]))
    assert result is None
