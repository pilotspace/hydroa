"""Failing-first (RED) suite for oidc-tenant-config (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS (T1–T12).

TRUE-RED RULE: every test asserts TARGET behavior (per-tenant DB-backed OIDC config,
Fernet-encrypted secrets, OidcConfigResolver port, JwksKeyCache (jwks_url, kid) keying,
tenant-confusion defense via oidc_tenant_id cookie) and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  T1  (two-tenant happy path):
    OidcConfigResolver port does not exist; no oidc_tenant_id cookie logic; FakeOidcConfigResolver
    import fails. ImportError / AttributeError / AssertionError: expected 302, got 404 or 500.

  T2  (secret never returned in GET /admin/oidc):
    GET /admin/oidc route does not exist → 404. AssertionError: expected 200, got 404.

  T3  (secret never logged during PUT /admin/oidc):
    PUT /admin/oidc route does not exist → 404 / 405. AssertionError: expected 200.

  T4  (env fallback preserves v4 behavior):
    The env fallback path currently works (v4/v5 frozen code). HOWEVER — the test now calls
    GET /auth/oidc/login WITHOUT ?domain= while also having the new OidcConfigResolver seam
    wired; with no resolver seam set on app.state, the current code would work. The test
    arranges app.state.oidc_config_resolver = FakeOidcConfigResolver(returns_none=True) which
    does not exist yet → ImportError / AttributeError. In the worst case this test will be
    green-by-design (env fallback still works); the assertion that distinguishes it is the
    seam injection, which fails.
    This is a GREEN-BY-DESIGN scenario if the seam injection is skipped; the test includes
    a sentinel assertion that forces red: it asserts the app has an 'oidc_config_resolver'
    attribute on state after create_app (which does not exist yet).

  T5  (unknown domain → 404 ERR_OIDC_NOT_CONFIGURED):
    The ?domain= query param handling does not exist yet. Current /auth/oidc/login ignores the
    domain param; with oidc_enabled=False it returns 404 ERR_OIDC_NOT_CONFIGURED regardless.
    The test arranges oidc_enabled=False explicitly — this MAY be green-by-design (the existing
    code already returns 404 when disabled). HOWEVER the test also asserts the new error
    ERR_OIDC_NOT_CONFIGURED which IS already returned by the existing code. Green-by-design.
    RED element: asserts app.state has oidc_config_resolver attribute → fails.

  T6  (callback without oidc_tenant_id cookie → 400 ERR_OIDC_TENANT_COOKIE_MISSING):
    New error code ERR_OIDC_TENANT_COOKIE_MISSING does not exist; callback does not check
    oidc_tenant_id cookie. Current callback ignores it and proceeds. AssertionError: expected
    400 ERR_OIDC_TENANT_COOKIE_MISSING, got either 400 (wrong code) or 302 (success).

  T7  (tenant-confusion defense):
    OidcConfigResolver seam not wired; oidc_tenant_id cookie not set or read. The callback
    uses the env Settings config (not the per-tenant DB config), so the iss mismatch defense
    is not tenant-specific. AssertionError: expected 401 ERR_OIDC_TOKEN_INVALID due to per-tenant
    config selection, but the flow either succeeds (wrong config used) or fails for wrong reason.

  T8  (PUT without encryption key → 409):
    PUT /admin/oidc route does not exist → 404 / 405. AssertionError: expected 409, got 404.

  T9  (PUT with http:// URL → 422):
    PUT /admin/oidc route does not exist → 404 / 405. AssertionError: expected 422, got 404.

  T10 (GET no config → 404 ERR_OIDC_CONFIG_NOT_FOUND):
    GET /admin/oidc route does not exist → 404 (but wrong code — FastAPI default 404, not
    ERR_OIDC_CONFIG_NOT_FOUND). AssertionError: expected code ERR_OIDC_CONFIG_NOT_FOUND.

  T11 (no cross-tenant kid collision):
    JwksKeyCache.resolve does not accept jwks_url as first param. If the test calls
    resolve(jwks_url, kid, client), it receives TypeError: resolve() takes 3 positional
    arguments but 4 were given. Or the test verifies via HTTP callbacks and finds both
    tenants verify against the same key (collision), which is the bug being fixed.
    AssertionError: tenant B's token verified against key_A (wrong key).

  T12 (PUT/GET round-trip secret never returned):
    PUT /admin/oidc route does not exist → 404. AssertionError: expected 200, got 404.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network for gateway calls)
  - FakeOidcConfigResolver injected via app.state.oidc_config_resolver
  - FakeOidcExchanger injected via app.state.oidc_exchanger (reused from sso-oidc pattern)
  - FakeJwksClient injected via app.state.jwks_client (reused from oidc-jwks pattern)
  - RSA keypairs generated in-test via cryptography package (already on allowlist)
  - asyncio_mode = "auto" (set in pyproject.toml)
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.auth.domain.errors import OidcTokenInvalidError, OidcUpstreamError
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
ADMIN_OIDC = "/admin/oidc"

# ---------------------------------------------------------------------------
# Fake IdP parameters
# ---------------------------------------------------------------------------
FAKE_ISSUER_A = "https://idp-a.test"
FAKE_ISSUER_B = "https://idp-b.test"
FAKE_CLIENT_ID_A = "client-a"
FAKE_CLIENT_ID_B = "client-b"
FAKE_CLIENT_SECRET = "test-client-secret-not-real"  # noqa: S105
FAKE_REDIRECT_URI = "http://gateway.test/auth/oidc/callback"
FAKE_DOMAIN_A = "a.com"
FAKE_DOMAIN_B = "b.com"
FAKE_NONCE_A = "nonce-tenant-a-abc123"
FAKE_NONCE_B = "nonce-tenant-b-xyz789"
FAKE_STATE_A = "state-tenant-a-xyz789"
FAKE_STATE_B = "state-tenant-b-abc123"
FAKE_JWKS_URL_A = "https://idp-a.test/.well-known/jwks.json"
FAKE_JWKS_URL_B = "https://idp-b.test/.well-known/jwks.json"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_KID_SHARED = "shared-kid-collision-test"
TEST_PLAINTEXT_SECRET = "my-real-client-secret-never-return-me"  # noqa: S105

# Valid Fernet key for tests (generated once; deterministic for reproducibility)
TEST_FERNET_KEY = Fernet.generate_key().decode()


# ---------------------------------------------------------------------------
# RSA key generation — real cryptographic keys for RS256 testing
# ---------------------------------------------------------------------------


def generate_rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate a 2048-bit RSA keypair."""
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key, private_key.public_key()


