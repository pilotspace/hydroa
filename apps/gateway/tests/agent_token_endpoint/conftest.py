"""Suite-local fixtures for the agent-token-endpoint tests (TASK.md §4).

Mirrors the device-authorization-endpoint conftest pattern: real Postgres + Redis (db 9)
per test. Overrides ``settings`` to add the three new token-lifetime knobs.

Redis isolation: clears agent_oauth:poll:* (slow_down) and agent_oauth:authz:rl:* (IP
rate-limit) keys before each test so state never bleeds between tests.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.application.use_cases import AgentOAuthService
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

_REDIS_URL = "redis://localhost:6380/9"

# Default token knobs used across most tests
_ACCESS_TTL = 3600
_REFRESH_TTL = 2592000
_TOKEN_RPM = 60


async def _clear_poll_and_rl_keys() -> None:
    """Delete slow_down (agent_oauth:poll:*) and IP-rate-limit keys from the test Redis.

    Called before each test to prevent state from bleeding. Fail-open.
    """
    r = aioredis.from_url(_REDIS_URL)
    try:
        poll_keys = [k async for k in r.scan_iter(match="agent_oauth:poll:*", count=500)]
        rl_keys = [k async for k in r.scan_iter(match="agent_oauth:authz:rl:*", count=500)]
        keys = poll_keys + rl_keys
        if keys:
            await r.delete(*keys)
    except (RedisError, OSError):
        pass
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _clear_oauth_keys() -> None:
    """Clear agent_oauth poll and rate-limit keys BEFORE each test."""
    await _clear_poll_and_rl_keys()


@pytest.fixture
def settings() -> Settings:
    """Override the global settings fixture with agent_oauth token knobs configured."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=30,
        agent_oauth_access_token_ttl_seconds=_ACCESS_TTL,
        agent_oauth_refresh_token_ttl_seconds=_REFRESH_TTL,
        agent_oauth_token_rpm=_TOKEN_RPM,
        # Disable retention sweep to avoid background noise
        retention_check_interval_seconds=0,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    """Standard app fixture wired to real Postgres + Redis (db 9)."""
    application = create_app(settings)
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_session(app: Any) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:
        yield session


@pytest.fixture
def hasher() -> Sha256SecretHasher:
    return Sha256SecretHasher()


# ---------------------------------------------------------------------------
# Helpers for seeding authorizations directly via the repository
# ---------------------------------------------------------------------------


async def _signup_tenant_and_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    tenant_name: str = "Acme",
    email: str = "ada@acme.io",
) -> dict[str, uuid.UUID]:
    """Seed a tenant+user via the admin signup API."""
    from sqlalchemy import text

    r = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 201, f"signup failed: {r.text}"
    tenant_id = uuid.UUID(r.json()["tenant_id"])
    res = await db_session.execute(
        text("SELECT id FROM users WHERE tenant_id = :t"), {"t": tenant_id}
    )
    user_id = res.scalar_one()
    return {"tenant_id": tenant_id, "user_id": user_id}


@pytest.fixture
async def tenant_user(client: httpx.AsyncClient, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    return await _signup_tenant_and_user(client, db_session)


async def _seed_authorization(
    app: Any,
    *,
    status: str = "pending",
    scope: str = "proxy",
    interval_seconds: int = 5,
    ttl_seconds: int = 600,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    now: datetime | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, Any]:
    """Create a pending DeviceAuthorization and optionally transition it.

    Returns (plaintext_device_code, DeviceAuthorization).
    """
    now = now or datetime.now(UTC)
    device_code = secrets.token_urlsafe(32)
    user_code = "ABCD-EFGH"  # stable for tests; hash is what matters
    hasher = Sha256SecretHasher()

    effective_expires_at = (
        expires_at if expires_at is not None else (now + timedelta(seconds=ttl_seconds))
    )

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(device_code),
            user_code_hash=hasher.hash(user_code),
            scope=scope,
            interval_seconds=interval_seconds,
            expires_at=effective_expires_at,
        )

        if status == "approved":
            assert tenant_id is not None and user_id is not None
            auth = await repo.approve(
                authorization_id=auth.id,
                tenant_id=tenant_id,
                user_id=user_id,
                now=now,
            )
        elif status == "denied":
            auth = await repo.deny(authorization_id=auth.id)

    return device_code, auth


async def _make_app_and_client(custom_settings: Settings) -> tuple[Any, httpx.AsyncClient]:
    """Build an app+client with custom settings; caller must close the client."""
    application = create_app(custom_settings)
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return application, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),
        base_url="http://test",
    )


@pytest.fixture
async def no_refresh_app_and_client() -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with refresh_ttl=0 (refresh token disabled)."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=30,
        agent_oauth_access_token_ttl_seconds=_ACCESS_TTL,
        agent_oauth_refresh_token_ttl_seconds=0,  # DISABLED
        agent_oauth_token_rpm=_TOKEN_RPM,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()


@pytest.fixture
async def low_rpm_app_and_client() -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with token_rpm=2 for the per-IP rate-limit test."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=30,
        agent_oauth_access_token_ttl_seconds=_ACCESS_TTL,
        agent_oauth_refresh_token_ttl_seconds=_REFRESH_TTL,
        agent_oauth_token_rpm=2,  # LOW — for rate-limit test
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()
