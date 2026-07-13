"""Failing-first (RED) suite for saml-sso (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario, plus supplementary tests for every §2 Reject-list
error code not already covered by a named scenario (M4/M5 signature-,
issuer-, and audience-mismatch; response-mismatch; SSRF URL validation;
config-not-found; non-owner GET).

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW because the
routes/tables do not exist yet (404 without the ERR_SAML_* code, or a bare
FastAPI validation error) — not because the test harness itself is broken.

Infrastructure: real Postgres + real Redis (see conftest.py) + httpx.ASGITransport.
Assertions are REAL, cryptographically signed via python3-saml/xmlsec (see
saml_fixtures.py) — this suite exercises the production signature-verification
path for real, never a stubbed-out "assume it's valid" shortcut.
"""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

import httpx
import jwt as pyjwt
from onelogin.saml2.utils import OneLogin_Saml2_Utils
from onelogin.saml2.xml_utils import OneLogin_Saml2_XML
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.saml_sso.saml_fixtures import (
    AssertionSpec,
    build_saml_response_b64,
    build_signed_response,
    generate_idp_keypair,
)
from tests import _redis_env

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
SAML_LOGIN = "/auth/saml/login"
SAML_ACS = "/auth/saml/acs"
SAML_ADMIN = "/admin/saml"

# ---------------------------------------------------------------------------
# Fake IdP parameters — deterministic test values
# ---------------------------------------------------------------------------
IDP_ENTITY_ID = "https://fake-idp.test/entity"
IDP_SSO_URL = "https://fake-idp.test/sso"
DOMAIN = "acme.com"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def signup_tenant(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


def get_cookies_from_response(resp: httpx.Response) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        parts = header.split(";")
        if parts:
            name_value = parts[0].strip()
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                cookies[name.strip()] = value.strip()
    return cookies


def get_cookie_attributes(resp: httpx.Response, cookie_name: str) -> str:
    for header in resp.headers.get_list("set-cookie"):
        if header.split(";")[0].strip().startswith(cookie_name + "="):
            return header
    return ""


def extract_authn_request_id(location: str) -> str:
    """Parse the HTTP-Redirect-bound SAMLRequest param back into XML and
    return the AuthnRequest's @ID — mirrors what a real IdP does."""
    parsed = urllib.parse.urlparse(location)
    qs = urllib.parse.parse_qs(parsed.query)
    saml_request = qs["SAMLRequest"][0]
    xml_bytes = OneLogin_Saml2_Utils.decode_base64_and_inflate(saml_request)
    doc = OneLogin_Saml2_XML.to_etree(xml_bytes)
    request_id: str = doc.get("ID")
    return request_id


async def put_saml_config(
    client: httpx.AsyncClient,
    *,
    owner_token: str,
    idp_x509_cert: str,
    idp_entity_id: str = IDP_ENTITY_ID,
    idp_sso_url: str = IDP_SSO_URL,
    email_domains: list[str] | None = None,
    email_attribute_name: str | None = None,
    enabled: bool = True,
) -> httpx.Response:
    body: dict[str, Any] = {
        "idp_entity_id": idp_entity_id,
        "idp_sso_url": idp_sso_url,
        "idp_x509_cert": idp_x509_cert,
        "email_domains": email_domains if email_domains is not None else [DOMAIN],
        "enabled": enabled,
    }
    if email_attribute_name is not None:
        body["email_attribute_name"] = email_attribute_name
    return await client.put(SAML_ADMIN, json=body, headers=auth_jwt(owner_token))


async def seed_saml_config(client: httpx.AsyncClient, *, owner_token: str) -> tuple[dict, Any]:
    """Seed a valid, enabled SAML config for the owner's tenant; returns
    (response_json, IdpKeyPair used to sign real assertions in this test)."""
    keypair = generate_idp_keypair()
    resp = await put_saml_config(client, owner_token=owner_token, idp_x509_cert=keypair.cert_pem)
    assert resp.status_code == 200, f"seed PUT /admin/saml failed: {resp.text}"
    return resp.json(), keypair


async def start_login_and_get_request_id(client: httpx.AsyncClient, *, domain: str = DOMAIN) -> str:
    resp = await client.get(SAML_LOGIN, params={"domain": domain}, follow_redirects=False)
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    return extract_authn_request_id(resp.headers["location"])


async def count_users_by_email(session: AsyncSession, email: str) -> int:
    row = (
        await session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :email"),
            {"email": email.lower()},
        )
    ).fetchone()
    assert row is not None
    return int(row[0])


