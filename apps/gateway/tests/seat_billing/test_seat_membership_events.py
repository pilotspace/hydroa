"""RED suite — the 5 seat_membership_events write sites + M4 backfill + M2 inert-gate on
the write side (seat-billing TASK.md §3 — FROZEN @ v2, M3/M4).

Drives each of the 5 member-creating/deactivating seams through its REAL HTTP surface
(invite-accept, SCIM create, OIDC new-user JIT-provision, domain-capture auto-join, SCIM
PATCH active) — never a direct SQL insert — so a missing instrumentation call site fails
exactly the way it would in production. Cross-suite imports from tests/plan_seat_cap's
conftest (signup_owner/OIDC-app-builder/SCIM-token helpers) mirror that sibling task's
own real-flow harness for the IDENTICAL 4 member-creating seams it just cap-gated.

RED before BUILD: `seat_membership_events` does not exist yet, so every assertion
against it fails with an UndefinedTableError — the honest missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    FakeDnsResolverForSeatBilling,
    bearer,
    build_oidc_app,
    claim_and_verify_domain,
    create_scim_token,
    membership_events_for_user,
    oidc_callback,
    scim_bearer,
    signup_owner,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def fake_dns(app: Any) -> FakeDnsResolverForSeatBilling:
    resolver = FakeDnsResolverForSeatBilling()
    app.state.dns_resolver = resolver
    return resolver


# ---------------------------------------------------------------------------
# M3(a) — InviteRepository.accept
# ---------------------------------------------------------------------------


async def test_invite_accept_appends_one_joined_event(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@inviteledger.example")
    invite_resp = await client.post(
        "/admin/invites",
        json={"email": "newmember@inviteledger.example", "role": "member"},
        headers=bearer(owner["owner_token"]),
    )
    assert invite_resp.status_code == 201, invite_resp.text
    token = invite_resp.json()["token"]

    preview = await client.get(f"/invites/{token}")
    assert preview.status_code == 200, preview.text

    accept = await client.post(
        f"/invites/{token}/accept", json={"password": "brand-new-horse-battery-01"}
    )
    assert accept.status_code == 200, accept.text
    new_user_id = accept.json()["user_id"]

    events = await membership_events_for_user(db_session, user_id=new_user_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "joined"


# ---------------------------------------------------------------------------
# M3(b) — SqlAlchemyScimUserRepository.create_user
# ---------------------------------------------------------------------------


async def test_scim_create_user_appends_one_joined_event(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@scimledger.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "scimjoiner@scimledger.example",
            "active": True,
        },
        headers=scim_bearer(scim_token),
    )
    assert resp.status_code == 201, resp.text
    new_user_id = resp.json()["id"]

    events = await membership_events_for_user(db_session, user_id=new_user_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "joined"


# ---------------------------------------------------------------------------
# M3(b2) — _get_or_provision_sso_user, NEW-USER branch only (v2/CR-1)
# ---------------------------------------------------------------------------


async def test_sso_new_user_appends_one_joined_event_existing_relogin_appends_none(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@ssoledger.example")
    engine = app.state.engine

    _oidc_app, oidc_client = build_oidc_app(
        engine=engine,
        tenant_id=owner["tenant_id"],
        domain="ssoledger.example",
        id_token_email="ssonewbie@ssoledger.example",
    )
    async with oidc_client:
        resp = await oidc_callback(oidc_client)
    assert resp.status_code == 302, resp.text

    from sqlalchemy import text

    new_user_id = (
        await db_session.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": "ssonewbie@ssoledger.example"},
        )
    ).scalar_one()
    events = await membership_events_for_user(db_session, user_id=str(new_user_id))
    assert len(events) == 1
    assert events[0]["event_type"] == "joined"

    # A SECOND callback for the SAME (now-existing) email — the existing-user branch
    # returns first and must append NO additional event (v2/CR-1's own scenario).
    _oidc_app2, oidc_client2 = build_oidc_app(
        engine=engine,
        tenant_id=owner["tenant_id"],
        domain="ssoledger.example",
        id_token_email="ssonewbie@ssoledger.example",
    )
    async with oidc_client2:
        relogin_resp = await oidc_callback(oidc_client2)
    assert relogin_resp.status_code == 302, relogin_resp.text

    events_after_relogin = await membership_events_for_user(db_session, user_id=str(new_user_id))
    assert len(events_after_relogin) == 1, "an existing member's SSO re-login writes NOTHING"


# ---------------------------------------------------------------------------
# M3(b3) — join_verified_tenant_domain (v2/CR-1)
# ---------------------------------------------------------------------------


async def test_domain_capture_join_appends_one_joined_event(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolverForSeatBilling
) -> None:
    owner = await signup_owner(client, email="owner@domainledger.example")
    await claim_and_verify_domain(
        client, owner_token=owner["owner_token"], domain="domainledger.example", fake_dns=fake_dns
    )

    resp = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ignored on the auto-join branch",
            "email": "newhire@domainledger.example",
            "password": "new-hire-horse-battery-01",
        },
    )
    assert resp.status_code == 201, resp.text
    new_user_id = resp.json()["user_id"]

    events = await membership_events_for_user(db_session, user_id=str(new_user_id))
    assert len(events) == 1
    assert events[0]["event_type"] == "joined"


# ---------------------------------------------------------------------------
# M3(c) — SqlAlchemyScimUserRepository.set_active, changed=True branch ONLY
# ---------------------------------------------------------------------------


async def test_deactivate_then_repeat_then_reactivate_appends_exactly_two_events(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@flipledger.example")
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])
    create = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "flipper@flipledger.example",
        },
        headers=scim_bearer(scim_token),
    )
    assert create.status_code == 201, create.text
    user_id = create.json()["id"]

    def _patch(active: bool) -> dict[str, Any]:
        return {
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": active}],
        }

    first_deactivate = await client.patch(
        f"/scim/v2/Users/{user_id}", json=_patch(False), headers=scim_bearer(scim_token)
    )
    assert first_deactivate.status_code == 200, first_deactivate.text

    repeat_deactivate = await client.patch(
        f"/scim/v2/Users/{user_id}", json=_patch(False), headers=scim_bearer(scim_token)
    )
    assert repeat_deactivate.status_code == 200, repeat_deactivate.text

    reactivate = await client.patch(
        f"/scim/v2/Users/{user_id}", json=_patch(True), headers=scim_bearer(scim_token)
    )
    assert reactivate.status_code == 200, reactivate.text

    events = await membership_events_for_user(db_session, user_id=user_id)
    # SCIM create_user itself appends the initial 'joined' event (M3(b)) — the repeat
    # active:false PATCH is a true no-op (zero extra rows for that step), so the total is
    # joined + deactivated + reactivated = 3, never 2 and never 4.
    assert len(events) == 3, "the REPEAT active:false PATCH must be a true no-op, zero extra rows"
    assert [e["event_type"] for e in events] == ["joined", "deactivated", "reactivated"]
