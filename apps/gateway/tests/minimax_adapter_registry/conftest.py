"""Fixtures for the minimax_adapter_registry test suite.

No-DB fixtures (min_settings / app_no_db) mirror tests/credential_resolution_seam/conftest.py;
DB-backed helpers (make_settings / bootstrap_fresh_db / make_app / client_for / signup_tenant /
auth / assert_problem) mirror tests/provider_config_admin_api/test_provider_config_admin_api.py,
scoped down to what the minimax admin-API round-trip tests need.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from cryptography.fernet import Fernet

TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_REDIS_URL = "redis://localhost:6380/9"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_FERNET_KEY = Fernet.generate_key().decode()
PASSWORD = "correct horse battery staple"  # noqa: S105

PROVIDER_KEYS = "/admin/provider-keys"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"


# ---------------------------------------------------------------------------
# No-DB fixtures (unit-level wiring tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def min_settings() -> Any:
    """Minimal Settings — no operator-level provider API keys (BYOK-only)."""
    from gateway.core.config import Settings

    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        environment="test",
    )


@pytest.fixture
def app_no_db(min_settings: Any) -> Any:
    """create_app() using min_settings — no DB schema bootstrap needed for wiring assertions."""
    from gateway.main import create_app

    return create_app(min_settings)


# ---------------------------------------------------------------------------
# DB-backed helpers (admin-API round-trip tests)
# ---------------------------------------------------------------------------


def make_settings(*, encryption_key: str = TEST_FERNET_KEY) -> Any:
    from gateway.core.config import Settings

    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        provider_key_encryption_key=encryption_key,
    )


async def bootstrap_fresh_db(settings: Any) -> tuple[Any, Any]:
    from gateway.core.db import Base
    from gateway.main import create_app

    bootstrap_app = create_app(settings)
    engine = bootstrap_app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    await bootstrap_app.state.engine.dispose()
    return engine, bootstrap_app.state.sessionmaker


async def make_app(settings: Any, engine: Any, sessionmaker: Any) -> Any:
    from gateway.main import create_app

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
    return body


def store_of(app: Any) -> Any:
    return app.state.tenant_provider_key_store


def tenant_uuid(tenant_id: str) -> uuid.UUID:
    return uuid.UUID(tenant_id)