async def latest_audit_row(session: AsyncSession, *, action: str) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT result, actor_email, metadata, tenant_id FROM audit_events "
                    "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
                ),
                {"action": action},
            )
        )
        .mappings()
        .fetchone()
    )
    return dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# S1 — SP-initiated login redirects with a pinned pending request (M1)
# ---------------------------------------------------------------------------


async def test_login_redirects_with_pinned_pending_request(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.com"
    )
    config, _keypair = await seed_saml_config(client, owner_token=owner_token)

    resp = await client.get(SAML_LOGIN, params={"domain": DOMAIN}, follow_redirects=False)

    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(IDP_SSO_URL)
    assert "SAMLRequest=" in location
    assert get_cookies_from_response(resp) == {}, "no ai_proxy_session cookie should be set yet"

    request_id = extract_authn_request_id(location)
    assert request_id

    import redis.asyncio as aioredis

    r = aioredis.from_url(_redis_env.TEST_REDIS_URL)
    try:
        ttl = await r.ttl(f"saml:pending:{request_id}")
        assert 0 < ttl <= 300, f"expected TTL in (0, 300], got {ttl}"
        raw = await r.get(f"saml:pending:{request_id}")
        assert raw is not None
        raw_text = raw.decode() if isinstance(raw, bytes) else raw
        assert config["sp_entity_id"] in raw_text
    finally:
        await r.aclose()


# ---------------------------------------------------------------------------
# S2 — sp_entity_id is server-derived and not admin-settable (M2)
# ---------------------------------------------------------------------------


