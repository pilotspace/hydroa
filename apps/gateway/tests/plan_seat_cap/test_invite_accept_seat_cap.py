"""RED suite: seat-cap enforcement at POST /invites/{token}/accept (TASK.md §3 CONTRACT
R1/M4, FROZEN @ v1) — the ACCEPT-not-ISSUANCE seam.

RED until `assert_seat_available` is wired into InviteRepository.accept AND the router's
except-clause translates SeatCapExceededError -> ERR_PLAN_SEAT_CAP_EXCEEDED (403): pre-Build
these tests fail because accepting always succeeds (200) regardless of headcount.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    active_user_count,
    assert_problem,
    assign_plan,
    bearer,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
    user_exists,
)

pytestmark = pytest.mark.asyncio


async def _create_pending_invite(
    client: httpx.AsyncClient, *, owner_token: str, email: str, role: str = "member"
) -> dict[str, Any]:
    r = await client.post(
        "/admin/invites", json={"email": email, "role": role}, headers=bearer(owner_token)
    )
    assert r.status_code == 201, f"setup failed creating invite: {r.text}"
    return r.json()


async def test_accepting_invite_at_seat_cap_is_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'Accepting an invite at the tenant's seat cap is rejected' (R1) — plan
    'starter' (seat_cap=5) with exactly 5 active users, a pending unexpired invite for a
    6th member -> 403 ERR_PLAN_SEAT_CAP_EXCEEDED; the invite stays 'pending', unflipped,
    and no users row was inserted."""
    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="starter-invite-atcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=4)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5

    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="sixth@invitetest.io"
    )

    resp = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    hint = resp.json().get("upgrade_hint")
    assert hint is not None
    assert hint["seat_cap"] == 5
    assert hint["current_seats"] == 5

    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5
    assert not await user_exists(db_session, email="sixth@invitetest.io")

    invite_status = (
        await db_session.execute(
            text("SELECT status FROM invites WHERE id = :id"), {"id": uuid.UUID(invite["id"])}
        )
    ).scalar_one()
    assert invite_status == "pending"


async def test_accepting_invite_under_seat_cap_succeeds(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'Accepting an invite under the seat cap succeeds' — same tenant with 4
    active users (one below its cap of 5) -> 200, a new users row exists, invite
    'accepted'."""
    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="starter-invite-undercap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=3)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 4

    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="fifth@invitetest.io"
    )

    resp = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    assert resp.status_code == 200, resp.text
    assert await user_exists(db_session, email="fifth@invitetest.io")
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5


async def test_issuing_invite_never_gated_by_seat_cap(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'Issuing an invite is never gated by the seat cap, even already at cap'
    (framing) — a tenant already at its seat cap of 5 -> issuing a 6th invite still
    succeeds (201); the cap is consulted only at ACCEPT, never at issuance."""
    owner = await signup_owner(client)
    plan_id = await seed_plan(db_session, name="starter-issue-atcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=4)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5

    resp = await client.post(
        "/admin/invites",
        json={"email": "yet-another@invitetest.io", "role": "member"},
        headers=bearer(owner["owner_token"]),
    )

    assert resp.status_code == 201, resp.text
