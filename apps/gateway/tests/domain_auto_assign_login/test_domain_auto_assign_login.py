"""Failing-first (RED) suite for domain-auto-assign-login (TASK.md §3 — FROZEN @ v1).

One test per §4 test-plan item (5 gateway items). Asserts OBSERVABLE behavior only:
the redirect Location's ?joined=1 query param, the stored user role, tenant count,
and (for the unit test) the repository's public return shape — never internals of
how the signal is threaded.

TRUE-RED RULE: every test fails NOW for a real structural reason —
`oidc_callback`/`saml_acs` do not append `?joined=1` today (no such code exists),
and `_get_or_provision_sso_user`/its wrappers return a bare `User`, not
`(User, newly_provisioned)` — never a collection/fixture error.

Shape decision (recorded per TASK.md §1 assumption + §3 note "Build MAY instead
carry the bit as a transient User attribute... observable contract is the ?joined
param, not the tuple"): test_newly_provisioned_signal pins the TUPLE-RETURN form
(`(User, bool)`) as §3's PRIMARY documented shape. If Build chooses the transient-
attribute alternative instead, this ONE unit test is the sanctioned-edit surface
(update the assertion to `user.newly_provisioned`) — every other test in this file
asserts only the HTTP-observable `?joined=1` contract and is unaffected either way.

Infrastructure: real Postgres (GATEWAY_TEST_DATABASE_URL) + real Redis (SAML
pending/replay stores) + httpx.ASGITransport — see conftest.py.
"""

from __future__ import annotations

import time
import urllib.parse
import uuid

import httpx
import jwt as pyjwt
import pytest
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.saml_sso.saml_fixtures import build_signed_response

from .conftest import (
    OIDC_CALLBACK,
    OIDC_LOGIN,
    SAML_ACS,
    SAML_LOGIN,
    FakeDnsResolver,
    claim_and_verify_domain,
    get_cookies_from_response,
    insert_oidc_config_row,
    put_saml_config_with_keypair,
    signup_and_login,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _FakeOidcExchanger:
    """Test seam for app.state.oidc_exchanger — returns a fixed id_token."""

    def __init__(self, id_token: str) -> None:
        self._id_token = id_token

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, str]:
        return {"id_token": self._id_token, "access_token": "fake-access", "token_type": "bearer"}


def _build_id_token(*, email: str, nonce: str, issuer: str, client_id: str) -> str:
    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "email": email,
            "iss": issuer,
            "aud": client_id,
            "iat": now,
            "exp": now + 3600,
            "nonce": nonce,
        },
        "hs256-signing-key-not-verified",  # noqa: S106 — v4 TLS-channel mode, signature unverified by design
        algorithm="HS256",
    )


def _extract_authn_request_id(location: str) -> str:
    """Parse the HTTP-Redirect-bound SAMLRequest param back into XML and return
    the AuthnRequest's @ID — mirrors tests/saml_sso/test_saml_sso.py's own helper."""
    parsed = urllib.parse.urlparse(location)
    qs = urllib.parse.parse_qs(parsed.query)
    saml_request = qs["SAMLRequest"][0]
    xml_bytes = OneLogin_Saml2_Utils.decode_base64_and_inflate(saml_request)
    doc = OneLogin_Saml2_XML.to_etree(xml_bytes)
    request_id: str = doc.get("ID")
    return request_id


async def _user_row(db_session: AsyncSession, email: str) -> dict[str, object] | None:
    row = (
        (
            await db_session.execute(
                text("SELECT role, tenant_id FROM users WHERE email = :e"),
                {"e": email},
            )
        )
        .mappings()
        .fetchone()
    )
    return dict(row) if row is not None else None


async def _tenant_count(db_session: AsyncSession) -> int:
    return (await db_session.execute(text("SELECT COUNT(*) FROM tenants"))).scalar_one()


def _has_joined_param(location: str) -> bool:
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(location).query)
    return qs.get("joined") == ["1"]


# ===========================================================================
# 1. First OIDC login into a verified-domain tenant signals joined  (M1, M2)
# ===========================================================================