async def test_sp_entity_id_is_server_derived_not_admin_settable(
    client: httpx.AsyncClient,
) -> None:
    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner2@acme.com"
    )
    keypair = generate_idp_keypair()
    resp = await client.put(
        SAML_ADMIN,
        json={
            "idp_entity_id": IDP_ENTITY_ID,
            "idp_sso_url": IDP_SSO_URL,
            "idp_x509_cert": keypair.cert_pem,
            "email_domains": [DOMAIN],
            "sp_entity_id": "https://evil.example/sp",  # attacker-supplied, must be ignored
        },
        headers=auth_jwt(owner_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sp_entity_id"] != "https://evil.example/sp"
    assert body["sp_entity_id"].endswith(f"/tenant/{tenant_id}")

    # GET confirms the same server-derived value persisted, not the attacker's.
    get_resp = await client.get(SAML_ADMIN, headers=auth_jwt(owner_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["sp_entity_id"] == body["sp_entity_id"]


# ---------------------------------------------------------------------------
# S3 — ACS resolves tenant via the pending-request store, never a cookie (M3)
# ---------------------------------------------------------------------------


async def test_acs_resolves_tenant_via_pending_store_not_cookie(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner3@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user3@acme.com",
    )

    # No cookies at all — simulates a cross-site POST navigation dropping SameSite=Lax.
    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies

    # Single-use: the pending record is gone (deleted by the same GETDEL call).
    import redis.asyncio as aioredis

    r = aioredis.from_url(_redis_env.TEST_REDIS_URL)
    try:
        assert await r.get(f"saml:pending:{request_id}") is None
    finally:
        await r.aclose()

    assert await count_users_by_email(db_session, "user3@acme.com") == 1


# ---------------------------------------------------------------------------
# S4 — XSW-style forged unsigned block is rejected via reference-based
#      extraction (M4)
# ---------------------------------------------------------------------------


async def test_xsw_forged_unsigned_block_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner4@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    spec = AssertionSpec(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        subject_nameid="user4@acme.com",
        sign=True,
        sign_with=keypair,
        inject_xsw_evil_nameid="admin4@acme.com",
    )
    xsw_response_b64 = build_saml_response_b64(spec)

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": xsw_response_b64}, follow_redirects=False
    )

    # Either outright rejected, or (never) succeeds AS admin4@acme.com.
    if resp.status_code == 302:
        cookies = get_cookies_from_response(resp)
        token = cookies.get("ai_proxy_session")
        assert token is not None
        claims = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
        assert claims["email"] != "admin4@acme.com"
    else:
        assert resp.status_code in (400, 401), resp.text

    assert await count_users_by_email(db_session, "admin4@acme.com") == 0


# ---------------------------------------------------------------------------
# S5 — full validation set passes end-to-end and mints the same JWT shape as
#      OIDC (M5, M8)
# ---------------------------------------------------------------------------


async def test_full_validation_mints_same_jwt_shape_as_oidc(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner5@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user5@acme.com",
    )

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert resp.status_code == 302, resp.text
    cookie_header = get_cookie_attributes(resp, "ai_proxy_session")
    assert "HttpOnly" in cookie_header
    assert (
        "SameSite=strict" in cookie_header.lower().replace("samesite=strict", "SameSite=strict")
        or "samesite=strict" in cookie_header.lower()
    )
    assert "Path=/" in cookie_header
    assert "Max-Age=" in cookie_header

    token = get_cookies_from_response(resp)["ai_proxy_session"]
    claims = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["email"] == "user5@acme.com"
    assert claims["tenant_id"] == tenant_id
    assert claims["role"] == "member"
    assert "sub" in claims  # JwtTokenService.issue()'s user-id claim (jwt_service.py)


# ---------------------------------------------------------------------------
# S6 — IdP-initiated (unsolicited) response is rejected (M6, R1)
# ---------------------------------------------------------------------------


async def test_idp_initiated_unsolicited_response_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner6@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    spec = AssertionSpec(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=None,  # no InResponseTo at all
        subject_nameid="unsolicited6@acme.com",
        sign=True,
        sign_with=keypair,
    )
    unsolicited_b64 = build_saml_response_b64(spec)

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": unsolicited_b64}, follow_redirects=False
    )

    assert_problem(resp, 400, "ERR_SAML_REQUEST_NOT_FOUND")
    assert await count_users_by_email(db_session, "unsolicited6@acme.com") == 0


# ---------------------------------------------------------------------------
# S7 — JIT-provisioned SAML user is always role=member (M7)
# ---------------------------------------------------------------------------


async def test_jit_provisioned_user_is_always_member(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner7@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="newhire7@acme.com",
    )

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert resp.status_code == 302, resp.text

    row = (
        (
            await db_session.execute(
                text("SELECT role, auth_method, password_hash FROM users WHERE email = :e"),
                {"e": "newhire7@acme.com"},
            )
        )
        .mappings()
        .fetchone()
    )
    assert row is not None
    assert row["role"] == "member"
    assert row["auth_method"] == "saml"
    assert row["password_hash"] == "!sso-no-password"

    token = get_cookies_from_response(resp)["ai_proxy_session"]
    claims = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["role"] == "member"


# ---------------------------------------------------------------------------
# S8 — existing admin's stored role survives a SAML login (M7)
# ---------------------------------------------------------------------------


async def test_existing_admin_role_survives_saml_login(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner8@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    # Promote a second user to admin@acme.com via a legitimate admin action:
    # signup as a distinct tenant is not possible (email uniqueness is global
    # in this schema), so seed the row directly and promote via SQL — the
    # scenario's precondition is "an existing user with role=ADMIN", the
    # mechanism by which that row got there is out of this test's scope.
    import uuid

    admin_user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method) "
            "VALUES (:id, :tenant_id, :email, :ph, 'admin', 'password')"
        ),
        {
            "id": admin_user_id,
            "tenant_id": tenant_id,
            "email": "admin8@acme.com",
            "ph": "irrelevant-hash",
        },
    )
    await db_session.commit()

    request_id = await start_login_and_get_request_id(client)
    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="admin8@acme.com",
    )

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert resp.status_code == 302, resp.text

    row = (
        (
            await db_session.execute(
                text("SELECT role FROM users WHERE email = :e"), {"e": "admin8@acme.com"}
            )
        )
        .mappings()
        .fetchone()
    )
    assert row is not None
    assert row["role"] == "admin", "existing role must NOT be downgraded"

    token = get_cookies_from_response(resp)["ai_proxy_session"]
    claims = pyjwt.decode(token, TEST_JWT_SECRET, algorithms=["HS256"])
    assert claims["role"] == "admin"


