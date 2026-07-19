"""RED suite: seat-cap enforcement at the SSO JIT-provisioning seams — OIDC callback +
SAML ACS (TASK.md §3 CONTRACT R2/M4/M7, FROZEN @ v1) — "provision new user" branch ONLY,
an EXISTING member's re-login is never gated.

OIDC uses a SECOND app instance (oidc_enabled=True, domain_mapping pinned to the primary
tenant) sharing the primary app's Postgres schema — mirrors tests/sso_oidc's own
bootstrap-then-reuse-engine idiom (conftest.build_oidc_app). SAML uses its own dedicated
app/client fixtures (saml_client/saml_db_session, static SP config) — mirrors
tests/saml_sso's own conftest.py.

RED until `assert_seat_available` is wired into `_get_or_provision_sso_user`'s
"provision new user" branch ONLY, and both oidc_router.py/saml_router.py translate
SeatCapExceededError -> ERR_PLAN_SEAT_CAP_EXCEEDED (403).
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.saml_sso.saml_fixtures import build_signed_response, generate_idp_keypair

from .conftest import (
    active_user_count,
    assert_problem,
    assign_plan,
    build_oidc_app,
    get_cookies_from_response,
    oidc_callback,
    put_saml_config,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
    start_saml_login_and_get_request_id,
    user_exists,
)

pytestmark = pytest.mark.asyncio


async def test_new_oidc_login_at_seat_cap_is_rejected(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'A brand-new SSO login at the tenant's seat cap is rejected' (R2, OIDC) —
    plan 'team' (seat_cap=20) with exactly 20 active users, an OIDC assertion for an email
    with NO existing users row -> 403 ERR_PLAN_SEAT_CAP_EXCEEDED; no users row inserted."""
    owner = await signup_owner(client, email="owner@oidcseatcap.example")
    plan_id = await seed_plan(db_session, name="team-oidc-atcap", seat_cap=20)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=19)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 20

    engine = app.state.engine  # type: ignore[attr-defined]
    _oidc_app, oidc_client = build_oidc_app(
        engine=engine,
        tenant_id=owner["tenant_id"],
        domain="oidcseatcap.example",
        id_token_email="newhire@oidcseatcap.example",
    )
    async with oidc_client:
        resp = await oidc_callback(oidc_client)

    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    assert not await user_exists(db_session, email="newhire@oidcseatcap.example")


async def test_existing_member_oidc_relogin_never_gated_by_seat_cap(
    app: object, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Scenario: 'An EXISTING member's SSO re-login is never gated by the seat cap' (M7) —
    same at-cap tenant, OIDC assertion for an email that ALREADY has a users row -> 200
    (login succeeds); assert_seat_available is never even reached (the existing-user
    branch returns first)."""
    owner = await signup_owner(client, email="owner@oidcrelogin.example")
    plan_id = await seed_plan(db_session, name="team-oidc-relogin", seat_cap=20)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    existing_emails = await seed_extra_active_users(
        db_session, tenant_id=owner["tenant_id"], count=19, email_prefix="oidcrelogin"
    )
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 20
    existing_email = existing_emails[0]
    domain = existing_email.split("@", 1)[-1]

    engine = app.state.engine  # type: ignore[attr-defined]
    _oidc_app, oidc_client = build_oidc_app(
        engine=engine, tenant_id=owner["tenant_id"], domain=domain, id_token_email=existing_email
    )
    async with oidc_client:
        resp = await oidc_callback(oidc_client)

    assert resp.status_code == 302, resp.text
    assert "ai_proxy_session" in get_cookies_from_response(resp)
    # Still exactly 20 — no NEW row was inserted for the existing user.
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 20


async def test_new_saml_login_at_seat_cap_is_rejected(
    saml_client: httpx.AsyncClient, saml_db_session: AsyncSession
) -> None:
    """Scenario: 'A brand-new SAML login at the tenant's seat cap is rejected
    identically' (R2, SAML) — same at-cap condition -> 403 ERR_PLAN_SEAT_CAP_EXCEEDED,
    identical in shape to the OIDC case."""
    owner = await signup_owner(saml_client, email="owner@samlseatcap.example")
    plan_id = await seed_plan(saml_db_session, name="team-saml-atcap", seat_cap=20)
    await assign_plan(saml_db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(saml_db_session, tenant_id=owner["tenant_id"], count=19)
    assert await active_user_count(saml_db_session, tenant_id=owner["tenant_id"]) == 20

    keypair = generate_idp_keypair()
    # SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
    # requires a verified claim first — precondition added, assertion intent unchanged.
    config_resp = await put_saml_config(
        saml_client,
        owner_token=owner["owner_token"],
        idp_x509_cert=keypair.cert_pem,
        idp_entity_id="https://fake-idp.test/entity-seatcap",
        idp_sso_url="https://fake-idp.test/sso-seatcap",
        email_domains=["samlseatcap.example"],
        db_session=saml_db_session,
    )
    assert config_resp.status_code == 200, config_resp.text
    config = config_resp.json()

    request_id = await start_saml_login_and_get_request_id(
        saml_client, domain="samlseatcap.example"
    )
    saml_response_b64 = build_signed_response(
        idp_entity_id="https://fake-idp.test/entity-seatcap",
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="newhire@samlseatcap.example",
    )

    resp = await saml_client.post(
        "/auth/saml/acs", data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert_problem(resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    assert not await user_exists(saml_db_session, email="newhire@samlseatcap.example")