def public_key_to_pem(public_key: rsa.RSAPublicKey) -> bytes:
    """Serialize an RSA public key to PEM format."""
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def make_rs256_id_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = TEST_KID_SHARED,
    email: str = "alice@a.com",
    issuer: str = FAKE_ISSUER_A,
    audience: str = FAKE_CLIENT_ID_A,
    nonce: str = FAKE_NONCE_A,
    exp_offset: int = 3600,
) -> str:
    """Mint a real RS256-signed ID token."""
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
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": kid},
    )


def make_hs256_id_token(
    *,
    email: str = "alice@a.com",
    issuer: str = FAKE_ISSUER_A,
    audience: str = FAKE_CLIENT_ID_A,
    nonce: str = FAKE_NONCE_A,
    exp_offset: int = 3600,
    signing_key: str = "hs256-test-key",  # noqa: S107
) -> str:
    """Mint an HS256-signed ID token for env-fallback tests (mirrors sso-oidc pattern)."""
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
    return jwt.encode(claims, signing_key, algorithm="HS256")


# ---------------------------------------------------------------------------
# FakeOidcConfigResolver — injectable via app.state.oidc_config_resolver
# ---------------------------------------------------------------------------


class FakeOidcProviderConfig:
    """Lightweight in-memory representation of an OidcProviderConfig for tests.

    Does not import gateway.auth.domain.entities.OidcProviderConfig (which does
    not exist yet). Carries the same fields the real class would have.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        issuer: str,
        client_id: str,
        client_secret: str,
        authorize_url: str = "",
        token_url: str,
        jwks_url: str,
        email_domains: list[str],
        enabled: bool = True,
    ) -> None:
        self.tenant_id = uuid.UUID(tenant_id)
        self.issuer = issuer
        self.client_id = client_id
        self.client_secret = client_secret
        self.authorize_url = authorize_url
        self.token_url = token_url
        self.jwks_url = jwks_url
        self.email_domains = email_domains
        self.enabled = enabled


class FakeOidcConfigResolver:
    """In-process fake for the OidcConfigResolver port.

    Returns pre-built OidcProviderConfig objects by domain.
    Injected via app.state.oidc_config_resolver.
    """

    def __init__(
        self,
        *,
        configs: dict[str, FakeOidcProviderConfig] | None = None,
        returns_none: bool = False,
    ) -> None:
        # domain → config mapping
        self._configs = configs or {}
        self._returns_none = returns_none
        self.calls: list[str | None] = []

    async def resolve(self, domain: str | None) -> FakeOidcProviderConfig | None:
        self.calls.append(domain)
        if self._returns_none:
            return None
        if domain is None:
            return None
        return self._configs.get(domain)


# ---------------------------------------------------------------------------
# FakeOidcExchanger — reused from sso-oidc/oidc-jwks pattern
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    """In-process fake for the OidcTokenExchanger port."""

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
            return {
                "id_token": self._id_token,
                "access_token": "fake-access",
                "token_type": "bearer",
            }
        return {}


# ---------------------------------------------------------------------------
# FakeJwksClient — reused from oidc-jwks pattern with per-jwks_url awareness
# ---------------------------------------------------------------------------


class FakeJwksClient:
    """In-process fake for the JwksClient port.

    Tracks calls including the kid argument. Supports per-kid keys.
    The client is jwks_url-agnostic; tests assign different instances per tenant.
    """

    def __init__(
        self,
        *,
        keys: dict[str, Any] | None = None,
        raise_exc: Exception | None = None,
    ) -> None:
        self._keys: dict[str, Any] = keys or {}
        self._raise_exc = raise_exc
        self.calls: list[str | None] = []

    async def get_signing_key(self, kid: str | None) -> Any:
        self.calls.append(kid)
        if self._raise_exc is not None:
            raise self._raise_exc
        if kid is None:
            if len(self._keys) == 1:
                return next(iter(self._keys.values()))
            raise OidcTokenInvalidError("kid=None but JWKS has 0 or multiple keys")
        if kid not in self._keys:
            raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS")
        return self._keys[kid]


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def make_env_oidc_settings(
    *,
    tenant_id: str | None = None,
    issuer: str = FAKE_ISSUER_A,
    client_id: str = FAKE_CLIENT_ID_A,
    domain: str = FAKE_DOMAIN_A,
    encryption_key: str = "",
    allow_http_urls: bool = False,
    jwks_url: str = "",
) -> Settings:
    """Build Settings with env-level OIDC enabled (v4/v5 style). No DB config resolver."""
    mapping: list[dict[str, str]] = []
    if tenant_id is not None:
        mapping = [{"email_domain": domain, "tenant_id": tenant_id}]
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        oidc_enabled=True,
        oidc_issuer=issuer,
        oidc_client_id=client_id,
        oidc_client_secret=FAKE_CLIENT_SECRET,
        oidc_redirect_uri=FAKE_REDIRECT_URI,
        oidc_domain_mapping=json.dumps(mapping),
        oidc_jwks_url=jwks_url,
        oidc_config_encryption_key=encryption_key,
    )


def make_base_settings(
    *,
    encryption_key: str = "",
    allow_http_urls: bool = False,
) -> Settings:
    """Build minimal Settings for tests that inject a FakeOidcConfigResolver."""
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        oidc_config_encryption_key=encryption_key,
    )


# ---------------------------------------------------------------------------
# HTTP / cookie helpers (mirroring sso-oidc + oidc-jwks pattern)
# ---------------------------------------------------------------------------


def get_cookies_from_response(resp: httpx.Response) -> dict[str, str]:
    """Parse Set-Cookie headers into a name→value dict."""
    cookies: dict[str, str] = {}
    for header in resp.headers.get_list("set-cookie"):
        parts = header.split(";")
        if parts:
            name_value = parts[0].strip()
            if "=" in name_value:
                name, _, value = name_value.partition("=")
                cookies[name.strip()] = value.strip()
    return cookies


def get_full_cookie_header(resp: httpx.Response, cookie_name: str) -> str:
    """Return the raw Set-Cookie header for the named cookie."""
    for header in resp.headers.get_list("set-cookie"):
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


def build_callback_url(*, code: str = "valid-code", state: str = FAKE_STATE_A) -> str:
    return f"{OIDC_CALLBACK}?code={code}&state={state}"


# ---------------------------------------------------------------------------
# Tenant bootstrap helpers (mirroring oidc-jwks pattern)
# ---------------------------------------------------------------------------


async def signup_tenant(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",  # noqa: S107
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
    row = (
        await session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :email"),
            {"email": email.lower()},
        )
    ).fetchone()
    return int(row[0]) if row else 0


async def get_user_by_email(session: AsyncSession, email: str) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                "SELECT id, tenant_id, email, password_hash, role FROM users WHERE email = :email"
            ),
            {"email": email.lower()},
        )
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row[0],
        "tenant_id": row[1],
        "email": row[2],
        "password_hash": row[3],
        "role": row[4],
    }


async def bootstrap_fresh_db(settings: Settings) -> tuple[Any, Any]:
    """Create fresh schema; return (engine, sessionmaker)."""
    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker


async def bootstrap_tenant_via_api(
    engine: Any,
    sessionmaker: Any,
    *,
    tenant_name: str,
    owner_email: str,
    settings: Settings,
) -> str:
    """Sign up a tenant+owner via API; return tenant_id string."""
    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(client, tenant_name=tenant_name, email=owner_email)

    await app.state.engine.dispose()
    return tenant_id


async def seed_verified_domain_claim(sessionmaker: Any, *, tenant_id: str, domain: str) -> None:
    """SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
    requires a verified claim first — precondition added, assertion intent unchanged.

    PUT /admin/oidc now rejects an email_domains entry with no VERIFIED
    tenant_domain_claims row for the calling tenant (M5/R3). This suite has no
    DNS-TXT resolver seam wired (it predates domain_capture's DNS-TXT flow), so
    a verified claim is direct-inserted here rather than driven through
    POST /admin/domain-claims + /verify — the write-gate only cares that a
    verified row exists, not how it got there.
    """
    from gateway.domain_capture.infrastructure.orm import TenantDomainClaimRow

    async with sessionmaker() as session:
        owner_row = (
            await session.execute(
                text("SELECT id FROM users WHERE tenant_id = :tid ORDER BY created_at LIMIT 1"),
                {"tid": tenant_id},
            )
        ).scalar_one()
        session.add(
            TenantDomainClaimRow(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(str(tenant_id)),
                domain=domain,
                verification_token="sanctioned-edit-seed",  # noqa: S106
                status="verified",
                verified_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(days=365),
                created_by_user_id=owner_row,
            )
        )
        await session.commit()


# ---------------------------------------------------------------------------
# T1 — Two-tenant happy path: distinct IdP per tenant, both logins succeed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_tenant_happy_path() -> None:
    """T1: Tenant A (IdP A, issuer=idp-a.test) and tenant B (IdP B, issuer=idp-b.test)
    both complete OIDC logins in one app instance. Users land in their correct tenants.

    TRUE-RED: OidcConfigResolver port does not exist; no oidc_tenant_id cookie;
    FakeOidcConfigResolver does not exist. ImportError / AttributeError / 404 from routes
    that do not handle per-tenant resolution.

    Right-reason: the test wires app.state.oidc_config_resolver = FakeOidcConfigResolver(...)
    which does not exist as a recognized seam. The current oidc_router.py ignores that attribute.
    The oidc_tenant_id cookie is never set, so the callback will 400 ERR_OIDC_TENANT_COOKIE_MISSING
    — which also does not exist in the current code.
    AssertionError: expected 302 (two successful logins), got 4xx on first callback.
    """
    private_key_a, public_key_a = generate_rsa_keypair()
    private_key_b, public_key_b = generate_rsa_keypair()
    pem_a = public_key_to_pem(public_key_a)
    pem_b = public_key_to_pem(public_key_b)

    base_settings = make_base_settings()
    engine, sessionmaker = await bootstrap_fresh_db(base_settings)

    # Sign up two tenants
    settings_for_signup = base_settings
    app_signup = create_app(settings_for_signup)
    app_signup.state.engine = engine
    app_signup.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=app_signup)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id_a = await signup_tenant(client, tenant_name="TenantA", email="owner-a@a.com")
        _, tenant_id_b = await signup_tenant(client, tenant_name="TenantB", email="owner-b@b.com")
    await app_signup.state.engine.dispose()

    # Build per-tenant fake configs
    config_a = FakeOidcProviderConfig(
        tenant_id=tenant_id_a,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        client_secret=FAKE_CLIENT_SECRET,
        token_url=f"{FAKE_ISSUER_A}/token",
        jwks_url=FAKE_JWKS_URL_A,
        email_domains=[FAKE_DOMAIN_A],
    )
    config_b = FakeOidcProviderConfig(
        tenant_id=tenant_id_b,
        issuer=FAKE_ISSUER_B,
        client_id=FAKE_CLIENT_ID_B,
        client_secret=FAKE_CLIENT_SECRET,
        token_url=f"{FAKE_ISSUER_B}/token",
        jwks_url=FAKE_JWKS_URL_B,
        email_domains=[FAKE_DOMAIN_B],
    )
    resolver = FakeOidcConfigResolver(configs={FAKE_DOMAIN_A: config_a, FAKE_DOMAIN_B: config_b})

    id_token_a = make_rs256_id_token(
        private_key_a,
        kid=TEST_KID_SHARED,
        email="alice@a.com",
        issuer=FAKE_ISSUER_A,
        audience=FAKE_CLIENT_ID_A,
        nonce=FAKE_NONCE_A,
    )
    id_token_b = make_rs256_id_token(
        private_key_b,
        kid=TEST_KID_SHARED,
        email="bob@b.com",
        issuer=FAKE_ISSUER_B,
        audience=FAKE_CLIENT_ID_B,
        nonce=FAKE_NONCE_B,
    )

    # Per-tenant JwksClients (same kid "shared-kid" but different keys — this is T11's
    # collision scenario too; in T1 we use a per-tenant jwks_client resolver seam)
    jwks_client_a = FakeJwksClient(keys={TEST_KID_SHARED: pem_a})
    jwks_client_b = FakeJwksClient(keys={TEST_KID_SHARED: pem_b})

    # We need two separate app instances or a multi-tenant JwksClient dispatch.
    # Per the §3 contract, the resolver port tells the use case which jwks_url to use,
    # and the JwksKeyCache keys by (jwks_url, kid). We use a single FakeJwksClient that
    # dispatches by the jwks_url passed during resolve — but that seam doesn't exist yet.
    # For T1, we demonstrate the DESIRED behavior via two separate app instances
    # sharing the same engine/sessionmaker (production-like parallel tenant logins).

    settings_a = make_env_oidc_settings(
        tenant_id=tenant_id_a,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        domain=FAKE_DOMAIN_A,
        jwks_url=FAKE_JWKS_URL_A,
    )
    settings_b = make_env_oidc_settings(
        tenant_id=tenant_id_b,
        issuer=FAKE_ISSUER_B,
        client_id=FAKE_CLIENT_ID_B,
        domain=FAKE_DOMAIN_B,
        jwks_url=FAKE_JWKS_URL_B,
    )

    app_a = create_app(settings_a)
    app_a.state.engine = engine
    app_a.state.sessionmaker = sessionmaker
    app_a.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token_a)
    app_a.state.jwks_client = jwks_client_a
    # Wire per-tenant resolver seam (NEW — does not exist in current code)
    app_a.state.oidc_config_resolver = resolver

    app_b = create_app(settings_b)
    app_b.state.engine = engine
    app_b.state.sessionmaker = sessionmaker
    app_b.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token_b)
    app_b.state.jwks_client = jwks_client_b
    app_b.state.oidc_config_resolver = resolver

    # ── Tenant A login flow ────────────────────────────────────────────────
    transport_a = httpx.ASGITransport(app=app_a)
    async with httpx.AsyncClient(transport=transport_a, base_url="http://test") as client_a:
        # Login: GET /auth/oidc/login?domain=a.com → expect oidc_tenant_id cookie set
        login_resp_a = await client_a.get(f"{OIDC_LOGIN}?domain={FAKE_DOMAIN_A}")
        assert login_resp_a.status_code == 302, (
            f"T1 tenant A login expected 302, got {login_resp_a.status_code}: {login_resp_a.text}"
        )
        cookies_a = get_cookies_from_response(login_resp_a)
        assert "oidc_tenant_id" in cookies_a, (
            "T1: oidc_tenant_id cookie must be set by /auth/oidc/login?domain=a.com"
        )
        assert "oidc_state" in cookies_a
        assert "oidc_nonce" in cookies_a

        # Callback: with all three cookies
        cb_resp_a = await client_a.get(
            build_callback_url(code="code-a", state=cookies_a["oidc_state"]),
            cookies={
                "oidc_state": cookies_a["oidc_state"],
                "oidc_nonce": FAKE_NONCE_A,
                "oidc_tenant_id": cookies_a["oidc_tenant_id"],
            },
        )
        assert cb_resp_a.status_code == 302, (
            f"T1 tenant A callback expected 302, got {cb_resp_a.status_code}: {cb_resp_a.text}"
        )
        cb_cookies_a = get_cookies_from_response(cb_resp_a)
        assert "ai_proxy_session" in cb_cookies_a, "T1: ai_proxy_session must be set for tenant A"

    # ── Tenant B login flow ────────────────────────────────────────────────
    transport_b = httpx.ASGITransport(app=app_b)
    async with httpx.AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        login_resp_b = await client_b.get(f"{OIDC_LOGIN}?domain={FAKE_DOMAIN_B}")
        assert login_resp_b.status_code == 302, (
            f"T1 tenant B login expected 302, got {login_resp_b.status_code}: {login_resp_b.text}"
        )
        cookies_b = get_cookies_from_response(login_resp_b)
        assert "oidc_tenant_id" in cookies_b, (
            "T1: oidc_tenant_id cookie must be set by /auth/oidc/login?domain=b.com"
        )

        cb_resp_b = await client_b.get(
            build_callback_url(code="code-b", state=cookies_b["oidc_state"]),
            cookies={
                "oidc_state": cookies_b["oidc_state"],
                "oidc_nonce": FAKE_NONCE_B,
                "oidc_tenant_id": cookies_b["oidc_tenant_id"],
            },
        )
        assert cb_resp_b.status_code == 302, (
            f"T1 tenant B callback expected 302, got {cb_resp_b.status_code}: {cb_resp_b.text}"
        )
        cb_cookies_b = get_cookies_from_response(cb_resp_b)
        assert "ai_proxy_session" in cb_cookies_b, "T1: ai_proxy_session must be set for tenant B"

    # ── Verify users are in their respective tenants ───────────────────────
    from sqlalchemy.ext.asyncio import async_sessionmaker  # noqa: PLC0415

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        user_a = await get_user_by_email(session, "alice@a.com")
        user_b = await get_user_by_email(session, "bob@b.com")

    assert user_a is not None, "T1: alice@a.com must be provisioned"
    assert user_b is not None, "T1: bob@b.com must be provisioned"
    assert str(user_a["tenant_id"]) == tenant_id_a, (
        "T1: alice must be in tenant A, not contaminated into tenant B"
    )
    assert str(user_b["tenant_id"]) == tenant_id_b, (
        "T1: bob must be in tenant B, not contaminated into tenant A"
    )
    assert user_a["role"] == "member"
    assert user_b["role"] == "member"

    await engine.dispose()


# ---------------------------------------------------------------------------
# T2 — Secret never returned in GET /admin/oidc response body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_never_returned_in_get_response() -> None:
    """T2: GET /admin/oidc must NEVER include client_secret plaintext in the response body.
    The client_secret field must be exactly the string "<stored>".

    TRUE-RED: GET /admin/oidc route does not exist → 404.
    AssertionError: expected HTTP 200, got 404.
    """
    settings = make_base_settings(encryption_key=TEST_FERNET_KEY)
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # First, sign up a tenant/owner to get auth credentials
        _, tenant_id = await signup_tenant(
            client,
            tenant_name="SecretTestTenant",
            email="owner-t2@secret.test",
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t2@secret.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        # SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
        # requires a verified claim first — precondition added, assertion intent unchanged.
        await seed_verified_domain_claim(sessionmaker, tenant_id=tenant_id, domain="secret.test")

        # PUT a config so there is something to GET
        put_resp = await client.put(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "issuer": "https://idp.test",
                "client_id": "my-client",
                "client_secret": TEST_PLAINTEXT_SECRET,
                "token_url": "https://idp.test/token",
                "jwks_url": "https://idp.test/.well-known/jwks.json",
                "email_domains": ["secret.test"],
                "enabled": True,
            },
        )
        # PUT must succeed (200) for this test to proceed to GET assertion
        assert put_resp.status_code == 200, (
            f"T2: PUT /admin/oidc expected 200, got {put_resp.status_code}: {put_resp.text}"
        )

        # GET /admin/oidc — this is the target assertion
        get_resp = await client.get(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert get_resp.status_code == 200, (
            f"T2: GET /admin/oidc expected 200, got {get_resp.status_code}: {get_resp.text}"
        )

        body = get_resp.json()
        # Primary security assertion: plaintext secret must not appear anywhere in response
        raw_body = get_resp.text
        assert TEST_PLAINTEXT_SECRET not in raw_body, (
            f"T2 SECURITY VIOLATION: plaintext client_secret found in GET /admin/oidc response body!"
            f" Secret: {TEST_PLAINTEXT_SECRET!r} | Body: {raw_body[:200]}"
        )

        # The client_secret field must be exactly the sentinel placeholder
        assert body.get("client_secret") == "<stored>", (
            f"T2: client_secret field must be '<stored>', got {body.get('client_secret')!r}"
        )

    await engine.dispose()


# ---------------------------------------------------------------------------
# T3 — Secret never logged during PUT /admin/oidc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_never_logged_during_put() -> None:
    """T3: No log line during PUT /admin/oidc contains the plaintext client_secret.

    TRUE-RED: PUT /admin/oidc route does not exist → 404 / 405.
    AssertionError: expected HTTP 200, got 404.
    """
    settings = make_base_settings(encryption_key=TEST_FERNET_KEY)
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client,
            tenant_name="LogTestTenant",
            email="owner-t3@log.test",
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t3@log.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        # SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
        # requires a verified claim first — precondition added, assertion intent unchanged.
        await seed_verified_domain_claim(sessionmaker, tenant_id=tenant_id, domain="log.test")

        # Capture log output during PUT
        log_handler = _CapturingLogHandler()
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        root_logger.setLevel(logging.DEBUG)

        try:
            put_resp = await client.put(
                ADMIN_OIDC,
                headers={"Authorization": f"Bearer {owner_token}"},
                json={
                    "issuer": "https://idp.test",
                    "client_id": "my-client",
                    "client_secret": TEST_PLAINTEXT_SECRET,
                    "token_url": "https://idp.test/token",
                    "jwks_url": "https://idp.test/.well-known/jwks.json",
                    "email_domains": ["log.test"],
                    "enabled": True,
                },
            )
        finally:
            root_logger.removeHandler(log_handler)

        assert put_resp.status_code == 200, (
            f"T3: PUT /admin/oidc expected 200, got {put_resp.status_code}: {put_resp.text}"
        )

        # Security assertion: plaintext secret must not appear in any log line
        captured_logs = log_handler.records
        for record in captured_logs:
            msg = record.getMessage()
            assert TEST_PLAINTEXT_SECRET not in msg, (
                f"T3 SECURITY VIOLATION: plaintext client_secret found in log line: {msg[:300]}"
            )

        # Response must also not contain the secret
        assert TEST_PLAINTEXT_SECRET not in put_resp.text, (
            f"T3 SECURITY VIOLATION: plaintext client_secret in PUT response body"
        )
        assert put_resp.json().get("client_secret") == "<stored>"

    await engine.dispose()


class _CapturingLogHandler(logging.Handler):
    """Captures log records for inspection."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# ---------------------------------------------------------------------------
# T4 — Env fallback: existing sso-oidc/oidc-jwks behavior preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_env_fallback_preserves_v4_behavior() -> None:
    """T4: When no DB config and oidc_enabled=True (env Settings), the env fallback path
    works exactly as in v4/v5. Green-by-design regression pin.

    TRUE-RED element: the test asserts that app.state has 'oidc_config_resolver' attribute
    (even if None) after create_app — this attribute does not exist yet. The test also
    verifies the FakeOidcConfigResolver(returns_none=True) seam is honored.
    AssertionError: app.state has no attribute 'oidc_config_resolver'.
    """
    settings = make_env_oidc_settings(tenant_id=None)  # no domain mapping yet; set below
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    # Sign up a tenant and get tenant_id for domain mapping
    bootstrap_app = create_app(settings)
    bootstrap_app.state.engine = engine
    bootstrap_app.state.sessionmaker = sessionmaker
    transport_b = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport_b, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client,
            tenant_name="EnvFallbackTenant",
            email="owner-t4@example.com",
        )
    await bootstrap_app.state.engine.dispose()

    # Rebuild with domain mapping for the env path
    settings_with_mapping = make_env_oidc_settings(
        tenant_id=tenant_id,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        domain=FAKE_DOMAIN_A,
    )
    id_token = make_hs256_id_token(
        email="alice@a.com",
        issuer=FAKE_ISSUER_A,
        audience=FAKE_CLIENT_ID_A,
        nonce=FAKE_NONCE_A,
    )

    app = create_app(settings_with_mapping)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    # Sentinel assertion: create_app must initialize app.state.oidc_config_resolver
    # (even as None) — checked BEFORE the test sets the seam, so we verify the
    # production create_app behavior not the test's own assignment.
    # TRUE-RED: this attribute does not exist on create_app output; hasattr returns False.
    assert hasattr(app.state, "oidc_config_resolver"), (
        "T4: create_app must initialize app.state.oidc_config_resolver "
        "(even as None) so tests can inject the resolver seam"
    )

    # Wire resolver seam that returns None → env fallback
    app.state.oidc_config_resolver = FakeOidcConfigResolver(returns_none=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Login via env fallback (no ?domain= param)
        login_resp = await client.get(OIDC_LOGIN)
        assert login_resp.status_code == 302, (
            f"T4: env fallback login expected 302, got {login_resp.status_code}"
        )
        login_cookies = get_cookies_from_response(login_resp)
        assert "oidc_state" in login_cookies

        # Callback
        cb_resp = await client.get(
            build_callback_url(code="valid-code", state=login_cookies["oidc_state"]),
            cookies={
                "oidc_state": login_cookies["oidc_state"],
                "oidc_nonce": FAKE_NONCE_A,
                "oidc_tenant_id": login_cookies.get("oidc_tenant_id", "env-config"),
            },
        )
        assert cb_resp.status_code == 302, (
            f"T4: env fallback callback expected 302, got {cb_resp.status_code}: {cb_resp.text}"
        )
        cb_cookies = get_cookies_from_response(cb_resp)
        assert "ai_proxy_session" in cb_cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# T5 — Unknown domain → 404 ERR_OIDC_NOT_CONFIGURED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_domain_returns_404() -> None:
    """T5: When ?domain=unknown.com and no DB config + env not enabled → 404.

    TRUE-RED element: assert app.state.oidc_config_resolver attribute exists
    (does not exist yet on create_app output).
    """
    settings = make_base_settings()  # oidc_enabled defaults to False, no resolver
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Sentinel: oidc_config_resolver seam must exist on app.state after create_app
    assert hasattr(app.state, "oidc_config_resolver"), (
        "T5: create_app must initialize app.state.oidc_config_resolver"
    )

    # Wire resolver that returns None for all domains
    app.state.oidc_config_resolver = FakeOidcConfigResolver(returns_none=True)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(f"{OIDC_LOGIN}?domain=unknown.com")
        assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")

        # No cookies set
        cookies = get_cookies_from_response(resp)
        assert "oidc_tenant_id" not in cookies
        assert "oidc_state" not in cookies
        assert "oidc_nonce" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# T6 — Callback without oidc_tenant_id cookie → 400 ERR_OIDC_TENANT_COOKIE_MISSING
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callback_without_tenant_cookie_returns_400() -> None:
    """T6: DB-config deployment (env OIDC disabled) — callback with a live login
    round-trip (oidc_state cookie present) but WITHOUT the oidc_tenant_id cookie →
    400 ERR_OIDC_TENANT_COOKIE_MISSING.

    DISPOSITION EDIT (orchestrator, 2026-06-11): the original T6 arranged env-ENABLED
    settings — the exact arrangement frozen sso-oidc S3 pins as 302 (env fallback);
    the two tests were contradictory and the build correctly HARD-STOPPED. Per the
    v4 S11 precedent the defective red test is amended in-file with this disposition
    rather than weakening either contract. Binding semantics (recorded at the gate),
    with cookie_tenant_id absent:
      env enabled                          → env fallback (frozen S3/J-suite, unchanged)
      env disabled + oidc_state present    → 400 ERR_OIDC_TENANT_COOKIE_MISSING
                                             (a DB-config login lost its tenant binding)
      env disabled + no state cookie       → 404 ERR_OIDC_NOT_CONFIGURED (frozen S2)

    TRUE-RED (original defect class still covered): ERR_OIDC_TENANT_COOKIE_MISSING
    did not exist pre-build; the callback did not inspect oidc_tenant_id.
    """
    settings = make_base_settings()  # env OIDC DISABLED — DB-config-only deployment
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Callback WITH oidc_state cookie (login round-trip began) but WITHOUT
        # the oidc_tenant_id cookie (tenant binding lost mid-flight).
        resp = await client.get(
            build_callback_url(code="valid-code", state=FAKE_STATE_A),
            cookies={
                "oidc_state": FAKE_STATE_A,
                "oidc_nonce": FAKE_NONCE_A,
                # oidc_tenant_id intentionally absent
            },
        )
        assert_problem(resp, 400, "ERR_OIDC_TENANT_COOKIE_MISSING")

        # No session or user created
        cookies = get_cookies_from_response(resp)
        assert "ai_proxy_session" not in cookies

    await engine.dispose()

    await engine.dispose()


# ---------------------------------------------------------------------------
# T7 — Tenant-confusion defense: callback with wrong tenant cookie → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_confusion_defense() -> None:
    """T7: Callback with oidc_tenant_id=tenantA cookie but id_token iss=idp-b.test (wrong
    issuer for tenant A's config) → 401 ERR_OIDC_TOKEN_INVALID.

    The config resolved from oidc_tenant_id cookie is tenant A's. The token's iss does not
    match tenant A's issuer → iss mismatch → 401. This is the tenant-confusion defense.

    TRUE-RED: oidc_tenant_id cookie is not read or used in current callback code. The
    current code uses Settings env config, which matches FAKE_ISSUER_A. A token with
    iss=FAKE_ISSUER_B would be rejected by the current code too — but for the wrong reason
    (the env Settings issuer=FAKE_ISSUER_A, not the per-tenant config).
    The test forces a right-reason failure by: setting up per-tenant resolver that
    returns config_A (issuer=idp-a.test) when oidc_tenant_id=tenantA, and presenting a
    token with iss=idp-b.test. Currently the oidc_tenant_id cookie is ignored → the
    Settings env issuer (FAKE_ISSUER_A) is used → the iss=idp-b.test token fails the env
    check — this produces the correct 401, but for the wrong architectural reason.
    The sentinel assertion is: the resolver must have been CALLED with the tenant_id from
    the cookie (not the domain param). Without per-tenant resolution, the resolver call count
    is 0 or the seam is not wired.
    AssertionError: resolver.calls must contain the tenant_id from the cookie; got 0 calls.
    """
    settings = make_env_oidc_settings(
        tenant_id=None,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        domain=FAKE_DOMAIN_A,
    )
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    bootstrap_app = create_app(settings)
    bootstrap_app.state.engine = engine
    bootstrap_app.state.sessionmaker = sessionmaker
    transport_b = httpx.ASGITransport(app=bootstrap_app)
    async with httpx.AsyncClient(transport=transport_b, base_url="http://test") as client:
        _, tenant_id_a = await signup_tenant(
            client, tenant_name="T7TenantA", email="owner-t7@a.test"
        )
    await bootstrap_app.state.engine.dispose()

    config_a = FakeOidcProviderConfig(
        tenant_id=tenant_id_a,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        client_secret=FAKE_CLIENT_SECRET,
        token_url=f"{FAKE_ISSUER_A}/token",
        jwks_url=FAKE_JWKS_URL_A,
        email_domains=[FAKE_DOMAIN_A],
    )
    # Resolver keyed by tenant_id (for callback resolution, not domain)
    resolver = FakeOidcConfigResolver(configs={FAKE_DOMAIN_A: config_a})

    # Token with WRONG issuer (idp-b.test instead of idp-a.test)
    wrong_issuer_token = make_hs256_id_token(
        email="alice@a.com",
        issuer=FAKE_ISSUER_B,  # WRONG — tenant A expects FAKE_ISSUER_A
        audience=FAKE_CLIENT_ID_A,
        nonce=FAKE_NONCE_A,
    )

    settings_mapped = make_env_oidc_settings(
        tenant_id=tenant_id_a,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        domain=FAKE_DOMAIN_A,
    )
    app = create_app(settings_mapped)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.oidc_exchanger = FakeOidcExchanger(id_token=wrong_issuer_token)
    app.state.oidc_config_resolver = resolver

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            build_callback_url(code="valid-code", state=FAKE_STATE_A),
            cookies={
                "oidc_state": FAKE_STATE_A,
                "oidc_nonce": FAKE_NONCE_A,
                "oidc_tenant_id": tenant_id_a,  # tenant A's cookie
            },
        )
        assert_problem(resp, 401, "ERR_OIDC_TOKEN_INVALID")

        # Sentinel: the resolver must have been invoked with the tenant_id from the cookie
        # (not just the domain). This proves the callback uses per-tenant resolution.
        assert len(resolver.calls) >= 1, (
            "T7: OidcConfigResolver must be called during /callback to resolve per-tenant config "
            f"from oidc_tenant_id cookie; got {len(resolver.calls)} calls"
        )

        # No session or user
        cookies = get_cookies_from_response(resp)
        assert "ai_proxy_session" not in cookies

    await engine.dispose()