# ---------------------------------------------------------------------------
# S9 — every /acs outcome is audited, success and rejection alike (M9)
# ---------------------------------------------------------------------------


async def test_every_acs_outcome_is_audited(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner9@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    request_id = await start_login_and_get_request_id(client)
    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user9@acme.com",
    )
    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert resp.status_code == 302, resp.text

    # Fire-and-forget audit write — give the event loop a beat to schedule it.
    row: dict[str, Any] | None = None
    for _ in range(20):
        row = await latest_audit_row(db_session, action="auth.saml_login")
        if row is not None and row["result"] == "success":
            break
        await asyncio.sleep(0.05)
    assert row is not None
    assert row["result"] == "success"
    assert row["actor_email"] == "user9@acme.com"
    assert "assertion" not in str(row["metadata"]).lower() or "SAMLResponse" not in str(
        row["metadata"]
    )

    # Rejection path: expired assertion.
    request_id2 = await start_login_and_get_request_id(client)
    expired_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id2,
        keypair=keypair,
        subject_email="user9@acme.com",
        not_before_offset_seconds=-600,
        not_on_or_after_offset_seconds=-90,
    )
    resp2 = await client.post(SAML_ACS, data={"SAMLResponse": expired_b64}, follow_redirects=False)
    assert_problem(resp2, 401, "ERR_SAML_ASSERTION_EXPIRED")

    row2: dict[str, Any] | None = None
    for _ in range(20):
        row2 = await latest_audit_row(db_session, action="auth.saml_login")
        if row2 is not None and row2["result"] == "rejected":
            break
        await asyncio.sleep(0.05)
    assert row2 is not None
    assert row2["result"] == "rejected"
    assert row2["metadata"].get("error_code") == "ERR_SAML_ASSERTION_EXPIRED"


# ---------------------------------------------------------------------------
# S10 — admin config CRUD is owner-only and validates the IdP cert (M10)
# ---------------------------------------------------------------------------


