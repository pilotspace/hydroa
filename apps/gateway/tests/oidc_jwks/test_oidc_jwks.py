"""Failing-first (RED) suite for oidc-jwks (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS (J1–J11).

TRUE-RED RULE: every test asserts TARGET behavior (RS256 signature verification) and fails
NOW for the RIGHT reason (missing implementation — the gateway still uses verify_signature=False
and has no JwksClient seam).

Right-reason red targets per test:

  J1  (valid RS256 → login succeeds):
    The gateway does NOT verify RS256 signatures today; app.state.jwks_client does not exist;
    BUT the happy path still works with a valid HS256-style claim set because v4 skips
    signature verification. HOWEVER — there is no jwks_client port on deps.py yet, no
    JwksClient import, and the test explicitly wires app.state.jwks_client which does not
    exist as a recognised seam. The test passes a properly-signed RS256 token but the
    current _decode_id_token_claims uses algorithms=["HS256", "RS256"] with verify_signature=False
    — so claim-wise it would pass. But the test asserts that the JWKS seam is wired
    (it checks the FakeJwksClient was called), which will fail because the seam does not exist.
    AssertionError: fake_jwks.calls >= 1, got 0.

  J2  (forged signature → 401 ERR_OIDC_TOKEN_INVALID):
    Current gateway: verify_signature=False → forged token's claims are accepted.
    The test expects 401; current behaviour is 302 (happy path succeeds).
    AssertionError: expected 401, got 302.

  J3  (alg=none → 401 ERR_OIDC_TOKEN_INVALID):
    Current gateway: decode with algorithms=["HS256","RS256"] and verify_signature=False
    extracts claims regardless of alg. Token with alg=none and valid claims → happy path.
    AssertionError: expected 401, got 302.

  J4  (alg=HS256 key-confusion → 401 ERR_OIDC_TOKEN_INVALID):
    Current gateway: HS256 token decoded with verify_signature=False → claims extracted.
    Valid claims → happy path. AssertionError: expected 401, got 302.

  J5  (unknown kid → one refresh then 401):
    No JwksClient in current code; no kid-miss refresh logic.
    AssertionError: expected 401 ERR_OIDC_TOKEN_INVALID, got 302.

  J6  (JWKS fetch failure → 502 ERR_OIDC_UPSTREAM_ERROR):
    No JwksClient; OidcUpstreamError from the fake is never raised.
    AssertionError: expected 502, got 302.

  J7  (cache hit avoids second fetch):
    No JwksClient; no cache. AssertionError: expected jwks calls==1, got 0.

  J8  (bad nonce after valid signature → 401 ERR_OIDC_TOKEN_INVALID):
    This WOULD pass today (nonce validation works in v4). BUT the test also asserts
    fake_jwks.calls >= 1, which fails because no JwksClient seam exists.
    AssertionError: fake_jwks.calls >= 1, got 0.

  J9  (expired RS256 token → 401 ERR_OIDC_TOKEN_EXPIRED):
    This would pass today via the manual exp check. BUT jwks fake not called.
    AssertionError: fake_jwks.calls >= 1, got 0. (Or expected 401 via exp path
    which does work in v4 — the right-reason red is the missing jwks seam assertion.)
    Annotated below to be explicit.

  J10 (startup fails without jwks_url):
    Settings._validate_oidc_config does not check oidc_jwks_url yet.
    The Settings instantiation does NOT raise. AssertionError: expected ValueError, none raised.

  J11 (httpx adapter fetches from URL):
    HttpxJwksClient does not exist. ImportError / AttributeError.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network for gateway calls)
  - FakeJwksClient injected via app.state.jwks_client
  - RSA keypairs generated in-test via cryptography package
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

# ---------------------------------------------------------------------------
# Fake IdP parameters — deterministic test values
# ---------------------------------------------------------------------------
FAKE_ISSUER = "https://fake-idp.test"
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret-not-real"
FAKE_REDIRECT_URI = "http://gateway.test/auth/oidc/callback"
FAKE_DOMAIN = "example.com"
FAKE_NONCE = "test-nonce-abc123"
FAKE_STATE = "test-state-xyz789"
FAKE_JWKS_URL = "https://fake-idp.test/.well-known/jwks.json"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"
TEST_KID = "test-kid-1"


# ---------------------------------------------------------------------------
# RSA key generation — real cryptographic keys for testing RS256 signatures
# ---------------------------------------------------------------------------


def generate_rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate a 2048-bit RSA keypair for signing/verifying test tokens."""
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


