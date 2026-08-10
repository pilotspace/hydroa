"""RED suite: M6 (a rejection leaves everything byte-identical, no audit event) + the
M4 concurrency guarantee (TASK.md §3 CONTRACT, FROZEN @ v1) — the proof the
`FOR UPDATE OF t` lock actually serializes competing admissions, not merely compiles.

RED until `assert_seat_available` is wired into BOTH `InviteRepository.accept` and
`_get_or_provision_sso_user`'s new-user branch, holding the tenant-row lock through each
seam's own INSERT.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    active_user_count,
    assert_problem,
    assign_plan,
    audit_event_count,
    bearer,
    build_oidc_app,
    oidc_callback,
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


async def test_rejected_admission_fires_no_audit_event(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'A rejected admission at any seam never fires an audit event' (M6) — a
    tenant at its seat cap; a rejected invite-accept fires no `invite.accept` audit row,
    and every OTHER tenant's data is completely unaffected."""
    owner = await signup_owner(client, email="owner@auditseatcap.example")
    other = await signup_owner(client, email="owner@othertenant-auditseatcap.example")
    plan_id = await seed_plan(db_session, name="starter-audit-atcap", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=4)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5
    other_count_before = await active_user_count(db_session, tenant_id=other["tenant_id"])

    before = await audit_event_count(db_session, action="invite.accept")
    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="rejected@auditseatcap.example"
    )

    resp = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")

    # NEGATIVE WAIT: the assertion below is `after == before` — a rejected admission must
    # fire ZERO invite.accept audit events. Polling an unchanged count returns on the first
    # iteration and proves nothing; the elapsed window IS the test.
    await asyncio.sleep(0.05)  # let any fire-and-forget task complete, if one were (wrongly) fired
    after = await audit_event_count(db_session, action="invite.accept")
    assert after == before, "a rejected admission must fire zero invite.accept audit events"

    # The OTHER tenant is completely unaffected.
    assert await active_user_count(db_session, tenant_id=other["tenant_id"]) == other_count_before
    row = (
        (
            await db_session.execute(
                text("SELECT plan_id, seat_cap FROM tenants WHERE id = :tid"),
                {"tid": owner["tenant_id"]},
            )
        )
        .mappings()
        .one()
    )
    assert str(row["plan_id"]) == plan_id
    assert row["seat_cap"] is None  # never explicitly overridden for this tenant


async def test_concurrent_admissions_racing_the_last_seat_exactly_one_wins(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'Two concurrent admissions racing for the last remaining seat — exactly
    one wins' (M4/concurrency) — plan 'starter' (seat_cap=5) with exactly 4 active users
    (one seat remaining), a pending invite AND a fresh OIDC assertion for two DIFFERENT
    new emails, attempted at the same instant via POST /invites/{token}/accept and
    GET /admin/auth/oidc/callback concurrently -> exactly ONE succeeds, the other is
    rejected — never both succeeding (6 active users, over cap), never both failing (a
    real seat left unfilled while both callers saw a false rejection)."""
    owner = await signup_owner(client, email="owner@raceseatcap.example")
    plan_id = await seed_plan(db_session, name="starter-race", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=3)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 4

    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="racer-invite@raceseatcap.example"
    )

    engine = app.state.engine  # type: ignore[attr-defined]
    _oidc_app, oidc_client = build_oidc_app(
        engine=engine,
        tenant_id=owner["tenant_id"],
        domain="raceseatcap.example",
        id_token_email="racer-oidc@raceseatcap.example",
    )

    async with oidc_client:
        invite_resp, oidc_resp = await asyncio.gather(
            client.post(
                f"/invites/{invite['token']}/accept",
                json={"password": "correct horse battery staple"},
            ),
            oidc_callback(oidc_client),
        )

    invite_won = invite_resp.status_code == 200
    oidc_won = oidc_resp.status_code == 302
    statuses = sorted([invite_resp.status_code, oidc_resp.status_code])

    assert invite_won != oidc_won, (
        f"expected exactly one winner, got invite={invite_resp.status_code} "
        f"oidc={oidc_resp.status_code}"
    )
    if not invite_won:
        assert invite_resp.status_code == 403, invite_resp.text
        assert invite_resp.json().get("code") == "ERR_PLAN_SEAT_CAP_EXCEEDED"
    if not oidc_won:
        assert oidc_resp.status_code == 403, oidc_resp.text
        assert oidc_resp.json().get("code") == "ERR_PLAN_SEAT_CAP_EXCEEDED"

    # The two paths signal success DIFFERENTLY — the invite returns 200, the OIDC callback
    # returns 302 (see `oidc_won` above) — so the winning pair depends on WHICH racer won.
    # This assertion used to hard-code sorted([200, 403]), which holds only when the invite
    # wins; when the OIDC racer won, [302, 403] was the CORRECT outcome and the test failed
    # anyway, contradicting its own docstring ("exactly ONE succeeds") and the
    # `invite_won != oidc_won` assertion above. Scheduling decides the winner, so this was a
    # coin-flip failure that only showed up under load.
    expected = sorted([200, 403]) if invite_won else sorted([302, 403])
    assert statuses == expected, (
        f"expected one winner and one seat-cap rejection, got {statuses} "
        f"(invite_won={invite_won}, oidc_won={oidc_won})"
    )
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5
    assert await user_exists(db_session, email="racer-invite@raceseatcap.example") == invite_won
    assert await user_exists(db_session, email="racer-oidc@raceseatcap.example") == oidc_won