async def test_admin_config_owner_only_and_validates_cert(client: httpx.AsyncClient) -> None:
    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner10@acme.com"
    )
    # Create a member (non-owner) in the SAME tenant via direct member add is not
    # exposed generically here; simplest byte-identical-to-OIDC-suite path: a
    # second signup creates a SEPARATE tenant whose owner is not OWNER of tenant_id,
    # but the FORBIDDEN check is role-based on the CALLER's own tenant regardless —
    # any non-owner-role bearer token is rejected identically. Use a fresh signup's
    # owner token is still role=owner of ITS OWN tenant, so to get a genuine
    # role=member token we invite+accept is out of scope; instead assert the
    # role-check directly by minting a member token isn't available without extra
    # plumbing — cover the owner-role gate via a DIFFERENT tenant's owner token,
    # which the identity/tenant-scoped GetIdentityUseCase resolves as owner of a
    # DIFFERENT tenant (still role=owner, so this does NOT exercise the 403 path).
    #
    # The gate under test is `identity.role != Role.OWNER`, which is role-based,
    # not tenant-based — so the only way to hit it via HTTP is a genuine
    # role=member bearer token. We provision one directly via SQL + issue a real
    # session JWT through the SAME TokenService the app uses, keeping the test at
    # the HTTP boundary (not calling the router function directly).
    import uuid

    member_user_id = uuid.uuid4()

    async def _seed_member(db_session: AsyncSession) -> None:
        await db_session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method) "
                "VALUES (:id, :tenant_id, :email, 'x', 'member', 'password')"
            ),
            {"id": member_user_id, "tenant_id": tenant_id, "email": "member10@acme.com"},
        )
        await db_session.commit()

    # db_session fixture isn't a parameter here; use the app's sessionmaker via client.app.
    app = client._transport.app  # type: ignore[attr-defined]
    async with app.state.sessionmaker() as session:
        await _seed_member(session)

    member_jwt, _expires = app.state.token_service.issue(
        user_id=member_user_id,
        tenant_id=__import__("uuid").UUID(tenant_id),
        role=__import__("gateway.tenants.domain.entities", fromlist=["Role"]).Role.MEMBER,
        email="member10@acme.com",
    )

    keypair = generate_idp_keypair()
    resp = await put_saml_config(client, owner_token=member_jwt, idp_x509_cert=keypair.cert_pem)
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    get_resp = await client.get(SAML_ADMIN, headers=auth_jwt(member_jwt))
    assert_problem(get_resp, 403, "ERR_AUTH_FORBIDDEN")

    # Invalid cert, as owner.
    bad_cert_resp = await put_saml_config(
        client, owner_token=owner_token, idp_x509_cert="not a real PEM cert"
    )
    assert_problem(bad_cert_resp, 422, "ERR_SAML_CERT_INVALID")


# ---------------------------------------------------------------------------
# S11 — no env-Settings fallback exists for SAML (M11)
# ---------------------------------------------------------------------------


async def test_no_env_settings_fallback(client: httpx.AsyncClient) -> None:
    resp = await client.get(
        SAML_LOGIN, params={"domain": "unknown-domain.com"}, follow_redirects=False
    )
    assert_problem(resp, 404, "ERR_SAML_NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# S12 — Redis unavailability fails the login closed, not open (M12)
# ---------------------------------------------------------------------------


async def test_redis_unavailable_fails_closed(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner12@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user12@acme.com",
    )

    class _BrokenRedis:
        async def getdel(self, *_a: object, **_kw: object) -> None:
            raise ConnectionError("simulated redis outage")

        async def set(self, *_a: object, **_kw: object) -> None:
            raise ConnectionError("simulated redis outage")

        async def exists(self, *_a: object, **_kw: object) -> None:
            raise ConnectionError("simulated redis outage")

    app = client._transport.app  # type: ignore[attr-defined]
    from gateway.auth.infrastructure.saml_request_store import RedisSamlRequestStore

    app.state.saml_request_store = RedisSamlRequestStore(redis=_BrokenRedis())

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert_problem(resp, 503, "ERR_SAML_STORE_UNAVAILABLE")
    assert await count_users_by_email(db_session, "user12@acme.com") == 0


# ---------------------------------------------------------------------------
# S13 — email resolution falls through NameID -> configured attribute ->
#       fallback search (M13)
# ---------------------------------------------------------------------------


async def test_email_resolution_falls_through_to_configured_attribute(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner13@acme.com"
    )
    keypair = generate_idp_keypair()
    # Do NOT set email_attribute_name -> uses the ADFS/Azure AD default.
    resp = await put_saml_config(client, owner_token=owner_token, idp_x509_cert=keypair.cert_pem)
    assert resp.status_code == 200, resp.text
    config = resp.json()

    request_id = await start_login_and_get_request_id(client)
    adfs_attr = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
    saml_response_b64 = build_saml_response_b64(
        AssertionSpec(
            idp_entity_id=IDP_ENTITY_ID,
            sp_entity_id=config["sp_entity_id"],
            acs_url=config["acs_url"],
            request_id=request_id,
            subject_nameid="not-an-email-nameid",
            subject_nameid_format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
            attributes={adfs_attr: ["user13@acme.com"]},
            sign=True,
            sign_with=keypair,
        )
    )

    login_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert login_resp.status_code == 302, login_resp.text
    assert await count_users_by_email(db_session, "user13@acme.com") == 1


async def test_email_resolution_all_three_fail_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner13b@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    request_id = await start_login_and_get_request_id(client)
    saml_response_b64 = build_saml_response_b64(
        AssertionSpec(
            idp_entity_id=IDP_ENTITY_ID,
            sp_entity_id=config["sp_entity_id"],
            acs_url=config["acs_url"],
            request_id=request_id,
            subject_nameid="opaque-subject-id",
            subject_nameid_format="urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
            attributes={"unrelated_attr": ["not-an-email"]},
            sign=True,
            sign_with=keypair,
        )
    )

    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert_problem(resp, 401, "ERR_SAML_EMAIL_MISSING")


# ---------------------------------------------------------------------------
# S14 — request-store replay is rejected — same request_id used twice
# ---------------------------------------------------------------------------


async def test_request_store_replay_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner14@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user14@acme.com",
    )

    first = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert first.status_code == 302, first.text

    second = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert_problem(second, 400, "ERR_SAML_REQUEST_ALREADY_USED")

    assert await count_users_by_email(db_session, "user14@acme.com") == 1