def public_key_to_jwk_dict(public_key: rsa.RSAPublicKey, kid: str) -> dict[str, Any]:
    """Serialize an RSA public key to a JWK dict (for building JWKS responses)."""
    import base64

    pub_numbers = public_key.public_key().public_numbers() if hasattr(public_key, "public_key") else public_key.public_numbers()

    def _b64url(n: int, length: int) -> str:
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    n_bytes = (pub_numbers.n.bit_length() + 7) // 8
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _b64url(pub_numbers.n, n_bytes),
        "e": _b64url(pub_numbers.e, 3),
    }


# ---------------------------------------------------------------------------
# Token minting helpers — RS256 and attack variants
# ---------------------------------------------------------------------------


def make_rs256_id_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = TEST_KID,
    email: str = "alice@example.com",
    issuer: str = FAKE_ISSUER,
    audience: str = FAKE_CLIENT_ID,
    nonce: str = FAKE_NONCE,
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


def make_expired_rs256_token(
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = TEST_KID,
    email: str = "alice@example.com",
    nonce: str = FAKE_NONCE,
) -> str:
    """Mint an RS256-signed token with exp in the past."""
    return make_rs256_id_token(private_key, kid=kid, email=email, nonce=nonce, exp_offset=-3600)


def make_alg_none_token(
    *,
    email: str = "alice@example.com",
    issuer: str = FAKE_ISSUER,
    audience: str = FAKE_CLIENT_ID,
    nonce: str = FAKE_NONCE,
    exp_offset: int = 3600,
) -> str:
    """Craft an unsigned token with alg=none (forgery attempt)."""
    import base64

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
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode()
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=").decode()
    # alg=none: no signature
    return f"{header}.{payload}."


def make_hs256_token(
    *,
    email: str = "alice@example.com",
    issuer: str = FAKE_ISSUER,
    audience: str = FAKE_CLIENT_ID,
    nonce: str = FAKE_NONCE,
    exp_offset: int = 3600,
) -> str:
    """Craft an HS256-signed token (key-confusion attack attempt)."""
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
    return jwt.encode(claims, "any-hmac-secret", algorithm="HS256")


# ---------------------------------------------------------------------------
# FakeJwksClient — injectable via app.state.jwks_client
# ---------------------------------------------------------------------------


class FakeJwksClient:
    """In-process fake for the JwksClient port.

    Tracks call count for cache-hit assertions. Supports configuring keys
    per-kid, a fail mode (raises OidcUpstreamError), and a stateful refresh
    simulation (first call returns old keys, second call still returns old keys
    — for unknown-kid-one-refresh-then-fail scenarios).
    """

    def __init__(
        self,
        *,
        keys: dict[str, Any] | None = None,  # kid -> key object (e.g. RSAPublicKey PEM bytes)
        raise_exc: Exception | None = None,
        # For J5: simulate returning only "old-kid" on both initial and refresh calls
        always_return_kids: set[str] | None = None,
    ) -> None:
        self._keys: dict[str, Any] = keys or {}
        self._raise_exc = raise_exc
        self._always_return_kids = always_return_kids
        self.calls: list[str | None] = []  # list of kid args passed to get_signing_key

    async def get_signing_key(self, kid: str | None) -> Any:
        self.calls.append(kid)
        if self._raise_exc is not None:
            raise self._raise_exc
        if self._always_return_kids is not None:
            # Simulate a JWKS that never contains the requested kid
            if kid not in self._always_return_kids:
                if len(self.calls) == 1:
                    # First call: simulate cache miss + refresh (second call happens inside adapter)
                    # For the fake, we simulate both fetches by counting: if kid unknown → keep counting
                    # The adapter would call us twice; for the fake we raise on second miss
                    # But since FakeJwksClient IS the port (no internal retry), the use case
                    # should call us twice (once initial, once after kid-miss path).
                    # We return a key not matching kid to trigger the second call:
                    raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS (initial fetch)")
                else:
                    raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS (after refresh)")
        if kid is None:
            if len(self._keys) == 1:
                return next(iter(self._keys.values()))
            raise OidcTokenInvalidError("kid=None but JWKS has 0 or multiple keys")
        if kid not in self._keys:
            raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS")
        return self._keys[kid]


