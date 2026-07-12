"""RED suite: seat-cap enforcement at POST /scim/v2/Users (TASK.md §3 CONTRACT R4/M4/M5,
FROZEN @ v1) — `SqlAlchemyScimUserRepository.create_user`, restructured into its FIRST-EVER
explicit transaction to hold the `FOR UPDATE OF t` lock through the INSERT.

RED until `assert_seat_available` is wired into `create_user` (wrapped in
`async with self._session.begin():`) AND `scim_router.py`'s create_user except-clause
translates SeatCapExceededError -> `scim_seat_cap_exceeded()` (RFC 7644, 403, no scimType).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    active_user_count,
    assert_scim_error,
    assign_plan,
    create_scim_token,
    scim_bearer,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
    user_exists,
)

pytestmark = pytest.mark.asyncio


async def test_scim_create_at_seat_cap_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'SCIM user creation at the tenant's seat cap is rejected' (R4) — plan
    'team' (seat_cap=20) with exactly 20 active users, an IdP POSTs a new user -> 403 SCIM
    Error, status:'403', no scimType, detail naming the seat cap; no users row inserted;
    the SCIM token remains valid and unaffected."""
    owner = await signup_owner(client, email="owner@scimseatcap.example")
    plan_id = await seed_plan(db_session, name="team-scim-atcap", seat_cap=20)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=19)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 20

    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "new@corp.example",
            "active": True,
        },
        headers=scim_bearer(scim_token),
    )

    assert_scim_error(resp, 403, detail_contains="Seat cap exceeded")
    assert not await user_exists(db_session, email="new@corp.example")

    # The SCIM token remains valid and unaffected — a follow-up call under a DIFFERENT
    # (uncapped) tenant context proves it authenticates nothing was revoked; simplest
    # direct proof: re-list users with the SAME token still succeeds.
    list_resp = await client.get("/scim/v2/Users", headers=scim_bearer(scim_token))
    assert list_resp.status_code == 200, list_resp.text


async def test_scim_create_under_seat_cap_succeeds_unchanged(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'SCIM user creation under the seat cap succeeds exactly as before this
    task' (M2 positive) — same tenant with 19 active users -> 201 SCIM User,
    byte-identical to pre-task behavior."""
    owner = await signup_owner(client, email="owner@scimseatcapok.example")
    plan_id = await seed_plan(db_session, name="team-scim-undercap", seat_cap=20)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=18)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 19

    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "twentieth@corp.example",
            "active": True,
        },
        headers=scim_bearer(scim_token),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["userName"] == "twentieth@corp.example"
    assert body["active"] is True
    assert await user_exists(db_session, email="twentieth@corp.example")
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 20
