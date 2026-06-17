"""Failing-first (RED) suite for provider-config-admin-api (TASK.md §4, contract FROZEN @ v1).

One test per scenario in §2 SCENARIOS (M1–M9, R1–R5, SEC-tenant, SEC-log) + the
production-wiring regression (WIRING, v5 fold).

This is the BYOK **WRITE path**: an authenticated tenant OWNER manages their OWN per-provider
credentials (create/replace · list · inspect-status · delete), persisted into the task-1
Fernet-at-rest store. Secrets are write-only — accepted as plaintext on the way IN, NEVER
returned in any response and NEVER logged.

TRUE-RED RULE: every test asserts TARGET behavior against the contracted surface
``provider_keys_admin_router`` @ ``/admin/provider-keys``. That router does not exist yet and is
NOT registered in main.py, so every PUT/GET/DELETE hits FastAPI's default 404 (no route) — each
test therefore fails NOW for the RIGHT reason:

  M1–M9 (happy paths)      : route missing → 404 default body; expected 200/204 + a status body.
  R1 (unknown provider)    : route missing → 404 default; expected 422 ERR_PROVIDER_UNKNOWN.
  R2 (incomplete azure aad): route missing → 404 default; expected 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE.
                             Post-build the code MUST come from the value-object validator (flat
                             optional-field request body), NOT FastAPI's own 422 — the assertion
                             on the exact code forces that.
  R3 (missing token)       : route missing → 404 default; expected 401 ERR_AUTH_INVALID_TOKEN.
  R4 (non-owner)           : route missing → 404 default; expected 403 ERR_AUTH_FORBIDDEN.
  R5 (absent provider)     : route missing → 404 default body; expected 404 ERR_PROVIDER_KEY_NOT_FOUND
                             (the default 404 carries no ``code`` field → assert_problem fails).
  SEC-tenant               : tenant-scoping is JWT-intrinsic; route missing → 404.
  SEC-log                  : route missing → no successful PUT; expected 200 + no secret in logs.
  WIRING                   : create_app must mount the router; route missing → 404.

Infrastructure (mirrors tests/oidc_tenant_config):
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (autouse usage-leak clear in conftest)
  - httpx.ASGITransport (no network for gateway calls)
  - Self-contained per test: create_app + bootstrap_fresh_db + a per-suite Fernet key
  - signup_tenant() → owner JWT; app.state.token_service.issue() → member JWT (non-owner case)
  - Assertions read the real DbTenantProviderKeyStore via app.state.tenant_provider_key_store
  - asyncio_mode = "auto" (pyproject.toml)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.proxy.domain.provider_credentials import (
    AzureCredential,
    BearerCredential,
    BedrockCredential,
)
from gateway.tenants.domain.entities import Role

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PROVIDER_KEYS = "/admin/provider-keys"

# ---------------------------------------------------------------------------
# Test parameters
# ---------------------------------------------------------------------------
TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_REDIS_URL = "redis://localhost:6380/9"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_FERNET_KEY = Fernet.generate_key().decode()
PASSWORD = "correct horse battery staple"  # noqa: S105

# Sentinel secrets — never expected back in any response or log line.
BEARER_SECRET = "sk-or-123-never-return-me"  # noqa: S105
AWS_SECRET = "aws-secret-access-key-never-return-me"  # noqa: S105
AWS_SESSION = "aws-session-token-never-return-me"  # noqa: S105
AZURE_API_KEY = "azure-api-key-never-return-me"  # noqa: S105
AZURE_CLIENT_SECRET = "azure-client-secret-never-return-me"  # noqa: S105
AZURE_ENDPOINT = "https://my-resource.openai.azure.com"
AZURE_API_VERSION = "2024-02-01"


# ---------------------------------------------------------------------------
# Settings + bootstrap helpers (mirror tests/oidc_tenant_config)
# ---------------------------------------------------------------------------


def make_settings(*, encryption_key: str = TEST_FERNET_KEY) -> Settings:
    """Build Settings with the provider-key Fernet key set (BYOK store needs it)."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        provider_key_encryption_key=encryption_key,
    )


async def bootstrap_fresh_db(settings: Settings) -> tuple[Any, Any]:
    """Create fresh schema; return (engine, sessionmaker)."""
    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker


