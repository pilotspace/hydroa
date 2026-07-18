"""Failing-first (RED) suite for sso-oidc (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS (S1–S16).

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  S1 (OIDC disabled → /auth/oidc/login returns 404):
    Route does not exist → 404 (missing route). AssertionError: 404 != 404 on code check,
    OR the route returns FastAPI default 404 without the ERR_OIDC_NOT_CONFIGURED code.
    Actually: GET /auth/oidc/login does not exist yet → 404 plain FastAPI 404 (no code field
    or wrong code). AssertionError: expected code ERR_OIDC_NOT_CONFIGURED.

  S2 (OIDC disabled → /auth/oidc/callback returns 404):
    Same: route does not exist → 404 without ERR_OIDC_NOT_CONFIGURED code.

  S3–S4, S12, S13, S14 (happy-path flows):
    /auth/oidc/callback (and /auth/oidc/login) routes do not exist → 404.
    AssertionError: expected 302, got 404.

  S5–S11, S15–S16 (rejection flows):
    Route does not exist → 404. AssertionError: expected 4xx/5xx specific code, got 404.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /auth/oidc/login, /auth/oidc/callback

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network)
  - FakeOidcExchanger injected via app.state.oidc_exchanger
  - asyncio_mode = "auto" (set in pyproject.toml)
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests import _redis_env

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
OIDC_LOGIN = "/auth/oidc/login"
OIDC_CALLBACK = "/auth/oidc/callback"

# ---------------------------------------------------------------------------
# Fake IdP parameters — deterministic test values
# ---------------------------------------------------------------------------
FAKE_ISSUER = "https://fake-idp.test"
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret-not-real"
FAKE_REDIRECT_URI = "http://gateway.test/auth/oidc/callback"
FAKE_DOMAIN = "example.com"
OTHER_DOMAIN = "otherdomain.com"
FAKE_NONCE = "test-nonce-abc123"
FAKE_STATE = "test-state-xyz789"
# HS256 key used to sign fake ID tokens (no cryptography package available)
FAKE_SIGNING_KEY = "fake-idp-signing-secret-for-tests"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# Helpers — FakeOidcExchanger
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    """In-process fake for the OidcTokenExchanger port.

    Returns pre-built token responses without any network calls.
    Mirrors the app.state.completion_upstream override pattern.
    """

    def __init__(
        self,
        *,
        id_token: str | None = None,
        raise_exc: Exception | None = None,
        response_body: dict[str, Any] | None = None,
    ) -> None:
        self._id_token = id_token
        self._raise_exc = raise_exc
        self._response_body = response_body
        self.calls: list[dict[str, Any]] = []

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        self.calls.append({"code": code, "redirect_uri": redirect_uri})
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._response_body is not None:
            return self._response_body
        if self._id_token is not None:
            return {"id_token": self._id_token, "access_token": "fake-access", "token_type": "bearer"}
        # Default: return empty body (no id_token) — triggers ERR_OIDC_UPSTREAM_ERROR
        return {}


def make_id_token(
    *,
    email: str = "alice@example.com",
    issuer: str = FAKE_ISSUER,
    audience: str = FAKE_CLIENT_ID,
    nonce: str = FAKE_NONCE,
    exp_offset: int = 3600,
) -> str:
    """Mint a fake HS256 ID token for testing claim validation paths.

    NOTE: The gateway in v4 does NOT verify the signature (cryptography package
    absent). These tokens are signed with HS256 for structural completeness; the
    gateway decodes with verify_signature=False and validates claims manually.
    """
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": nonce,
    }
    return jwt.encode(claims, FAKE_SIGNING_KEY, algorithm="HS256")


def make_expired_id_token(
    *,
    email: str = "alice@example.com",
    nonce: str = FAKE_NONCE,
) -> str:
    """Mint an expired fake ID token (exp in the past)."""
    return make_id_token(email=email, nonce=nonce, exp_offset=-3600)


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# App factory helpers
# ---------------------------------------------------------------------------


def make_oidc_settings(*, tenant_id: str | None = None, domain: str = FAKE_DOMAIN) -> Settings:
    """Build Settings with OIDC enabled and a domain mapping for FAKE_DOMAIN."""
    mapping: list[dict[str, str]] = []
    if tenant_id is not None:
        mapping = [{"email_domain": domain, "tenant_id": tenant_id}]
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        oidc_enabled=True,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret=FAKE_CLIENT_SECRET,
        oidc_redirect_uri=FAKE_REDIRECT_URI,
        oidc_domain_mapping=json.dumps(mapping),
    )


def make_disabled_settings() -> Settings:
    """Build Settings with OIDC disabled (default)."""
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        # oidc_enabled defaults to False
    )


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------


def get_cookies_from_response(resp: httpx.Response) -> dict[str, str]:
    """Parse Set-Cookie headers into a name→value dict."""
    cookies: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        # Cookie name=value is the first segment before ";"
        parts = header.split(";")
        if parts:
            name_value = parts[0].strip()
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                cookies[name.strip()] = value.strip()
    return cookies


def get_cookie_attributes(resp: httpx.Response, cookie_name: str) -> str:
    """Return the raw Set-Cookie header for the named cookie."""
    for header in resp.headers.get_list("set-cookie"):
        if header.startswith(cookie_name + "=") or f"; {cookie_name}=" in header:
            # Check first segment
            first_part = header.split(";")[0].strip()
            if first_part.startswith(cookie_name + "="):
                return header
    return ""


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape; return parsed body."""
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; full body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


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