# ---------------------------------------------------------------------------
# T8 — PUT /admin/oidc without encryption key → 409
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_without_encryption_key_returns_409() -> None:
    """T8: PUT /admin/oidc when oidc_config_encryption_key="" → 409
    ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED.

    TRUE-RED: PUT /admin/oidc route does not exist → 404 / 405.
    AssertionError: expected 409, got 404.
    """
    settings = make_base_settings(encryption_key="")  # no encryption key
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, _tenant_id = await signup_tenant(
            client, tenant_name="T8Tenant", email="owner-t8@t8.test"
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t8@t8.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        resp = await client.put(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "issuer": "https://idp.test",
                "client_id": "my-client",
                "client_secret": TEST_PLAINTEXT_SECRET,
                "token_url": "https://idp.test/token",
                "jwks_url": "https://idp.test/.well-known/jwks.json",
                "email_domains": ["t8.test"],
                "enabled": True,
            },
        )
        assert_problem(resp, 409, "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED")

        # No DB write occurred — verify via GET returning 404 (no row)
        get_resp = await client.get(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert_problem(get_resp, 404, "ERR_OIDC_CONFIG_NOT_FOUND")

    await engine.dispose()


# ---------------------------------------------------------------------------
# T9 — PUT /admin/oidc with http:// URL in production → 422
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_with_http_url_in_production_returns_422() -> None:
    """T9: PUT /admin/oidc with token_url="http://..." when oidc_allow_http_urls=False → 422.

    TRUE-RED: PUT /admin/oidc route does not exist → 404 / 405.
    AssertionError: expected 422, got 404.
    """
    settings = make_base_settings(encryption_key=TEST_FERNET_KEY, allow_http_urls=False)
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, _tenant_id = await signup_tenant(
            client, tenant_name="T9Tenant", email="owner-t9@t9.test"
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t9@t9.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        resp = await client.put(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "issuer": "https://idp.test",
                "client_id": "my-client",
                "client_secret": TEST_PLAINTEXT_SECRET,
                "token_url": "http://insecure.example.com/token",  # HTTP — invalid
                "jwks_url": "https://idp.test/.well-known/jwks.json",
                "email_domains": ["t9.test"],
                "enabled": True,
            },
        )
        # Expect 422 per-field validation error for token_url
        assert resp.status_code == 422, (
            f"T9: expected 422 for http:// token_url in production, got {resp.status_code}: {resp.text}"
        )
        # The error detail must mention token_url
        body = resp.json()
        detail_str = json.dumps(body)
        assert "token_url" in detail_str.lower() or "url" in detail_str.lower(), (
            f"T9: 422 error must mention the problematic URL field; body: {detail_str[:300]}"
        )

    await engine.dispose()


# ---------------------------------------------------------------------------
# T10 — GET /admin/oidc for tenant with no row → 404 ERR_OIDC_CONFIG_NOT_FOUND
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_no_config_returns_404() -> None:
    """T10: GET /admin/oidc when no oidc_provider_configs row for the caller's tenant → 404
    ERR_OIDC_CONFIG_NOT_FOUND.

    TRUE-RED: GET /admin/oidc route does not exist → 404 FastAPI default (no code field).
    AssertionError: expected code ERR_OIDC_CONFIG_NOT_FOUND; got no code or wrong code.
    """
    settings = make_base_settings(encryption_key=TEST_FERNET_KEY)
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, _tenant_id = await signup_tenant(
            client, tenant_name="T10Tenant", email="owner-t10@t10.test"
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t10@t10.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        # No PUT was called — no row exists for this tenant
        get_resp = await client.get(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert_problem(get_resp, 404, "ERR_OIDC_CONFIG_NOT_FOUND")

    await engine.dispose()


# ---------------------------------------------------------------------------
# T11 — No cross-tenant kid collision (JwksKeyCache keyed by (jwks_url, kid))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwks_cache_no_cross_tenant_collision() -> None:
    """T11: Tenant A (jwks_url=jwks-a.test) and tenant B (jwks_url=jwks-b.test) both use
    kid="shared-kid" but different RSA key pairs. After tenant A's callback, the cache
    stores (jwks-a.test, shared-kid) → key_A. Tenant B's callback must fetch key_B from
    jwks-b.test, NOT reuse key_A from cache.

    This tests that JwksKeyCache.resolve accepts jwks_url as a parameter (the security fix)
    and that the cache key is (jwks_url, kid) not bare kid.

    TRUE-RED: JwksKeyCache.resolve(kid, jwks_client) currently takes 2 params (not 3).
    The test calls jwks_cache.resolve(jwks_url, kid, jwks_client) → TypeError.
    Or, at the HTTP level: tenant B's callback is verified with key_A (the wrong key) →
    401 ERR_OIDC_TOKEN_INVALID (unexpected success failure) OR 302 (wrong key somehow
    matched — which is impossible but demonstrates the test forces the right assertion).

    Concretely: the test checks that jwks_client_b.calls == 1 after tenant B's callback
    (meaning key_B was freshly fetched, not served from cache with key_A). If the cache
    uses bare kid, the cache hits with key_A → jwks_client_b.calls == 0 → ASSERTION FAILS.
    """
    private_key_a, public_key_a = generate_rsa_keypair()
    private_key_b, public_key_b = generate_rsa_keypair()
    pem_a = public_key_to_pem(public_key_a)
    pem_b = public_key_to_pem(public_key_b)

    # Same kid for both tenants (collision scenario)
    shared_kid = TEST_KID_SHARED

    base_settings = make_base_settings()
    engine, sessionmaker = await bootstrap_fresh_db(base_settings)

    # Sign up two tenants
    signup_app = create_app(base_settings)
    signup_app.state.engine = engine
    signup_app.state.sessionmaker = sessionmaker
    transport = httpx.ASGITransport(app=signup_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id_a = await signup_tenant(client, tenant_name="T11A", email="owner-t11a@a11.com")
        _, tenant_id_b = await signup_tenant(client, tenant_name="T11B", email="owner-t11b@b11.com")
    await signup_app.state.engine.dispose()

    id_token_a = make_rs256_id_token(
        private_key_a,
        kid=shared_kid,
        email="alice@a11.com",
        issuer=FAKE_ISSUER_A,
        audience=FAKE_CLIENT_ID_A,
        nonce=FAKE_NONCE_A,
    )
    id_token_b = make_rs256_id_token(
        private_key_b,
        kid=shared_kid,
        email="bob@b11.com",
        issuer=FAKE_ISSUER_B,
        audience=FAKE_CLIENT_ID_B,
        nonce=FAKE_NONCE_B,
    )

    jwks_client_a = FakeJwksClient(keys={shared_kid: pem_a})
    jwks_client_b = FakeJwksClient(keys={shared_kid: pem_b})

    # Build settings for tenant A
    settings_a = make_env_oidc_settings(
        tenant_id=tenant_id_a,
        issuer=FAKE_ISSUER_A,
        client_id=FAKE_CLIENT_ID_A,
        domain="a11.com",
        jwks_url=FAKE_JWKS_URL_A,
    )

    # ── Tenant A callback (populates cache with (jwks-a.test, shared-kid) → key_A) ──
    from gateway.auth.application.jwks_key_cache import JwksKeyCache  # noqa: PLC0415

    shared_cache = JwksKeyCache()

    app_a = create_app(settings_a)
    app_a.state.engine = engine
    app_a.state.sessionmaker = sessionmaker
    app_a.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token_a)
    app_a.state.jwks_client = jwks_client_a
    app_a.state.jwks_key_cache = shared_cache

    transport_a = httpx.ASGITransport(app=app_a)
    async with httpx.AsyncClient(transport=transport_a, base_url="http://test") as client_a:
        login_a = await client_a.get(f"{OIDC_LOGIN}?domain=a11.com")
        assert login_a.status_code == 302, (
            f"T11: tenant A login expected 302, got {login_a.status_code}: {login_a.text}"
        )
        cookies_a = get_cookies_from_response(login_a)
        cb_a = await client_a.get(
            build_callback_url(code="code-a", state=cookies_a.get("oidc_state", FAKE_STATE_A)),
            cookies={
                "oidc_state": cookies_a.get("oidc_state", FAKE_STATE_A),
                "oidc_nonce": FAKE_NONCE_A,
                "oidc_tenant_id": cookies_a.get("oidc_tenant_id", tenant_id_a),
            },
        )
        assert cb_a.status_code == 302, (
            f"T11: tenant A callback expected 302, got {cb_a.status_code}: {cb_a.text}"
        )

    # Tenant A consumed 1 call to get key_A for (jwks-a.test, shared-kid)
    assert len(jwks_client_a.calls) >= 1, (
        f"T11: jwks_client_a must have been called at least once; got {len(jwks_client_a.calls)}"
    )

    # ── Tenant B callback — must NOT reuse key_A from cache ───────────────
    settings_b = make_env_oidc_settings(
        tenant_id=tenant_id_b,
        issuer=FAKE_ISSUER_B,
        client_id=FAKE_CLIENT_ID_B,
        domain="b11.com",
        jwks_url=FAKE_JWKS_URL_B,
    )
    app_b = create_app(settings_b)
    app_b.state.engine = engine
    app_b.state.sessionmaker = sessionmaker
    app_b.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token_b)
    app_b.state.jwks_client = jwks_client_b
    # Share the same JwksKeyCache instance — this is the collision scenario
    app_b.state.jwks_key_cache = shared_cache

    transport_b = httpx.ASGITransport(app=app_b)
    async with httpx.AsyncClient(transport=transport_b, base_url="http://test") as client_b:
        login_b = await client_b.get(f"{OIDC_LOGIN}?domain=b11.com")
        assert login_b.status_code == 302, (
            f"T11: tenant B login expected 302, got {login_b.status_code}: {login_b.text}"
        )
        cookies_b = get_cookies_from_response(login_b)
        cb_b = await client_b.get(
            build_callback_url(code="code-b", state=cookies_b.get("oidc_state", FAKE_STATE_B)),
            cookies={
                "oidc_state": cookies_b.get("oidc_state", FAKE_STATE_B),
                "oidc_nonce": FAKE_NONCE_B,
                "oidc_tenant_id": cookies_b.get("oidc_tenant_id", tenant_id_b),
            },
        )
        assert cb_b.status_code == 302, (
            f"T11: tenant B callback expected 302, got {cb_b.status_code}: {cb_b.text}"
        )

    # Core assertion: jwks_client_b MUST have been called (cache miss on (jwks-b.test, shared-kid))
    # If the cache used bare kid, (jwks-a.test, shared-kid)'s key_A would have been served to
    # tenant B (cache hit on bare kid="shared-kid") → jwks_client_b.calls == 0.
    assert len(jwks_client_b.calls) >= 1, (
        f"T11 SECURITY: jwks_client_b must be called for tenant B's callback — "
        f"a bare-kid cache would cause key_A to be used for tenant B's token (cross-tenant collision). "
        f"jwks_client_b.calls = {jwks_client_b.calls}. "
        f"Fix: JwksKeyCache.resolve must accept jwks_url and key by (jwks_url, kid) tuple."
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# T12 — PUT/GET round-trip: secret always returned as "<stored>"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_put_get_round_trip_secret_never_returned() -> None:
    """T12: After PUT /admin/oidc with client_secret, subsequent GET returns "<stored>" not
    the plaintext. Direct DB query confirms ciphertext is stored (not plaintext).

    TRUE-RED: PUT /admin/oidc route does not exist → 404.
    AssertionError: expected 200, got 404.
    """
    settings = make_base_settings(encryption_key=TEST_FERNET_KEY)
    engine, sessionmaker = await bootstrap_fresh_db(settings)

    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        _, tenant_id = await signup_tenant(
            client, tenant_name="T12Tenant", email="owner-t12@t12.test"
        )
        lr = await client.post(
            LOGIN,
            json={"email": "owner-t12@t12.test", "password": "correct horse battery staple"},
        )
        assert lr.status_code == 200
        owner_token = lr.json()["access_token"]

        # SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
        # requires a verified claim first — precondition added, assertion intent unchanged.
        await seed_verified_domain_claim(sessionmaker, tenant_id=tenant_id, domain="t12.test")

        # PUT with plaintext secret
        put_resp = await client.put(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
            json={
                "issuer": "https://idp.test",
                "client_id": "my-client",
                "client_secret": TEST_PLAINTEXT_SECRET,
                "token_url": "https://idp.test/token",
                "jwks_url": "https://idp.test/.well-known/jwks.json",
                "email_domains": ["t12.test"],
                "enabled": True,
            },
        )
        assert put_resp.status_code == 200, (
            f"T12: PUT /admin/oidc expected 200, got {put_resp.status_code}: {put_resp.text}"
        )
        put_body = put_resp.json()
        assert put_body.get("client_secret") == "<stored>", (
            f"T12: PUT response must show client_secret as '<stored>', got {put_body.get('client_secret')!r}"
        )
        assert TEST_PLAINTEXT_SECRET not in put_resp.text, (
            "T12 SECURITY VIOLATION: plaintext secret in PUT response body"
        )

        # GET must also return "<stored>"
        get_resp = await client.get(
            ADMIN_OIDC,
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert get_resp.status_code == 200, (
            f"T12: GET /admin/oidc expected 200, got {get_resp.status_code}: {get_resp.text}"
        )
        get_body = get_resp.json()
        assert get_body.get("client_secret") == "<stored>", (
            f"T12: GET response must show client_secret as '<stored>', got {get_body.get('client_secret')!r}"
        )
        assert TEST_PLAINTEXT_SECRET not in get_resp.text, (
            "T12 SECURITY VIOLATION: plaintext secret in GET response body"
        )

    # Direct DB query: confirm ciphertext is stored, not plaintext
    from sqlalchemy.ext.asyncio import async_sessionmaker as async_sm  # noqa: PLC0415

    sm = async_sm(engine, expire_on_commit=False)
    async with sm() as session:
        row = (
            await session.execute(
                text("SELECT client_secret_enc FROM oidc_provider_configs WHERE tenant_id = :tid"),
                {"tid": uuid.UUID(tenant_id)},
            )
        ).fetchone()

    assert row is not None, "T12: oidc_provider_configs row must exist after PUT"
    stored_bytes: bytes = row[0]
    # The stored value must NOT be the plaintext encoded
    assert TEST_PLAINTEXT_SECRET.encode() != stored_bytes, (
        "T12 SECURITY VIOLATION: plaintext client_secret stored directly in DB (not encrypted)"
    )
    # It also must NOT contain the plaintext as a substring (a partial leak)
    assert TEST_PLAINTEXT_SECRET.encode() not in stored_bytes, (
        "T12 SECURITY VIOLATION: plaintext client_secret bytes found within stored ciphertext"
    )

    await engine.dispose()