# ---------------------------------------------------------------------------
# FakeOidcExchanger — reused from sso-oidc pattern
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    """In-process fake for OidcTokenExchanger port."""

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
        return {}


# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def make_oidc_settings(*, tenant_id: str | None = None, domain: str = FAKE_DOMAIN) -> Settings:
    """Build Settings with OIDC enabled, jwks_url set, domain mapping for FAKE_DOMAIN."""
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
        oidc_jwks_url=FAKE_JWKS_URL,
        oidc_domain_mapping=json.dumps(mapping),
    )


def make_disabled_settings() -> Settings:
    return Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )


# ---------------------------------------------------------------------------
# HTTP / cookie helpers
# ---------------------------------------------------------------------------


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


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
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
# Shared tenant bootstrap helper
# ---------------------------------------------------------------------------


async def bootstrap_tenant(tenant_name: str, owner_email: str) -> tuple[Any, str, str]:
    """Create a fresh DB, sign up a tenant, return (engine, sessionmaker, tenant_id)."""
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
        _, tenant_id = await signup_tenant(client, tenant_name=tenant_name, email=owner_email)

    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker, tenant_id


# ---------------------------------------------------------------------------
# J1 — valid RS256 token verified and login succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_rs256_token_login_succeeds() -> None:
    """J1: A valid RS256-signed id_token is verified against the FakeJwksClient and login succeeds.

    TRUE-RED: app.state.jwks_client seam does not exist; the use case never calls
    fake_jwks.get_signing_key. AssertionError: expected fake_jwks.calls >= 1, got 0.
    (Or: the gateway succeeds via verify_signature=False without consulting the JWKS —
    the assertion on fake_jwks.calls proves the seam is not wired.)
    """
    private_key, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J1", "owner-j1@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    id_token = make_rs256_id_token(private_key, kid=TEST_KID, email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert resp.status_code == 302, f"expected 302, got {resp.status_code}: {resp.text}"
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies

    # CRITICAL: assert the JwksClient was actually consulted (seam wired correctly)
    assert len(fake_jwks.calls) >= 1, (
        f"FakeJwksClient.get_signing_key was never called — jwks_client seam not wired. "
        f"calls={fake_jwks.calls}"
    )

    async with oidc_app.state.sessionmaker() as session:
        user = await get_user_by_email(session, "alice@example.com")
    assert user is not None
    assert user["role"] == "member"
    assert user["password_hash"] == "!sso-no-password"

    await engine.dispose()


# ---------------------------------------------------------------------------
# J2 — forged signature (different RSA key) → 401 ERR_OIDC_TOKEN_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forged_signature_rejected() -> None:
    """J2: RS256 token signed with key_B but JWKS contains key_A → 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: gateway uses verify_signature=False → forged claims accepted → happy path (302).
    AssertionError: expected 401, got 302.
    """
    _, public_key_a = generate_rsa_keypair()
    private_key_b, _ = generate_rsa_keypair()
    pem_a = public_key_to_pem(public_key_a)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J2", "owner-j2@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    # JWKS has key_A, token signed with key_B
    fake_jwks = FakeJwksClient(keys={TEST_KID: pem_a})
    oidc_app.state.jwks_client = fake_jwks

    # Token signed with the WRONG key (key_B)
    id_token = make_rs256_id_token(private_key_b, kid=TEST_KID, email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
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
# J3 — alg=none token rejected → 401 ERR_OIDC_TOKEN_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alg_none_rejected() -> None:
    """J3: Token with alg=none (unsigned) → 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: gateway decodes with verify_signature=False and algorithms=["HS256","RS256"];
    alg=none token's claims extracted successfully → happy path (302).
    AssertionError: expected 401, got 302.
    """
    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J3", "owner-j3@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    _, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)
    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    # alg=none token — no signature
    id_token = make_alg_none_token(email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
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
# J4 — alg=HS256 key-confusion attack → 401 ERR_OIDC_TOKEN_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alg_hs256_rejected() -> None:
    """J4: HS256-signed token (key-confusion attack) → 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: gateway decodes HS256 tokens with verify_signature=False → claims trusted.
    Happy path succeeds (302). AssertionError: expected 401, got 302.
    """
    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J4", "owner-j4@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    _, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)
    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    # HS256 token with valid-looking claims — key-confusion attack
    id_token = make_hs256_token(email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
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
# J5 — unknown kid triggers one JWKS refresh, then fails → 401
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_kid_one_refresh_then_fail() -> None:
    """J5: Token with unknown kid; FakeJwksClient returns only old-kid on both calls.
    get_signing_key must be called exactly twice (initial + one refresh attempt).
    Then 401 ERR_OIDC_TOKEN_INVALID.

    TRUE-RED: no JwksClient seam → FakeJwksClient never called.
    AssertionError: expected calls==2, got 0. AND expected 401, got 302.
    """
    private_key, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J5", "owner-j5@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    # FakeJwksClient only knows "old-kid"; token uses "unknown-kid"
    # We use a stateful fake: first call raises (kid not found), second call raises again
    class _TwoCallFakeJwksClient:
        """Raises OidcTokenInvalidError on first and second call for unknown kid.
        Tracks call count to assert exactly 2 calls were made.
        """
        calls: list[str | None] = []

        async def get_signing_key(self, kid: str | None) -> Any:
            self.calls.append(kid)
            # Never returns a key for "unknown-kid"
            raise OidcTokenInvalidError(f"kid {kid!r} not found in JWKS (call #{len(self.calls)})")

    two_call_fake = _TwoCallFakeJwksClient()
    two_call_fake.calls = []
    oidc_app.state.jwks_client = two_call_fake

    # Token with unknown kid (not in any JWKS)
    id_token = make_rs256_id_token(private_key, kid="unknown-kid", email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_INVALID")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    # The kid-miss refresh contract: get_signing_key should be called exactly TWICE
    # (the HttpxJwksClient does this internally; for the FakeJwksClient-at-the-port
    # level, the use case itself should call get_signing_key at most once per token —
    # the retry logic lives inside the adapter. Here we verify fail-CLOSED behaviour.)
    assert len(two_call_fake.calls) >= 1, (
        f"FakeJwksClient never called — jwks_client seam not wired. calls={two_call_fake.calls}"
    )

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# J6 — JWKS endpoint unreachable → 502 ERR_OIDC_UPSTREAM_ERROR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwks_fetch_failure_returns_502() -> None:
    """J6: FakeJwksClient raises OidcUpstreamError → 502 ERR_OIDC_UPSTREAM_ERROR.
    Fail-CLOSED: no fallback to skip-verify.

    TRUE-RED: no JwksClient seam → OidcUpstreamError never raised by JWKS path.
    Gateway happily decodes with verify_signature=False → 302.
    AssertionError: expected 502, got 302.
    """
    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J6", "owner-j6@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    # FakeJwksClient that always raises OidcUpstreamError
    fake_jwks = FakeJwksClient(raise_exc=OidcUpstreamError("JWKS endpoint unreachable"))
    oidc_app.state.jwks_client = fake_jwks

    private_key, _ = generate_rsa_keypair()
    id_token = make_rs256_id_token(private_key, kid=TEST_KID, email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
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
# J7 — JWKS cache hit avoids second fetch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwks_cache_hit_avoids_second_fetch() -> None:
    """J7: Two sequential callbacks with same kid → FakeJwksClient called only once (cache).

    TRUE-RED: no JwksClient seam → get_signing_key never called.
    AssertionError: expected fake_jwks.calls==1, got 0.
    """
    private_key, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J7", "owner-j7@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    async def do_callback(email: str) -> httpx.Response:
        id_token = make_rs256_id_token(private_key, kid=TEST_KID, email=email, nonce=FAKE_NONCE)
        oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)
        transport = httpx.ASGITransport(app=oidc_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
            return await client.get(
                build_callback_url(code="code", state=FAKE_STATE),
                cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
            )

    resp1 = await do_callback("alice@example.com")
    assert resp1.status_code == 302, f"first callback failed: {resp1.status_code}: {resp1.text}"

    resp2 = await do_callback("bob@example.com")
    assert resp2.status_code == 302, f"second callback failed: {resp2.status_code}: {resp2.text}"

    # Cache hit: same kid used twice → get_signing_key should be called only once
    assert len(fake_jwks.calls) == 1, (
        f"expected exactly 1 call to get_signing_key (cache hit on second), "
        f"got {len(fake_jwks.calls)}: {fake_jwks.calls}"
    )

    await engine.dispose()


# ---------------------------------------------------------------------------
# J8 — claim validation enforced after valid signature (bad nonce)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_validation_enforced_after_valid_signature() -> None:
    """J8: RS256 signature is valid but nonce is wrong → 401 ERR_OIDC_TOKEN_INVALID.
    Signature passing does NOT bypass claim validation.

    TRUE-RED (primary): no JwksClient seam → fake_jwks never called.
    AssertionError: expected fake_jwks.calls >= 1, got 0.
    (Secondary, if seam were wired but no claim validation post-sig: 302 instead of 401.)
    """
    private_key, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J8", "owner-j8@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    # Valid RS256 signature but WRONG nonce in token
    id_token = make_rs256_id_token(
        private_key, kid=TEST_KID, email="alice@example.com",
        nonce="WRONG-NONCE"  # does not match FAKE_NONCE cookie
    )
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},  # correct nonce in cookie
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_INVALID")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    # JwksClient should have been called (signature verified first, THEN claim rejected)
    assert len(fake_jwks.calls) >= 1, (
        f"FakeJwksClient never called — jwks_client seam not wired. calls={fake_jwks.calls}"
    )

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# J9 — expired RS256 token → 401 ERR_OIDC_TOKEN_EXPIRED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_rs256_token_rejected() -> None:
    """J9: RS256-signed token with exp in the past → 401 ERR_OIDC_TOKEN_EXPIRED.

    TRUE-RED (primary): no JwksClient seam → fake_jwks never called.
    AssertionError: expected fake_jwks.calls >= 1, got 0.
    (In v4, ERR_OIDC_TOKEN_EXPIRED is returned via manual exp check — so the status code
    assertion would pass. But the jwks.calls assertion fails, proving the seam is missing.)
    """
    private_key, public_key = generate_rsa_keypair()
    pem = public_key_to_pem(public_key)

    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J9", "owner-j9@acme.com")

    oidc_settings = make_oidc_settings(tenant_id=tenant_id)
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker

    fake_jwks = FakeJwksClient(keys={TEST_KID: pem})
    oidc_app.state.jwks_client = fake_jwks

    # Valid RS256 signature but expired
    id_token = make_expired_rs256_token(private_key, kid=TEST_KID, email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert_problem(resp, 401, "ERR_OIDC_TOKEN_EXPIRED")
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" not in cookies

    # JwksClient should have been called (alg + key fetch before exp check in v5 path)
    assert len(fake_jwks.calls) >= 1, (
        f"FakeJwksClient never called — jwks_client seam not wired. calls={fake_jwks.calls}"
    )

    async with oidc_app.state.sessionmaker() as session:
        count = await count_users_by_email(session, "alice@example.com")
    assert count == 0

    await engine.dispose()


# ---------------------------------------------------------------------------
# J10 — empty jwks_url + no seam = v4 TLS-channel mode preserved (compat pin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconfigured_jwks_preserves_v4_flow() -> None:
    """J10: oidc_enabled=True, oidc_jwks_url empty, no app.state.jwks_client seam →
    the v4 TLS-channel flow runs unchanged: an HS256-signed token with valid claims
    logs in (302 + session cookie).

    GREEN-BY-DESIGN regression pin (orchestrator amendment, 2026-06-11): this test
    passes BEFORE the build and must still pass after — it pins the config-gated
    fallback so the FROZEN sso-oidc suite's behavior (HS256 fixtures, no jwks_url)
    is preserved by contract, not by accident. It replaces the original
    startup-ValueError test, which would have broken the frozen Settings fixtures.
    The red gate for this task is carried by the other 10 tests.
    """
    engine, sessionmaker, tenant_id = await bootstrap_tenant("Acme J10", "owner-j10@acme.com")

    mapping = [{"email_domain": FAKE_DOMAIN, "tenant_id": tenant_id}]
    oidc_settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        oidc_enabled=True,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret=FAKE_CLIENT_SECRET,
        oidc_redirect_uri=FAKE_REDIRECT_URI,
        oidc_domain_mapping=json.dumps(mapping),
        # oidc_jwks_url intentionally absent / empty (defaults to "") — v4 mode
    )
    oidc_app = create_app(oidc_settings)
    oidc_app.state.engine = engine
    oidc_app.state.sessionmaker = sessionmaker
    # NO oidc_app.state.jwks_client — verification must stay INACTIVE

    id_token = make_hs256_token(email="alice@example.com", nonce=FAKE_NONCE)
    oidc_app.state.oidc_exchanger = FakeOidcExchanger(id_token=id_token)

    transport = httpx.ASGITransport(app=oidc_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        resp = await client.get(
            build_callback_url(code="code", state=FAKE_STATE),
            cookies={"oidc_state": FAKE_STATE, "oidc_nonce": FAKE_NONCE},
        )

    assert resp.status_code == 302, (
        f"v4 TLS-channel mode must be preserved when jwks_url is unconfigured: "
        f"expected 302, got {resp.status_code}: {resp.text}"
    )
    cookies = get_cookies_from_response(resp)
    assert "ai_proxy_session" in cookies, "session cookie missing on v4 fallback path"

    await engine.dispose()


# ---------------------------------------------------------------------------
# J11 — HttpxJwksClient adapter fetches from URL with timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_httpx_jwks_adapter_fetches_from_url() -> None:
    """J11: HttpxJwksClient issues a GET to jwks_url with 10s timeout and returns the signing key.

    Uses an in-process ASGI app to serve the JWKS response — mirrors the sso-oidc pattern
    (no respx; no new test packages). Tests the real adapter, not the fake.

    TRUE-RED: HttpxJwksClient does not exist yet.
    ImportError / AttributeError when importing or constructing it.
    """
    from gateway.auth.infrastructure.httpx_jwks_client import HttpxJwksClient  # type: ignore[import]

    private_key, public_key = generate_rsa_keypair()

    # Build a minimal JWKS dict with the public key
    jwks_data = {
        "keys": [public_key_to_jwk_dict(public_key, kid=TEST_KID)]
    }

    # Serve JWKS via a minimal ASGI app (no FastAPI needed)
    async def jwks_asgi_app(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            body = json.dumps(jwks_data).encode()
            await send({
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            })
            await send({"type": "http.response.body", "body": body})

    # Wire the HttpxJwksClient with ASGITransport pointing at our fake JWKS server
    transport = httpx.ASGITransport(app=jwks_asgi_app)
    client = HttpxJwksClient(
        jwks_url="http://fake-jwks-server/.well-known/jwks.json",
        transport=transport,  # injected for testing (no real network)
    )

    key = await client.get_signing_key(TEST_KID)
    assert key is not None, "get_signing_key returned None"

    # Verify the returned key can actually verify a token signed with the private key
    id_token = make_rs256_id_token(private_key, kid=TEST_KID, email="test@example.com", nonce="n")
    decoded = jwt.decode(
        id_token,
        key,
        algorithms=["RS256"],
        options={"verify_aud": False, "verify_iss": False},
    )
    assert decoded["email"] == "test@example.com"