async def count_users_by_email(session: AsyncSession, email: str) -> int:
    """Count rows in users table matching the given email (lowercased)."""
    row = (
        await session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :email"),
            {"email": email.lower()},
        )
    ).fetchone()
    return int(row[0]) if row else 0


async def get_user_by_email(session: AsyncSession, email: str) -> dict[str, Any] | None:
    """Fetch user row as dict by email."""
    row = (
        await session.execute(
            text("SELECT id, tenant_id, email, password_hash, role FROM users WHERE email = :email"),
            {"email": email.lower()},
        )
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "tenant_id": row[1], "email": row[2], "password_hash": row[3], "role": row[4]}


def build_callback_url(*, code: str = "valid-code", state: str = FAKE_STATE) -> str:
    return f"{OIDC_CALLBACK}?code={code}&state={state}"


# ---------------------------------------------------------------------------
# S1 — OIDC disabled → /auth/oidc/login returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_login_disabled_returns_404() -> None:
    """S1: When OIDC is disabled (default), GET /auth/oidc/login returns 404
    ERR_OIDC_NOT_CONFIGURED.

    TRUE-RED: /auth/oidc/login route does not exist yet → FastAPI returns 404
    without the ERR_OIDC_NOT_CONFIGURED code field.
    AssertionError: expected code ERR_OIDC_NOT_CONFIGURED.
    """
    settings = make_disabled_settings()
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(OIDC_LOGIN)
        assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")
        # No oidc_state or oidc_nonce cookies should be set
        cookies = get_cookies_from_response(resp)
        assert "oidc_state" not in cookies
        assert "oidc_nonce" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# S2 — OIDC disabled → /auth/oidc/callback returns 404
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oidc_callback_disabled_returns_404() -> None:
    """S2: When OIDC is disabled, GET /auth/oidc/callback returns 404
    ERR_OIDC_NOT_CONFIGURED.

    TRUE-RED: route does not exist → 404 without ERR_OIDC_NOT_CONFIGURED code.
    """
    settings = make_disabled_settings()
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(build_callback_url())
        assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")
        cookies = get_cookies_from_response(resp)
        assert "ai_proxy_session" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# S3 — happy-path: new user provisioned, session minted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_new_user_provisioned() -> None:
    """S3: Valid OIDC callback provisions a new member user and mints session cookie.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected 302, got 404.
    """
    # First, create a tenant to map to
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner@example.com"
        )

    await bootstrap_app.state.engine.dispose()

    # Now create OIDC-enabled app with the real tenant_id in domain mapping
    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    # Re-use the same DB (do NOT drop_all again — tenant row must persist)
    oidc_app.state.engine = engine

    fake_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="alice@example.com", nonce=FAKE_NONCE)
    )
    oidc_app.state.oidc_exchanger = fake_exchanger

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="good-code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    # S3: expect 302 to post-login redirect with session cookie
    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies, f"ai_proxy_session cookie missing; cookies: {cookies}"

    # Verify user was created with correct attributes
    async with oidc_app.state.sessionmaker() as session:
        user = await get_user_by_email(session, "alice@example.com")
    assert user is not None, "user not created in DB"
    assert user["role"] == "member", f"expected role=member, got {user['role']}"
    assert user["password_hash"] == "!sso-no-password", (
        f"expected SSO sentinel, got {user['password_hash']}"
    )
    assert str(user["tenant_id"]) == tenant_id, "wrong tenant bound"

    # oidc_state and oidc_nonce should be cleared (Max-Age=0)
    for raw_header in resp.headers.get_list("set-cookie"):
        if "oidc_state=" in raw_header or "oidc_nonce=" in raw_header:
            assert "Max-Age=0" in raw_header or "max-age=0" in raw_header.lower(), (
                f"expected oidc_state/nonce to be cleared: {raw_header}"
            )

    await engine.dispose()


