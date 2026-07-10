"""Suite-local fixtures for the device-approval-flow tests (TASK.md §4).

Mirrors the device-authorization-endpoint conftest pattern: real Postgres + Redis (db 9)
per test. Overrides `settings` to set the agent_oauth knobs needed for these scenarios.
Also provides a factory for building custom-settings apps (rpm=2 for rate-limit tests).

Redis isolation note: clears agent_oauth:authz:rl:* keys before each test so rate-limit
state never bleeds between tests (mirrors device_authorization_endpoint conftest).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.tenants.domain.entities import Role
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

_REDIS_URL = "redis://localhost:6380/9"


async def _clear_rate_limit_keys() -> None:
    """Delete all agent_oauth rate-limit keys in the test Redis (db 9)."""
    r = aioredis.from_url(_REDIS_URL)
    try:
        keys = [k async for k in r.scan_iter(match="agent_oauth:authz:rl:*", count=500)]
        if keys:
            await r.delete(*keys)
    except (RedisError, OSError):
        pass
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _clear_oauth_rate_limits() -> None:
    """Clear agent_oauth rate-limit keys BEFORE each test in this suite."""
    await _clear_rate_limit_keys()


@pytest.fixture
def settings() -> Settings:
    """Override the global settings fixture with agent_oauth knobs configured."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        # agent_oauth knobs for the happy-path suite
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=12,
        # disable retention sweep to avoid background noise in tests
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


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------


async def signup_and_login(client: httpx.AsyncClient) -> tuple[str, str, str]:
    """Sign up a fresh tenant+owner; return (token, tenant_id, user_id)."""
    email = f"owner-{uuid.uuid4().hex[:8]}@test.example"
    sr = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": f"tenant-{uuid.uuid4().hex[:6]}",
            "email": email,
            "password": "correct horse battery",
        },
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(
        "/admin/auth/login", json={"email": email, "password": "correct horse battery"}
    )
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token: str = lr.json()["access_token"]
    # Decode user_id from the token service
    return token, tenant_id, ""


async def mint_token(
    app: Any, session: AsyncSession, *, tenant_id: str, role: Role = Role.MEMBER
) -> tuple[str, str]:
    """Insert a same-tenant user with `role` and mint a JWT. Returns (token, user_id)."""
    user_id = str(uuid.uuid4())
    email = f"user-{uuid.uuid4().hex[:8]}@test.example"
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "role": str(role)},
    )
    await session.commit()
    token, _ = app.state.token_service.issue(
        user_id=uuid.UUID(user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token), user_id


# ---------------------------------------------------------------------------
# Authorization seeding
# ---------------------------------------------------------------------------


async def seed_pending_authorization(
    app: Any,
    *,
    user_code: str,
    ttl_seconds: int = 600,
    scope: str = "proxy",
) -> str:
    """Seed a pending device_authorization with the given user_code. Returns row id."""
    from datetime import UTC, datetime, timedelta

    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    hasher = Sha256SecretHasher()
    device_code = f"device-{uuid.uuid4().hex}"
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(device_code),
            user_code_hash=hasher.hash(user_code),
            scope=scope,
            interval_seconds=5,
            expires_at=expires_at,
        )
    return str(auth.id)


async def seed_expired_authorization(
    app: Any,
    *,
    user_code: str,
    scope: str = "proxy",
) -> str:
    """Seed a pending device_authorization that is already expired. Returns row id."""
    from datetime import UTC, datetime, timedelta

    from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
    from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher

    hasher = Sha256SecretHasher()
    device_code = f"device-{uuid.uuid4().hex}"
    # expires_at in the past
    expires_at = datetime.now(UTC) - timedelta(seconds=10)

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(device_code),
            user_code_hash=hasher.hash(user_code),
            scope=scope,
            interval_seconds=5,
            expires_at=expires_at,
        )
    return str(auth.id)


async def get_authorization_row(app: Any, auth_id: str) -> dict[str, Any]:
    """Fetch the device_authorizations row by id."""
    async with app.state.sessionmaker() as session:
        res = await session.execute(
            text("SELECT id, status, tenant_id, user_id FROM device_authorizations WHERE id = :id"),
            {"id": auth_id},
        )
        row = res.fetchone()
        assert row is not None, f"No device_authorizations row found for id={auth_id}"
        return dict(row._mapping)


# ---------------------------------------------------------------------------
# Low-RPM app factory
# ---------------------------------------------------------------------------


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
async def low_rpm_app_and_client() -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with agent_oauth_approve_rpm=2 for the per-user rate-limit test."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=2,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()
