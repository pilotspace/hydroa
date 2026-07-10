"""Red suite for S3 write-time SSRF guard on PUT /admin/provider-keys/azure
(edge-input-hardening TASK.md §2/§3 FROZEN @ v1).

Infrastructure mirrors tests/provider_config_admin_api (self-contained: real Postgres,
per-suite Fernet key, httpx.ASGITransport).

RED reason (pre-BUILD): the router does not yet call assert_literal_host_not_denied, so a
metadata-IP endpoint is happily persisted (200) instead of rejected (422
ERR_PROVIDER_ENDPOINT_FORBIDDEN).
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
from cryptography.fernet import Fernet

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

PROVIDER_KEYS = "/admin/provider-keys"
TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_REDIS_URL = "redis://localhost:6380/9"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_FERNET_KEY = Fernet.generate_key().decode()
PASSWORD = "correct horse battery staple"  # noqa: S105


def make_settings(**overrides: Any) -> Settings:
    kwargs: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": TEST_REDIS_URL,
        "provider_key_encryption_key": TEST_FERNET_KEY,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


async def bootstrap_app(settings: Settings) -> Any:
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return app


def client_for(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    sr = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": PASSWORD},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post("/admin/auth/login", json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


# ---------------------------------------------------------------------------
# S3.write-1 — Azure endpoint pointed at the cloud metadata IP rejected at write time
# ---------------------------------------------------------------------------


async def test_s3_write1_metadata_endpoint_rejected_no_persistence() -> None:
    app = await bootstrap_app(make_settings())
    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="S3W1", email="owner-s3w1@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "api_key",
                "api_key": "azure-key-never-return-me",
                "endpoint": "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            },
        )
        assert_problem(resp, 422, "ERR_PROVIDER_ENDPOINT_FORBIDDEN")

        store = app.state.tenant_provider_key_store
        assert await store.get(uuid.UUID(tenant_id), "azure") is None, (
            "no credential row may be persisted for a metadata-IP endpoint"
        )
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-2 — an RFC1918 literal endpoint is rejected by default
# ---------------------------------------------------------------------------


async def test_s3_write2_rfc1918_literal_endpoint_rejected_by_default() -> None:
    app = await bootstrap_app(make_settings())
    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="S3W2", email="owner-s3w2@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "api_key",
                "api_key": "azure-key-never-return-me",
                "endpoint": "https://10.0.0.5/openai",
            },
        )
        assert_problem(resp, 422, "ERR_PROVIDER_ENDPOINT_FORBIDDEN")
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-3 — a real Azure resource hostname (not a literal IP) is NOT rejected
# ---------------------------------------------------------------------------


async def test_s3_write3_hostname_endpoint_not_rejected_at_write_time() -> None:
    """A genuine Azure resource hostname always passes the write-time literal check —
    DNS is deferred to the request-time check (this test proves that non-regression)."""
    app = await bootstrap_app(make_settings())
    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="S3W3", email="owner-s3w3@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "api_key",
                "api_key": "azure-key-never-return-me",
                "endpoint": "https://my-resource.openai.azure.com",
            },
        )
        assert resp.status_code == 200, resp.text
        store = app.state.tenant_provider_key_store
        assert await store.get(uuid.UUID(tenant_id), "azure") is not None
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-4 — an attacker-host `authority` (aad mode) is NOT rejected at write time
# (acknowledged write-time coverage gap; request-time check is authoritative)
# ---------------------------------------------------------------------------


async def test_s3_write4_attacker_hostname_authority_not_rejected_at_write_time() -> None:
    app = await bootstrap_app(make_settings())
    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="S3W4", email="owner-s3w4@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "aad",
                "tenant_id": "11111111-2222-3333-4444-555555555555",
                "client_id": "client-abc",
                "client_secret": "azure-client-secret-never-return-me",
                "endpoint": "https://my-resource.openai.azure.com",
                "authority": "https://attacker.example.com",
            },
        )
        assert resp.status_code == 200, resp.text
        store = app.state.tenant_provider_key_store
        cred = await store.get(uuid.UUID(tenant_id), "azure")
        assert cred is not None
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-5 — a literal-IP `authority` (aad mode) IS rejected at write time
# ---------------------------------------------------------------------------


async def test_s3_write5_literal_metadata_authority_rejected() -> None:
    app = await bootstrap_app(make_settings())
    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="S3W5", email="owner-s3w5@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "aad",
                "tenant_id": "11111111-2222-3333-4444-555555555555",
                "client_id": "client-abc",
                "client_secret": "azure-client-secret-never-return-me",
                "endpoint": "https://my-resource.openai.azure.com",
                "authority": "https://169.254.169.254",
            },
        )
        assert_problem(resp, 422, "ERR_PROVIDER_ENDPOINT_FORBIDDEN")
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-6 — a private-range endpoint IS accepted when the operator opts in
# ---------------------------------------------------------------------------


async def test_s3_write6_private_range_accepted_with_operator_opt_in() -> None:
    app = await bootstrap_app(make_settings(egress_allow_private_ranges=True))
    async with client_for(app) as client:
        token, tenant_id = await signup_tenant(
            client, tenant_name="S3W6", email="owner-s3w6@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "api_key",
                "api_key": "azure-key-never-return-me",
                "endpoint": "https://10.20.30.40/openai",
            },
        )
        assert resp.status_code == 200, resp.text
        store = app.state.tenant_provider_key_store
        assert await store.get(uuid.UUID(tenant_id), "azure") is not None
    await app.state.engine.dispose()


# ---------------------------------------------------------------------------
# S3.write-7 — a metadata endpoint is STILL rejected even with the private-range opt-in
# ---------------------------------------------------------------------------


async def test_s3_write7_metadata_still_rejected_with_private_ranges_opt_in() -> None:
    app = await bootstrap_app(make_settings(egress_allow_private_ranges=True))
    async with client_for(app) as client:
        token, _tenant_id = await signup_tenant(
            client, tenant_name="S3W7", email="owner-s3w7@byok.io"
        )
        resp = await client.put(
            f"{PROVIDER_KEYS}/azure",
            headers=auth(token),
            json={
                "mode": "api_key",
                "api_key": "azure-key-never-return-me",
                "endpoint": "http://169.254.169.254/",
            },
        )
        assert_problem(resp, 422, "ERR_PROVIDER_ENDPOINT_FORBIDDEN")
    await app.state.engine.dispose()