async def test_oidc_first_login_redirect_has_joined_param(
    app: object,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
) -> None:
    # covers: M1,M2 — new verified-domain OIDC user's callback redirect carries ?joined=1,
    # the user is provisioned MEMBER, and no new tenant is created (R3).
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-join-oidc.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-join-oidc.com"
    )
    await insert_oidc_config_row(
        db_session, tenant_id=tenant_a, email_domains=["acme-join-oidc.com"], jwks_url=""
    )
    tenant_count_before = await _tenant_count(db_session)

    login_resp = await client.get(
        OIDC_LOGIN, params={"domain": "acme-join-oidc.com"}, follow_redirects=False
    )
    assert login_resp.status_code == 302, login_resp.text
    login_cookies = get_cookies_from_response(login_resp)

    id_token = _build_id_token(
        email="newhire@acme-join-oidc.com",
        nonce=login_cookies["oidc_nonce"],
        issuer="https://idp.test",
        client_id="test-client-id",
    )
    app.state.oidc_exchanger = _FakeOidcExchanger(id_token)  # type: ignore[attr-defined]

    callback_resp = await client.get(
        f"{OIDC_CALLBACK}?code=fake-code&state={login_cookies['oidc_state']}",
        cookies=login_cookies,
        follow_redirects=False,
    )

    assert callback_resp.status_code == 302, callback_resp.text
    location = callback_resp.headers["location"]
    assert _has_joined_param(location), f"expected ?joined=1 in redirect Location, got {location!r}"

    row = await _user_row(db_session, "newhire@acme-join-oidc.com")
    assert row is not None, "the new user must have been provisioned"
    assert row["role"] == "member"
    assert str(row["tenant_id"]) == str(tenant_a)

    tenant_count_after = await _tenant_count(db_session)
    assert tenant_count_after == tenant_count_before, (
        "an SSO login must never create a new tenant (R3) — joined always means "
        "joined an EXISTING tenant"
    )


# ===========================================================================
# 2. Returning OIDC user is not signalled as joined  (R1)
# ===========================================================================


async def test_oidc_returning_user_no_joined_param(
    app: object,
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    fake_dns: FakeDnsResolver,
) -> None:
    # covers: R1 — a SECOND OIDC login by the same already-provisioned user must NOT
    # carry ?joined=1, and their stored role must be unchanged.
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-return-oidc.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-return-oidc.com"
    )
    await insert_oidc_config_row(
        db_session, tenant_id=tenant_a, email_domains=["acme-return-oidc.com"], jwks_url=""
    )
    email = "returning@acme-return-oidc.com"

    async def _do_login_and_callback() -> httpx.Response:
        login_resp = await client.get(
            OIDC_LOGIN, params={"domain": "acme-return-oidc.com"}, follow_redirects=False
        )
        assert login_resp.status_code == 302, login_resp.text
        cookies = get_cookies_from_response(login_resp)
        id_token = _build_id_token(
            email=email,
            nonce=cookies["oidc_nonce"],
            issuer="https://idp.test",
            client_id="test-client-id",
        )
        app.state.oidc_exchanger = _FakeOidcExchanger(id_token)  # type: ignore[attr-defined]
        return await client.get(
            f"{OIDC_CALLBACK}?code=fake-code&state={cookies['oidc_state']}",
            cookies=cookies,
            follow_redirects=False,
        )

    first = await _do_login_and_callback()
    assert first.status_code == 302, first.text
    assert _has_joined_param(first.headers["location"]), "first login must be signalled joined"

    second = await _do_login_and_callback()
    assert second.status_code == 302, second.text
    assert not _has_joined_param(second.headers["location"]), (
        f"a returning user's login must NOT carry ?joined=1, got {second.headers['location']!r}"
    )

    row = await _user_row(db_session, email)
    assert row is not None
    assert row["role"] == "member", "a returning user's stored role must never change on re-login"


# ===========================================================================
# 3. First SAML login signals joined  (M3)
# ===========================================================================