# ---------------------------------------------------------------------------
# S15 — assertion-ID replay against a DIFFERENT pending request (M5.6)
# ---------------------------------------------------------------------------


async def test_assertion_replay_against_different_pending_request(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner15@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    request_id_1 = await start_login_and_get_request_id(client)
    spec = AssertionSpec(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id_1,
        subject_nameid="user15@acme.com",
        sign=True,
        sign_with=keypair,
    )
    original_b64 = build_saml_response_b64(spec)
    first = await client.post(SAML_ACS, data={"SAMLResponse": original_b64}, follow_redirects=False)
    assert first.status_code == 302, first.text

    request_id_2 = await start_login_and_get_request_id(client)
    # Re-wrap the SAME signed assertion under a NEW outer envelope claiming
    # InResponseTo=request_id_2 (top-level, unverified) — the signed
    # SubjectConfirmationData/@InResponseTo still says request_id_1.
    tampered_spec = AssertionSpec(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id_2,  # only affects the outer Response InResponseTo
        subject_nameid="user15@acme.com",
        assertion_id=spec.assertion_id,
        sign=True,
        sign_with=keypair,
        scd_in_response_to_override=request_id_1,  # signed value stays request_id_1
    )
    replay_b64 = build_saml_response_b64(tampered_spec)

    second = await client.post(SAML_ACS, data={"SAMLResponse": replay_b64}, follow_redirects=False)
    assert second.status_code in (400, 401), second.text
    if second.status_code == 401:
        body = second.json()
        assert body["code"] in (
            "ERR_SAML_RESPONSE_MISMATCH",
            "ERR_SAML_SIGNATURE_INVALID",
            "ERR_SAML_ASSERTION_REPLAYED",
        )

    # At most one session was ever minted for this assertion.
    assert await count_users_by_email(db_session, "user15@acme.com") == 1


# ---------------------------------------------------------------------------
# S16 — cross-tenant email conflict is rejected like OIDC's
# ---------------------------------------------------------------------------


async def test_cross_tenant_email_conflict_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    # Seed user@partner.com as a member of a DIFFERENT tenant "beta".
    _beta_owner_token, beta_tenant_id = await signup_tenant(
        client, tenant_name="Beta", email="owner-beta16@partner.com"
    )
    import uuid as _uuid

    partner_user_id = _uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method) "
            "VALUES (:id, :tenant_id, :email, 'x', 'member', 'password')"
        ),
        {"id": partner_user_id, "tenant_id": beta_tenant_id, "email": "user16@partner.com"},
    )
    await db_session.commit()

    # acme's admin misconfigures email_domains to include partner.com.
    acme_owner_token, _acme_tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner16@acme.com"
    )
    keypair = generate_idp_keypair()
    resp = await put_saml_config(
        client,
        owner_token=acme_owner_token,
        idp_x509_cert=keypair.cert_pem,
        email_domains=[DOMAIN, "partner.com"],
    )
    assert resp.status_code == 200, resp.text
    config = resp.json()

    request_id = await start_login_and_get_request_id(client, domain="partner.com")
    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user16@partner.com",
    )

    login_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert_problem(login_resp, 403, "ERR_SAML_TENANT_CONFLICT")

    # Original beta row unchanged; no new row created.
    row = (
        (
            await db_session.execute(
                text("SELECT tenant_id FROM users WHERE email = :e"), {"e": "user16@partner.com"}
            )
        )
        .mappings()
        .fetchone()
    )
    assert row is not None
    assert str(row["tenant_id"]) == beta_tenant_id
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :e"), {"e": "user16@partner.com"}
        )
    ).scalar_one()
    assert count == 1