async def make_app(settings: Settings, engine: Any, sessionmaker: Any) -> Any:
    """create_app wired to the bootstrap engine/sessionmaker."""
    app = create_app(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    return app


def client_for(app: Any) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def signup_tenant(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = PASSWORD,
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


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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


def store_of(app: Any) -> Any:
    """The real DbTenantProviderKeyStore wired on app.state (the contract's store seam)."""
    return app.state.tenant_provider_key_store


class _CapturingLogHandler(logging.Handler):
    """Captures log records for inspection (mirrors oidc T3)."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


# Request-body builders (the contract's provider-discriminated PUT body) ------


def bearer_body(*, secret: str = BEARER_SECRET, enabled: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"secret": secret}
    if enabled is not None:
        body["enabled"] = enabled
    return body


def bedrock_body() -> dict[str, Any]:
    return {
        "access_key_id": "AKIAIOSFODNN7EXAMPLE",
        "secret_access_key": AWS_SECRET,
        "region": "us-east-1",
        "session_token": AWS_SESSION,
    }


def azure_aad_body() -> dict[str, Any]:
    return {
        "mode": "aad",
        "tenant_id": "11111111-2222-3333-4444-555555555555",
        "client_id": "client-abc",
        "client_secret": AZURE_CLIENT_SECRET,
        "endpoint": AZURE_ENDPOINT,
        "api_version": AZURE_API_VERSION,
    }


def azure_api_key_body() -> dict[str, Any]:
    return {
        "mode": "api_key",
        "api_key": AZURE_API_KEY,
        "endpoint": AZURE_ENDPOINT,
        "api_version": AZURE_API_VERSION,
    }


# ===========================================================================
# M1 — Owner creates a bearer credential
# ===========================================================================


@pytest.mark.asyncio
async def test_M1_owner_creates_bearer() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M1", email="owner-m1@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body()
        )
        assert resp.status_code == 200, (
            f"M1: PUT openrouter expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "openrouter"
        assert body["configured"] is True
        assert body["enabled"] is True
        # Secret is write-only: never echoed in the response.
        assert BEARER_SECRET not in resp.text, "M1 SECURITY: bearer secret leaked in PUT response"

        cred = await store_of(app).get(uuid.UUID(tenant_id), "openrouter")
        assert isinstance(cred, BearerCredential)
        assert cred.secret.get_secret_value() == BEARER_SECRET

    await engine.dispose()


# ===========================================================================
# M2 — Owner creates a bedrock credential with a session token
# ===========================================================================


@pytest.mark.asyncio
async def test_M2_owner_creates_bedrock_with_session_token() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M2", email="owner-m2@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/bedrock", headers=auth(token), json=bedrock_body()
        )
        assert resp.status_code == 200, (
            f"M2: PUT bedrock expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "bedrock"
        assert body["configured"] is True
        assert body["auth_mode"] is None
        assert AWS_SECRET not in resp.text and AWS_SESSION not in resp.text, (
            "M2 SECURITY: AWS secret/session leaked in response"
        )

        cred = await store_of(app).get(uuid.UUID(tenant_id), "bedrock")
        assert isinstance(cred, BedrockCredential)
        assert cred.access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert cred.secret_access_key.get_secret_value() == AWS_SECRET
        assert cred.region == "us-east-1"
        assert cred.session_token is not None
        assert cred.session_token.get_secret_value() == AWS_SESSION

    await engine.dispose()


# ===========================================================================
# M3 — Owner creates an azure aad credential
# ===========================================================================


@pytest.mark.asyncio
async def test_M3_owner_creates_azure_aad() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M3", email="owner-m3@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure", headers=auth(token), json=azure_aad_body()
        )
        assert resp.status_code == 200, (
            f"M3: PUT azure aad expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "azure"
        assert body["auth_mode"] == "aad"
        assert AZURE_CLIENT_SECRET not in resp.text, (
            "M3 SECURITY: azure client_secret leaked in response"
        )

        cred = await store_of(app).get(uuid.UUID(tenant_id), "azure")
        assert isinstance(cred, AzureCredential)
        assert cred.mode == "aad"
        assert cred.client_secret is not None
        assert cred.client_secret.get_secret_value() == AZURE_CLIENT_SECRET

    await engine.dispose()


# ===========================================================================
# M4 — Owner creates an azure api_key credential
# ===========================================================================


@pytest.mark.asyncio
async def test_M4_owner_creates_azure_api_key() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M4", email="owner-m4@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure", headers=auth(token), json=azure_api_key_body()
        )
        assert resp.status_code == 200, (
            f"M4: PUT azure api_key expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "azure"
        assert body["auth_mode"] == "api_key"
        assert AZURE_API_KEY not in resp.text, "M4 SECURITY: azure api_key leaked in response"

        cred = await store_of(app).get(uuid.UUID(tenant_id), "azure")
        assert isinstance(cred, AzureCredential)
        assert cred.mode == "api_key"
        assert cred.api_key is not None
        assert cred.api_key.get_secret_value() == AZURE_API_KEY

    await engine.dispose()


# ===========================================================================
# M5 — Owner lists configured providers without secrets
# ===========================================================================


@pytest.mark.asyncio
async def test_M5_list_no_secrets() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(client, tenant_name="M5", email="owner-m5@byok.io")
        await client.put(f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body())
        await client.put(f"{PROVIDER_KEYS}/bedrock", headers=auth(token), json=bedrock_body())

        resp = await client.get(PROVIDER_KEYS, headers=auth(token))
        assert resp.status_code == 200, (
            f"M5: GET list expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        providers = {entry["provider"] for entry in body["keys"]}
        assert providers == {"openrouter", "bedrock"}, f"M5: unexpected providers {providers}"
        for secret in (BEARER_SECRET, AWS_SECRET, AWS_SESSION):
            assert secret not in resp.text, f"M5 SECURITY: {secret!r} leaked in list response"

    await engine.dispose()


# ===========================================================================
# M6 — Owner inspects one provider's status
# ===========================================================================


@pytest.mark.asyncio
async def test_M6_get_one_status() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(client, tenant_name="M6", email="owner-m6@byok.io")
        await client.put(f"{PROVIDER_KEYS}/azure", headers=auth(token), json=azure_aad_body())

        resp = await client.get(f"{PROVIDER_KEYS}/azure", headers=auth(token))
        assert resp.status_code == 200, (
            f"M6: GET azure status expected 200, got {resp.status_code}: {resp.text}"
        )
        body = resp.json()
        assert body["provider"] == "azure"
        assert body["configured"] is True
        assert body["auth_mode"] == "aad"
        assert AZURE_CLIENT_SECRET not in resp.text, "M6 SECURITY: client_secret leaked"

    await engine.dispose()


# ===========================================================================
# M7 — Owner deletes a credential and the BYOK loop closes
# ===========================================================================


@pytest.mark.asyncio
async def test_M7_delete_closes_loop() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M7", email="owner-m7@byok.io")
        await client.put(f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body())

        resp = await client.delete(f"{PROVIDER_KEYS}/openrouter", headers=auth(token))
        assert resp.status_code == 204, (
            f"M7: DELETE expected 204, got {resp.status_code}: {resp.text}"
        )

        assert await store_of(app).get(uuid.UUID(tenant_id), "openrouter") is None
        list_resp = await client.get(PROVIDER_KEYS, headers=auth(token))
        providers = {e["provider"] for e in list_resp.json()["keys"]}
        assert "openrouter" not in providers, "M7: deleted provider still listed"

    await engine.dispose()


# ===========================================================================
# M8 — Owner disables a credential via enabled=false
# ===========================================================================


@pytest.mark.asyncio
async def test_M8_disable_via_enabled_false() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M8", email="owner-m8@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/openrouter",
            headers=auth(token),
            json=bearer_body(secret="sk-or-9", enabled=False),
        )
        assert resp.status_code == 200, (
            f"M8: PUT enabled=false expected 200, got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["enabled"] is False

        # A disabled credential is still configured: list shows enabled=false; status getter
        # reflects it. store.get returns None for DISABLED (fail-closed task-2 semantics), so the
        # presence-after-disable assertion is made via the status surface, not get().
        list_resp = await client.get(PROVIDER_KEYS, headers=auth(token))
        entries = {e["provider"]: e for e in list_resp.json()["keys"]}
        assert "openrouter" in entries, "M8: disabled provider must still be configured/listed"
        assert entries["openrouter"]["enabled"] is False

    await engine.dispose()


# ===========================================================================
# M9 — Re-PUT replaces (rotates) the stored secret
# ===========================================================================


@pytest.mark.asyncio
async def test_M9_reput_rotates() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="M9", email="owner-m9@byok.io")
        await client.put(
            f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body(secret="sk-or-old")
        )
        await client.put(
            f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body(secret="sk-or-new")
        )

        cred = await store_of(app).get(uuid.UUID(tenant_id), "openrouter")
        assert isinstance(cred, BearerCredential)
        assert cred.secret.get_secret_value() == "sk-or-new", "M9: re-PUT must rotate the secret"

        # Upsert, not insert: exactly one openrouter row for the tenant.
        statuses = await store_of(app).list(uuid.UUID(tenant_id))
        openrouter_rows = [s for s in statuses if s.provider == "openrouter"]
        assert len(openrouter_rows) == 1, (
            f"M9: expected exactly 1 openrouter row, got {len(openrouter_rows)}"
        )

    await engine.dispose()


# ===========================================================================
# R1 — Unknown provider is rejected
# ===========================================================================


@pytest.mark.asyncio
async def test_R1_unknown_provider_422() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="R1", email="owner-r1@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/notaprovider", headers=auth(token), json=bearer_body()
        )
        assert_problem(resp, 422, "ERR_PROVIDER_UNKNOWN")

        statuses = await store_of(app).list(uuid.UUID(tenant_id))
        assert statuses == [], "R1: no credential row may be created for an unknown provider"

    await engine.dispose()


# ===========================================================================
# R2 — Incomplete azure aad body is rejected
# ===========================================================================


@pytest.mark.asyncio
async def test_R2_incomplete_azure_aad_422() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="R2", email="owner-r2@byok.io")
        # aad mode missing client_secret → value-object validator raises
        # ERR_PROVIDER_CREDENTIAL_INCOMPLETE → 422 (NOT FastAPI's own 422).
        incomplete = {
            "mode": "aad",
            "tenant_id": "11111111-2222-3333-4444-555555555555",
            "client_id": "client-abc",
            "endpoint": AZURE_ENDPOINT,
            "api_version": AZURE_API_VERSION,
        }
        resp = await client.put(f"{PROVIDER_KEYS}/azure", headers=auth(token), json=incomplete)
        assert_problem(resp, 422, "ERR_PROVIDER_CREDENTIAL_INCOMPLETE")

        assert await store_of(app).get(uuid.UUID(tenant_id), "azure") is None, (
            "R2: nothing may be persisted for an incomplete body"
        )

    await engine.dispose()


# ===========================================================================
# R3 — Missing bearer token is rejected
# ===========================================================================


@pytest.mark.asyncio
async def test_R3_missing_token_401() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        # Need a tenant to exist so the only failing dimension is the missing token.
        await signup_tenant(client, tenant_name="R3", email="owner-r3@byok.io")
        resp = await client.put(f"{PROVIDER_KEYS}/openrouter", json=bearer_body(secret="x"))
        assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")

    await engine.dispose()


# ===========================================================================
# R4 — A non-owner is forbidden
# ===========================================================================


@pytest.mark.asyncio
async def test_R4_non_owner_403() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        _owner_token, tenant_id = await signup_tenant(
            client, tenant_name="R4", email="owner-r4@byok.io"
        )
        # Mint a same-tenant MEMBER token directly (avoids needing a member-signup path).
        member_jwt, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            role=Role.MEMBER,
            email="member-r4@byok.io",
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/openrouter", headers=auth(member_jwt), json=bearer_body(secret="x")
        )
        assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

        statuses = await store_of(app).list(uuid.UUID(tenant_id))
        assert statuses == [], "R4: a forbidden write must not create a row"

    await engine.dispose()


# ===========================================================================
# R5 — Status/delete of an absent provider is not found
# ===========================================================================


@pytest.mark.asyncio
async def test_R5_absent_provider_404() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(client, tenant_name="R5", email="owner-r5@byok.io")
        get_resp = await client.get(f"{PROVIDER_KEYS}/azure", headers=auth(token))
        assert_problem(get_resp, 404, "ERR_PROVIDER_KEY_NOT_FOUND")

        del_resp = await client.delete(f"{PROVIDER_KEYS}/azure", headers=auth(token))
        assert_problem(del_resp, 404, "ERR_PROVIDER_KEY_NOT_FOUND")

    await engine.dispose()


# ===========================================================================
# R6 — Missing encryption key is a clean error, not a raw 500 (verify-gate hardening)
# ===========================================================================
# Post-freeze, human-approved at the risk:high verify gate (Tin Dang 2026-06-16): when
# GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY is unset the store raises
# ProviderCredentialError("ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"). The admin API must surface
# that as a mapped RFC-9457 problem (mirroring OIDC's 409 encryption-not-configured), never a raw
# 500 — design-for-failure on the store IO path. No secret is involved (the guard fires before any
# encryption attempt).


@pytest.mark.asyncio
async def test_R6_missing_encryption_key_clean_error() -> None:
    settings = make_settings(encryption_key="")
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(client, tenant_name="R6", email="owner-r6@byok.io")
        resp = await client.put(
            f"{PROVIDER_KEYS}/openrouter", headers=auth(token), json=bearer_body()
        )
        assert_problem(resp, 409, "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE")

        # Guard fires before any IO: nothing persisted (list needs no decryption key).
        assert await store_of(app).list(uuid.UUID(tenant_id)) == [], (
            "R6: a config-error PUT must not create a row"
        )

    await engine.dispose()


# ===========================================================================
# SEC-tenant — A tenant cannot touch another tenant's credentials
# ===========================================================================


@pytest.mark.asyncio
async def test_SEC_tenant_isolation() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token_a, tenant_a = await signup_tenant(
            client, tenant_name="TenantA", email="owner-a@byok.io"
        )
        token_b, _tenant_b = await signup_tenant(
            client, tenant_name="TenantB", email="owner-b@byok.io"
        )
        # A configures openrouter.
        await client.put(f"{PROVIDER_KEYS}/openrouter", headers=auth(token_a), json=bearer_body())

        # B's list must not include A's credential.
        list_b = await client.get(PROVIDER_KEYS, headers=auth(token_b))
        assert list_b.status_code == 200, list_b.text
        assert list_b.json()["keys"] == [], "SEC-tenant: B must not see A's credentials"

        # B's delete of openrouter is a 404 for B (nothing in B's scope).
        del_b = await client.delete(f"{PROVIDER_KEYS}/openrouter", headers=auth(token_b))
        assert_problem(del_b, 404, "ERR_PROVIDER_KEY_NOT_FOUND")

        # A's credential remains intact.
        cred_a = await store_of(app).get(uuid.UUID(tenant_a), "openrouter")
        assert isinstance(cred_a, BearerCredential)
        assert cred_a.secret.get_secret_value() == BEARER_SECRET

    await engine.dispose()


# ===========================================================================
# SEC-log — The plaintext secret is never logged
# ===========================================================================


@pytest.mark.asyncio
async def test_SEC_secret_never_logged() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    log_handler = _CapturingLogHandler()
    root_logger = logging.getLogger()
    root_logger.addHandler(log_handler)
    root_logger.setLevel(logging.DEBUG)
    try:
        async with client_for(app) as client:
            token, tenant_id = await signup_tenant(
                client, tenant_name="SECLOG", email="owner-seclog@byok.io"
            )
            resp = await client.put(
                f"{PROVIDER_KEYS}/openrouter",
                headers=auth(token),
                json=bearer_body(secret="sk-or-LOGCHECK"),
            )
            assert resp.status_code == 200, (
                f"SEC-log: PUT expected 200, got {resp.status_code}: {resp.text}"
            )
            cred = await store_of(app).get(uuid.UUID(tenant_id), "openrouter")
            assert isinstance(cred, BearerCredential)
            assert cred.secret.get_secret_value() == "sk-or-LOGCHECK"
    finally:
        root_logger.removeHandler(log_handler)

    for record in log_handler.records:
        assert "sk-or-LOGCHECK" not in record.getMessage(), (
            f"SEC-log SECURITY VIOLATION: secret found in log line: {record.getMessage()[:300]}"
        )

    await engine.dispose()


# ===========================================================================
# WIRING — create_app mounts the router (production-wiring regression, v5 fold)
# ===========================================================================


@pytest.mark.asyncio
async def test_WIRING_router_registered() -> None:
    settings = make_settings()
    engine, sessionmaker = await bootstrap_fresh_db(settings)
    app = await make_app(settings, engine, sessionmaker)

    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="WIRE", email="owner-wire@byok.io"
        )
        # A real GET reaches the mounted router (empty list), not a 404 missing-route.
        resp = await client.get(PROVIDER_KEYS, headers=auth(token))
        assert resp.status_code == 200, (
            f"WIRING: create_app must mount provider_keys_admin_router; got "
            f"{resp.status_code}: {resp.text}"
        )
        assert resp.json() == {"keys": []}

    await engine.dispose()