async def test_saml_first_login_redirect_has_joined_param(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    # covers: M3 — a first-time SAML user's ACS redirect carries ?joined=1.
    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-join-saml.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-join-saml.com"
    )
    put_resp, keypair = await put_saml_config_with_keypair(
        client, owner_token=token_a, email_domains=["acme-join-saml.com"]
    )
    assert put_resp.status_code == 200, put_resp.text
    config = put_resp.json()

    login_resp = await client.get(
        SAML_LOGIN, params={"domain": "acme-join-saml.com"}, follow_redirects=False
    )
    assert login_resp.status_code == 302, login_resp.text
    request_id = _extract_authn_request_id(login_resp.headers["location"])

    saml_response_b64 = build_signed_response(
        idp_entity_id="https://fake-idp.test/entity",
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="newhire@acme-join-saml.com",
    )

    acs_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert acs_resp.status_code == 302, acs_resp.text
    location = acs_resp.headers["location"]
    assert _has_joined_param(location), f"expected ?joined=1 in ACS redirect, got {location!r}"

    row = await _user_row(db_session, "newhire@acme-join-saml.com")
    assert row is not None
    assert row["role"] == "member"
    assert str(row["tenant_id"]) == str(tenant_a)


# ===========================================================================
# 4. An auth-failure never emits joined  (R2)
# ===========================================================================


async def test_sso_auth_failure_no_joined_param(
    client: httpx.AsyncClient, fake_dns: FakeDnsResolver
) -> None:
    # covers: R2 — a failed OIDC callback and a failed SAML ACS call both reject
    # with a 4xx problem+json body, never a 302, never a ?joined param, and never
    # a token anywhere in the response.
    oidc_resp = await client.get(
        f"{OIDC_CALLBACK}?code=some-code&state=query-state-does-not-match",
        cookies={"oidc_state": "cookie-state-value", "oidc_nonce": "n1"},
        follow_redirects=False,
    )
    assert 400 <= oidc_resp.status_code < 500, oidc_resp.text
    assert "location" not in oidc_resp.headers
    oidc_body = oidc_resp.text
    assert "joined" not in oidc_body
    assert "ai_proxy_session" not in oidc_body

    saml_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": "bm90LWEtcmVhbC1zYW1sLXJlc3BvbnNl"}, follow_redirects=False
    )
    assert 400 <= saml_resp.status_code < 500, saml_resp.text
    assert "location" not in saml_resp.headers
    saml_body = saml_resp.text
    assert "joined" not in saml_body
    assert "ai_proxy_session" not in saml_body


# ===========================================================================
# 5. _get_or_provision_sso_user signals newly_provisioned  (M1, unit-level)
# ===========================================================================


async def test_newly_provisioned_signal(
    client: httpx.AsyncClient, db_session: AsyncSession, fake_dns: FakeDnsResolver
) -> None:
    # covers: M1 — the shared JIT-provisioning helper (exercised here via its public
    # get_or_provision_oidc_user wrapper) must expose whether THIS call inserted a
    # brand-new user (True) vs matched an existing one (False). Existing role/tenant
    # behavior stays byte-identical — only the additional bit is new.
    from gateway.auth.application.use_cases import SSO_PASSWORD_HASH_SENTINEL
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    tenant_a, token_a = await signup_and_login(
        client, tenant_name="Acme", email="owner@acme-unit-provision.com"
    )
    await claim_and_verify_domain(
        client, fake_dns, owner_token=token_a, domain="acme-unit-provision.com"
    )
    repo = SqlAlchemyIdentityRepository(db_session)
    email = "unit-provision@acme-unit-provision.com"

    first_user, first_newly_provisioned = await repo.get_or_provision_oidc_user(
        email=email, tenant_id=tenant_a, password_hash=SSO_PASSWORD_HASH_SENTINEL
    )
    assert first_newly_provisioned is True, "inserting a brand-new SSO user must signal True"
    assert first_user.tenant_id == tenant_a
    assert first_user.role.value == "member"

    second_user, second_newly_provisioned = await repo.get_or_provision_oidc_user(
        email=email, tenant_id=tenant_a, password_hash=SSO_PASSWORD_HASH_SENTINEL
    )
    assert second_newly_provisioned is False, "matching an EXISTING user must signal False"
    assert second_user.id == first_user.id