# ---------------------------------------------------------------------------
# S17 — clock-skew boundary is honored, not just "sometime around now"
# ---------------------------------------------------------------------------


async def test_clock_skew_boundary_honored(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner17@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)

    # Inside the default 60s skew: NotOnOrAfter 45s in the past -> SUCCESS.
    request_id_ok = await start_login_and_get_request_id(client)
    within_skew_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id_ok,
        keypair=keypair,
        subject_email="user17a@acme.com",
        not_before_offset_seconds=-300,
        not_on_or_after_offset_seconds=-45,
    )
    ok_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": within_skew_b64}, follow_redirects=False
    )
    assert ok_resp.status_code == 302, ok_resp.text

    # Outside the 60s allowance: NotOnOrAfter 90s in the past -> REJECTED.
    request_id_bad = await start_login_and_get_request_id(client)
    outside_skew_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id_bad,
        keypair=keypair,
        subject_email="user17b@acme.com",
        not_before_offset_seconds=-300,
        not_on_or_after_offset_seconds=-90,
    )
    bad_resp = await client.post(
        SAML_ACS, data={"SAMLResponse": outside_skew_b64}, follow_redirects=False
    )
    assert_problem(bad_resp, 401, "ERR_SAML_ASSERTION_EXPIRED")


# ---------------------------------------------------------------------------
# S18 — concurrent double-submit of the same assertion is serialized safely
# ---------------------------------------------------------------------------


async def test_concurrent_double_submit_serialized_safely(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner18@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user18@acme.com",
    )

    results = await asyncio.gather(
        client.post(SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False),
        client.post(SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False),
    )

    statuses = sorted(r.status_code for r in results)
    assert statuses == [302, 400], f"expected exactly one 302 and one 400, got {statuses}"

    assert await count_users_by_email(db_session, "user18@acme.com") == 1


# ---------------------------------------------------------------------------
# S19 — a tenant that never configures SAML is fully unaffected
# ---------------------------------------------------------------------------


async def test_unconfigured_tenant_fully_unaffected(client: httpx.AsyncClient) -> None:
    _owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="LegacyCo", email="owner19@legacy-co.com"
    )
    # Existing password login path untouched.
    login_resp = await client.post(
        LOGIN, json={"email": "owner19@legacy-co.com", "password": "correct horse battery staple"}
    )
    assert login_resp.status_code == 200

    resp = await client.get(SAML_LOGIN, params={"domain": "legacy-co.com"}, follow_redirects=False)
    assert_problem(resp, 404, "ERR_SAML_NOT_CONFIGURED")


# ---------------------------------------------------------------------------
# Supplementary Reject-list coverage not already exercised by a named scenario
# ---------------------------------------------------------------------------


