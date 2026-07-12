"""RED suite: M7 — no retroactive lockout, ever (TASK.md §3 CONTRACT, FROZEN @ v1).

A plan downgrade / seat_cap lowering below current headcount never deactivates anyone;
the cap applies only to the NEXT admission. Raising the cap unblocks the next admission
immediately (always-live read, no caching layer).

Uses the EXISTING, frozen `PUT /admin/platform/tenants/{tenant_id}/plan` (plan-catalog
TASK.md §3 M6-M9, unmodified by this task) to change plan/seat_cap — mirrors
tests/plan_catalog/test_plan_catalog.py's own superadmin-token-minting idiom.

RED until `assert_seat_available` is wired into the 4 admission seams — pre-Build every
admission attempt below succeeds regardless of headcount, so the "blocked from admitting
a 16th member" assertion fails (200/201 instead of 403).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    active_user_count,
    assert_problem,
    assign_plan,
    bearer,
    issue_token,
    platform_tenant_id,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
)

pytestmark = pytest.mark.asyncio


async def _superadmin_token(app: Any, db_session: AsyncSession) -> str:
    tid = await platform_tenant_id(db_session)
    return issue_token(app, role=Role.SUPERADMIN, tenant_id=tid, email="root@platform.internal")


async def _put_tenant_plan(
    client: httpx.AsyncClient,
    *,
    superadmin_token: str,
    tenant_id: str,
    plan_id: str | None,
    seat_cap: Any = "__omit__",
) -> httpx.Response:
    body: dict[str, Any] = {"plan_id": plan_id}
    if seat_cap != "__omit__":
        body["seat_cap"] = seat_cap
    return await client.put(
        f"/admin/platform/tenants/{tenant_id}/plan",
        json=body,
        headers=bearer(superadmin_token),
    )


async def _create_pending_invite(
    client: httpx.AsyncClient, *, owner_token: str, email: str, role: str = "member"
) -> dict[str, Any]:
    r = await client.post(
        "/admin/invites", json={"email": email, "role": role}, headers=bearer(owner_token)
    )
    assert r.status_code == 201, f"setup failed creating invite: {r.text}"
    return r.json()


async def test_plan_downgrade_below_headcount_does_not_deactivate_anyone(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'A plan downgrade below current headcount does not deactivate anyone'
    (M7) — tenant on plan 'team' (seat_cap=20) with 15 active users; superadmin downgrades
    to 'starter' (seat_cap=5) -> the downgrade succeeds and all 15 users remain active;
    the tenant's active-member count (15) now exceeds its new cap (5) — no scan/sweep/
    deactivation runs, this state persists indefinitely."""
    owner = await signup_owner(client, email="owner@downgradeseatcap.example")
    team_plan_id = await seed_plan(db_session, name="team-downgrade", seat_cap=20)
    starter_plan_id = await seed_plan(db_session, name="starter-downgrade", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=team_plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=14)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 15

    superadmin_token = await _superadmin_token(app, db_session)
    resp = await _put_tenant_plan(
        client,
        superadmin_token=superadmin_token,
        tenant_id=owner["tenant_id"],
        plan_id=starter_plan_id,
    )

    assert resp.status_code == 200, resp.text
    # All 15 remain active — the downgrade itself never scans/touches users rows.
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 15


async def test_over_cap_tenant_blocked_from_admitting_a_16th_member(
    client: httpx.AsyncClient, db_session: AsyncSession, app: object
) -> None:
    """Scenario: 'The over-cap tenant from above is blocked from admitting a 16th
    member' (M7, R1-R4) — same over-cap tenant (15 active users, effective seat_cap now
    5) -> a brand-new admission (invite-accept) is rejected; the cap applies to the NEXT
    admission, never retroactively to the 15 already present."""
    owner = await signup_owner(client, email="owner@overcapseatcap.example")
    team_plan_id = await seed_plan(db_session, name="team-overcap", seat_cap=20)
    starter_plan_id = await seed_plan(db_session, name="starter-overcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=team_plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=14)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 15

    superadmin_token = await _superadmin_token(app, db_session)
    downgrade = await _put_tenant_plan(
        client,
        superadmin_token=superadmin_token,
        tenant_id=owner["tenant_id"],
        plan_id=starter_plan_id,
    )
    assert downgrade.status_code == 200, downgrade.text

    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="sixteenth@overcapseatcap.example"
    )
    resp = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )

    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 15


async def test_raising_seat_cap_unblocks_the_next_admission_immediately(
    client: httpx.AsyncClient, db_session: AsyncSession, app: object
) -> None:
    """Scenario: 'Raising the seat cap unblocks the next admission immediately' (M2
    positive, live-read) — the over-cap tenant (15 active users, seat_cap=5); a
    superadmin raises tenants.seat_cap to 20 -> the very next admission attempt is
    evaluated against 20, not 5 — no caching layer, always-live read."""
    owner = await signup_owner(client, email="owner@raiseseatcap.example")
    team_plan_id = await seed_plan(db_session, name="team-raise", seat_cap=20)
    starter_plan_id = await seed_plan(db_session, name="starter-raise", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=team_plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=14)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 15

    superadmin_token = await _superadmin_token(app, db_session)
    downgrade = await _put_tenant_plan(
        client,
        superadmin_token=superadmin_token,
        tenant_id=owner["tenant_id"],
        plan_id=starter_plan_id,
    )
    assert downgrade.status_code == 200, downgrade.text

    blocked_invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="blocked@raiseseatcap.example"
    )
    blocked_resp = await client.post(
        f"/invites/{blocked_invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert_problem(blocked_resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")

    raise_cap = await _put_tenant_plan(
        client,
        superadmin_token=superadmin_token,
        tenant_id=owner["tenant_id"],
        plan_id=starter_plan_id,
        seat_cap=20,
    )
    assert raise_cap.status_code == 200, raise_cap.text

    unblocked_invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="unblocked@raiseseatcap.example"
    )
    unblocked_resp = await client.post(
        f"/invites/{unblocked_invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert unblocked_resp.status_code == 200, unblocked_resp.text
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 16
