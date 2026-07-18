"""Suite-local fixtures for device-activate-preview (device-activate-page TASK.md §4).

Mirrors the device_approval_flow conftest: real Postgres + Redis (db 9) per test, an
app whose agent_oauth knobs are set for the preview scenarios, and a low-preview-rpm
factory for the per-user rate-limit test. Adds seeding helpers for EVERY non-previewable
grant state (approved / denied / consumed) so the uniform-404 byte-identity test can
cover all five reconnaissance cases.

Redis isolation: clears agent_oauth:authz:rl:* before each test so preview/approve
rate-limit buckets never bleed between tests.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.main import create_app
from gateway.tenants.domain.entities import Role
from tests import _redis_env
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

_REDIS_URL = _redis_env.TEST_REDIS_URL

# The dev default the config now ships (device-activate-page §3 CONTRACT).
DEV_VERIFICATION_URI = "http://localhost:3000/activate"


async def _clear_rate_limit_keys() -> None:
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
    await _clear_rate_limit_keys()


@pytest.fixture
def settings() -> Settings:
    """Preview-suite settings — dev default verification_uri, generous rpm."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,
        agent_oauth_verification_uri=DEV_VERIFICATION_URI,
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=12,
        agent_oauth_preview_rpm=30,
        retention_check_interval_seconds=0,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
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
# Identity helpers (mirror device_approval_flow.conftest)
# ---------------------------------------------------------------------------


async def signup_and_login(client: httpx.AsyncClient) -> tuple[str, str, str]:
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
    return token, tenant_id, ""


async def mint_token(
    app: Any, session: AsyncSession, *, tenant_id: str, role: Role = Role.MEMBER
) -> tuple[str, str]:
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
# Authorization seeding — every state the uniform-404 test must cover
# ---------------------------------------------------------------------------


async def seed_pending(
    app: Any, *, user_code: str, ttl_seconds: int = 600, scope: str = "proxy"
) -> str:
    hasher = Sha256SecretHasher()
    expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(f"device-{uuid.uuid4().hex}"),
            user_code_hash=hasher.hash(user_code),
            scope=scope,
            interval_seconds=5,
            expires_at=expires_at,
        )
    return str(auth.id)


async def seed_expired(app: Any, *, user_code: str, scope: str = "proxy") -> str:
    hasher = Sha256SecretHasher()
    expires_at = datetime.now(UTC) - timedelta(seconds=10)
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(f"device-{uuid.uuid4().hex}"),
            user_code_hash=hasher.hash(user_code),
            scope=scope,
            interval_seconds=5,
            expires_at=expires_at,
        )
    return str(auth.id)


async def seed_approved(app: Any, *, user_code: str, tenant_id: str, user_id: str) -> str:
    auth_id = await seed_pending(app, user_code=user_code)
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        await repo.approve(
            authorization_id=uuid.UUID(auth_id),
            tenant_id=uuid.UUID(tenant_id),
            user_id=uuid.UUID(user_id),
            now=datetime.now(UTC),
        )
    return auth_id


async def seed_denied(app: Any, *, user_code: str) -> str:
    auth_id = await seed_pending(app, user_code=user_code)
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        await repo.deny(authorization_id=uuid.UUID(auth_id))
    return auth_id


async def seed_consumed(app: Any, *, user_code: str, tenant_id: str, user_id: str) -> str:
    auth_id = await seed_pending(app, user_code=user_code)
    now = datetime.now(UTC)
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        await repo.approve(
            authorization_id=uuid.UUID(auth_id),
            tenant_id=uuid.UUID(tenant_id),
            user_id=uuid.UUID(user_id),
            now=now,
        )
    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        await repo.mint_token(
            authorization_id=uuid.UUID(auth_id),
            access_token_hash=Sha256SecretHasher().hash(f"access-{uuid.uuid4().hex}"),
            refresh_token_hash=None,
            access_expires_at=now + timedelta(seconds=3600),
            refresh_expires_at=None,
            now=now,
        )
    return auth_id


# ---------------------------------------------------------------------------
# Low-preview-rpm app factory (per-user rate-limit test)
# ---------------------------------------------------------------------------


async def _make_app_and_client(custom_settings: Settings) -> tuple[Any, httpx.AsyncClient]:
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
async def low_preview_rpm_app_and_client() -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with agent_oauth_preview_rpm=2 (approve_rpm stays generous)."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,
        agent_oauth_verification_uri=DEV_VERIFICATION_URI,
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=12,
        agent_oauth_preview_rpm=2,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()