async def test_wrong_signing_key_rejected_signature_invalid(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner20@acme.com"
    )
    config, _keypair = await seed_saml_config(client, owner_token=owner_token)
    other_keypair = generate_idp_keypair()  # NOT the cert stored for this tenant

    request_id = await start_login_and_get_request_id(client)
    bad_sig_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=other_keypair,
        subject_email="user20@acme.com",
    )

    resp = await client.post(SAML_ACS, data={"SAMLResponse": bad_sig_b64}, follow_redirects=False)
    assert_problem(resp, 401, "ERR_SAML_SIGNATURE_INVALID")
    assert await count_users_by_email(db_session, "user20@acme.com") == 0


async def test_issuer_mismatch_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner21@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user21@acme.com",
        issuer_override="https://a-different-idp.example/entity",
    )
    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert_problem(resp, 401, "ERR_SAML_ISSUER_MISMATCH")
    assert await count_users_by_email(db_session, "user21@acme.com") == 0


async def test_audience_mismatch_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner22@acme.com"
    )
    config, keypair = await seed_saml_config(client, owner_token=owner_token)
    request_id = await start_login_and_get_request_id(client)

    saml_response_b64 = build_signed_response(
        idp_entity_id=IDP_ENTITY_ID,
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email="user22@acme.com",
        audience_override="https://a-different-sp.example",
    )
    resp = await client.post(
        SAML_ACS, data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )
    assert_problem(resp, 401, "ERR_SAML_AUDIENCE_MISMATCH")
    assert await count_users_by_email(db_session, "user22@acme.com") == 0


async def test_put_admin_saml_rejects_non_https_sso_url(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner23@acme.com"
    )
    keypair = generate_idp_keypair()
    resp = await client.put(
        SAML_ADMIN,
        json={
            "idp_entity_id": IDP_ENTITY_ID,
            "idp_sso_url": "http://fake-idp.test/sso",  # non-https
            "idp_x509_cert": keypair.cert_pem,
            "email_domains": [DOMAIN],
        },
        headers=auth_jwt(owner_token),
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert "detail" in body
    assert any("idp_sso_url" in str(e.get("loc", [])) for e in body["detail"])


async def test_put_admin_saml_rejects_private_ip_sso_url(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner24@acme.com"
    )
    keypair = generate_idp_keypair()
    resp = await client.put(
        SAML_ADMIN,
        json={
            "idp_entity_id": IDP_ENTITY_ID,
            "idp_sso_url": "https://10.0.0.5/sso",  # private IP
            "idp_x509_cert": keypair.cert_pem,
            "email_domains": [DOMAIN],
        },
        headers=auth_jwt(owner_token),
    )
    assert resp.status_code == 422, resp.text


async def test_get_admin_saml_config_not_found(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner25@acme.com"
    )
    resp = await client.get(SAML_ADMIN, headers=auth_jwt(owner_token))
    assert_problem(resp, 404, "ERR_SAML_CONFIG_NOT_FOUND")


async def test_expired_cert_rejected(client: httpx.AsyncClient) -> None:
    from tests.saml_sso.saml_fixtures import generate_idp_keypair as _gen

    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner26@acme.com"
    )
    expired_keypair = _gen(expired=True)
    resp = await put_saml_config(
        client, owner_token=owner_token, idp_x509_cert=expired_keypair.cert_pem
    )
    assert_problem(resp, 422, "ERR_SAML_CERT_INVALID")


async def test_disabled_saml_config_returns_not_configured(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner27@acme.com"
    )
    keypair = generate_idp_keypair()
    resp = await put_saml_config(
        client, owner_token=owner_token, idp_x509_cert=keypair.cert_pem, enabled=False
    )
    assert resp.status_code == 200, resp.text

    login_resp = await client.get(SAML_LOGIN, params={"domain": DOMAIN}, follow_redirects=False)
    assert_problem(login_resp, 404, "ERR_SAML_NOT_CONFIGURED")