# ---------------------------------------------------------------------------
# S4 — second login: existing user reused, no duplicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_login_no_duplicate() -> None:
    """S4: A second OIDC login with the same email reuses the existing user row.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected 302, got 404.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner2@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker

    async def do_callback() -> httpx.Response:
        fake_exchanger = FakeOidcExchanger(
            id_token=make_id_token(email="alice@example.com", nonce=FAKE_NONCE)
        )
        oidc_app.state.oidc_exchanger = fake_exchanger
        transport2 = httpx.ASGITransport(app=oidc_app)
        async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
            return await client.get(
                build_callback_url(code="code1", state=FAKE_STATE),
                cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
            )

    resp1 = await do_callback()
    assert resp1.status_code == 302, f"first login: expected 302, got {resp1.status_code}: {resp1.text}"

    resp2 = await do_callback()
    assert resp2.status_code == 302, f"second login: expected 302, got {resp2.status_code}: {resp2.text}"

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 1, f"expected exactly 1 user row, got {count}"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S5 — unknown email domain → 403 ERR_OIDC_DOMAIN_NOT_MAPPED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_domain_rejected() -> None:
    """S5: Email from an unmapped domain returns 403 ERR_OIDC_DOMAIN_NOT_MAPPED.
    No user created, no session cookie.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected code ERR_OIDC_DOMAIN_NOT_MAPPED.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner3@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)  # maps only example.com
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker

    fake_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="bob@otherdomain.com", nonce=FAKE_NONCE)
    )
    oidc_app.state.oidc_exchanger = fake_exchanger

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 403, "ERR_OIDC_DOMAIN_NOT_MAPPED")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "bob@otherdomain.com")
    assert count == 0, "no user should be created for unmapped domain"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S6 — state mismatch → 400 ERR_OIDC_STATE_MISMATCH
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_mismatch_rejected() -> None:
    """S6: State query param does not match oidc_state cookie → 400.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected code ERR_OIDC_STATE_MISMATCH.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner4@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker

    fake_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="alice@example.com", nonce=FAKE_NONCE)
    )
    oidc_app.state.oidc_exchanger = fake_exchanger

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state="WRONG-STATE"),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},  # correct-state != WRONG-STATE
        )

    assert_problem(resp, 400, "ERR_OIDC_STATE_MISMATCH")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S7 — oidc_state cookie absent → 400 ERR_OIDC_SESSION_EXPIRED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_state_cookie() -> None:
    """S7: Callback without oidc_state cookie returns 400 ERR_OIDC_SESSION_EXPIRED.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_SESSION_EXPIRED.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner5@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="alice@example.com", nonce=FAKE_NONCE)
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        # No oidc_state cookie — state/nonce not sent
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            # No cookies at all
        )

    assert_problem(resp, 400, "ERR_OIDC_SESSION_EXPIRED")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# S8 — IdP token endpoint timeout → 502 ERR_OIDC_UPSTREAM_ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idp_timeout_returns_502() -> None:
    """S8: When the IdP token endpoint times out, the callback returns 502.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_UPSTREAM_ERROR.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner6@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    # FakeOidcExchanger that raises TimeoutException (simulates IdP timeout)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        raise_exc=httpx.TimeoutException("fake IdP timeout")
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 502, "ERR_OIDC_UPSTREAM_ERROR")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S9 — wrong issuer in id_token → 401 ERR_OIDC_TOKEN_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wrong_issuer_rejected() -> None:
    """S9: id_token with iss != GATEWAY_OIDC_ISSUER → 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_TOKEN_INVALID.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner7@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    # Token has wrong issuer
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(
            email="alice@example.com",
            issuer="https://WRONG-ISSUER.test",
            nonce=FAKE_NONCE,
        )
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_INVALID")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S10 — expired id_token → 401 ERR_OIDC_TOKEN_EXPIRED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_token_rejected() -> None:
    """S10: id_token with exp in the past → 401 ERR_OIDC_TOKEN_EXPIRED.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_TOKEN_EXPIRED.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner8@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_expired_id_token(email="alice@example.com", nonce=FAKE_NONCE)
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_EXPIRED")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S11 — nonce mismatch → 401 ERR_OIDC_TOKEN_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonce_mismatch_rejected() -> None:
    """S11: id_token nonce != oidc_nonce cookie → 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_TOKEN_INVALID.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner9@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    # id_token has WRONG nonce
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(
            email="alice@example.com",
            nonce="WRONG-NONCE",
        )
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},  # correct nonce in cookie
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_INVALID")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S12 — auto-provisioned user always has role=member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provisioned_role_always_member() -> None:
    """S12: OIDC-provisioned user has role=member; JWT also decodes as member.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected 302, got 404.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner10@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="carol@example.com", nonce=FAKE_NONCE)
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"

    # Verify user DB row
    async with oidc_app.state.sessionmaker() as session:
        user = await get_user_by_email(session, "carol@example.com")
    assert user is not None
    assert user["role"] == "member", f"expected member, got {user['role']}"

    # Verify JWT contains role=member
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies
    session_jwt = cookies["ai_proxy_session"]
    claims = jwt.decode(
        session_jwt,
        TEST_JWT_SECRET,
        algorithms=["HS256"],
        options={"verify_iss": False},
    )
    assert claims["role"] == "member", f"JWT role must be member, got {claims['role']}"
    assert claims["role"] != "owner"
    assert claims["role"] != "admin"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S13 — GET /auth/oidc/login sets state and nonce cookies, redirects to IdP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_sets_state_nonce_cookies() -> None:
    """S13: GET /auth/oidc/login returns 302 to IdP authorize URL with correct params
    and sets oidc_state + oidc_nonce cookies.

    TRUE-RED: /auth/oidc/login route does not exist → 404.
    AssertionError: expected 302, got 404.
    """
    oidc_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        oidc_enabled=True,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret=FAKE_CLIENT_SECRET,
        oidc_redirect_uri=FAKE_REDIRECT_URI,
        oidc_domain_mapping="[]",
    )
    app = create_app(oidc_settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(OIDC_LOGIN)

    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    location = resp.headers.get("location", "")
    assert "response_type=code" in location, f"missing response_type=code in: {location}"
    assert f"client_id={FAKE_CLIENT_ID}" in location, f"missing client_id in: {location}"
    assert "openid" in location, f"missing openid scope in: {location}"
    assert "state=" in location, f"missing state in: {location}"
    assert "nonce=" in location, f"missing nonce in: {location}"

    # oidc_state and oidc_nonce cookies must be set
    cookies = get_cookies_from_response(resp)
    assert "oidc_state" in cookies, f"oidc_state cookie missing; all cookies: {cookies}"
    assert "oidc_nonce" in cookies, f"oidc_nonce cookie missing; all cookies: {cookies}"

    # State in cookie must match state in redirect URL
    state_in_cookie = cookies["oidc_state"]
    assert f"state={state_in_cookie}" in location, (
        f"state cookie {state_in_cookie!r} does not appear in location: {location}"
    )

    # oidc_state must be HttpOnly and SameSite=Lax
    for raw_header in resp.headers.get_list("set-cookie"):
        if raw_header.startswith("oidc_state="):
            assert "HttpOnly" in raw_header, f"oidc_state not HttpOnly: {raw_header}"
            assert "SameSite=Lax" in raw_header or "samesite=lax" in raw_header.lower(), (
                f"oidc_state not SameSite=Lax: {raw_header}"
            )

    # No ai_proxy_session cookie set at login step
    assert "ai_proxy_session" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# S14 — session cookie attributes match BFF login (Secure in non-dev)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_session_cookie_attributes() -> None:
    """S14: ai_proxy_session cookie has HttpOnly, SameSite=Strict, Secure (non-dev), Path=/.
    No JWT in response body.

    TRUE-RED: /auth/oidc/callback route does not exist → 404.
    AssertionError: expected 302, got 404.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner14@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    # Use environment="production" to trigger Secure flag
    # SANCTIONED EDIT (device-activate-page manifest maintenance, 2026-07-18): a production
    # Settings now requires a non-localhost https device-approval page URL
    # (_validate_agent_oauth_verification_uri); supplied so create_app boots under production.
    oidc_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        environment="production",
        agent_oauth_verification_uri="https://app.hydroa.example/activate",
        oidc_enabled=True,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret=FAKE_CLIENT_SECRET,
        oidc_redirect_uri=FAKE_REDIRECT_URI,
        oidc_domain_mapping=json.dumps([{"email_domain": FAKE_DOMAIN, "tenant_id": tenant_id}]),
    )
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="dave@example.com", nonce=FAKE_NONCE)
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"

    # Find ai_proxy_session Set-Cookie header
    session_cookie_header = ""
    for raw_header in resp.headers.get_list("set-cookie"):
        if raw_header.startswith("ai_proxy_session="):
            session_cookie_header = raw_header
            break

    assert session_cookie_header, "ai_proxy_session Set-Cookie header not found"
    assert "HttpOnly" in session_cookie_header, f"missing HttpOnly: {session_cookie_header}"
    assert "SameSite=Strict" in session_cookie_header or "samesite=strict" in session_cookie_header.lower(), (
        f"missing SameSite=Strict: {session_cookie_header}"
    )
    assert "Secure" in session_cookie_header, (
        f"missing Secure flag in non-dev environment: {session_cookie_header}"
    )
    assert "Path=/" in session_cookie_header or "path=/" in session_cookie_header.lower(), (
        f"missing Path=/: {session_cookie_header}"
    )

    # No JWT in response body (response is a 302, body should be empty or minimal)
    try:
        body = resp.json()
    except Exception:
        body = {}
    assert "access_token" not in str(body), "JWT must not appear in response body"
    assert "id_token" not in str(body), "id_token must not appear in response body"

    await engine.dispose()


# ---------------------------------------------------------------------------
# S15 — missing id_token in IdP response → 502 ERR_OIDC_UPSTREAM_ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_id_token_in_response() -> None:
    """S15: IdP token endpoint returns a body without id_token → 502.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_UPSTREAM_ERROR.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="Acme Corp", email="owner15@example.com"
        )
    await bootstrap_app.state.engine.dispose()

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    # FakeOidcExchanger returns body without id_token
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        response_body={"access_token": "no-id-token-here", "token_type": "bearer"}
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 502, "ERR_OIDC_UPSTREAM_ERROR")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# S16 — cross-tenant email collision → 403 ERR_OIDC_TENANT_CONFLICT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_email_conflict() -> None:
    """S16: SSO email matches a user bound to a different tenant → 403.

    TRUE-RED: route does not exist → 404.
    AssertionError: expected code ERR_OIDC_TENANT_CONFLICT.
    """
    base_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )
    bootstrap_app = create_app(base_settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    transport = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Create tenant_A — this will be the OIDC domain mapping target
        _, tenant_a_id = await signup_tenant(
            client, tenant_name="Tenant A", email="owner-a@tenant-a.com"
        )
        # Create tenant_B — dave@example.com lives here (different tenant)
        _, tenant_b_id = await signup_tenant(
            client, tenant_name="Tenant B", email="owner-b@tenant-b.com"
        )

        # Manually insert dave@example.com into tenant_B
        # (cannot use signup because email domain != tenant-b.com for SSO)
        # Use direct DB insert via the session
    await bootstrap_app.state.engine.dispose()

    # Insert cross-tenant user directly
    async with bootstrap_app.state.sessionmaker() as session:
        await session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, role) "
                "VALUES (:id, :tid, :email, :ph, :role)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tid": tenant_b_id,
                "email": "dave@example.com",
                "ph": "!sso-no-password",
                "role": "member",
            },
        )
        await session.commit()

    # OIDC domain_mapping maps example.com → tenant_A (NOT tenant_B)
    oidc_settings = make_oidc_settings(tenant_id=tenant_a_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = bootstrap_app.state.sessionmaker
    # id_token for dave@example.com — mapped to tenant_A but user exists in tenant_B
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(
        id_token=make_id_token(email="dave@example.com", nonce=FAKE_NONCE)
    )

    transport2 = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport2, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 403, "ERR_OIDC_TENANT_CONFLICT")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    # Existing user in tenant_B must be unmodified
    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "dave@example.com")
    assert count == 1, "cross-tenant collision must not create a duplicate user"

    async with oidc_app.state.sessionmaker() as session:
        existing = await get_user_by_email(session, "dave@example.com")
    assert existing is not None
    assert str(existing["tenant_id"]) == tenant_b_id, "existing user tenant must be unmodified"

    await engine.dispose()
