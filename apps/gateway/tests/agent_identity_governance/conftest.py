"""Suite-local fixtures for agent-identity-governance (TASK.md §4).

Wires a real Postgres + Redis (isolated per-worker db) test environment. Provides
helpers to mint live agent tokens (mirrors tests/agent_oauth_e2e/conftest.py) and to
mint arbitrary-role JWTs directly via token_service.issue() — no `users` row needed
for a role-check-only caller (audit_events.actor_user_id has no FK constraint).
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
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.agent_oauth.application.use_cases import AgentOAuthService
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

_ACCESS_TTL = 3600
_REFRESH_TTL = 2592000


async def _clear_test_redis_state() -> None:
    r = aioredis.from_url(_REDIS_URL)
    try:
        keys = [k async for k in r.scan_iter(match="usage:spend:key:*", count=500)]
        keys += [k async for k in r.scan_iter(match="usage:spend:agent_principal:*", count=500)]
        if keys:
            await r.delete(*keys)
    except (RedisError, OSError):
        pass
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _clear_state() -> None:  # pyright: ignore[reportUnusedFunction]
    await _clear_test_redis_state()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        public_signup_enabled=True,
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=60,
        agent_oauth_approve_rpm=60,
        agent_oauth_access_token_ttl_seconds=_ACCESS_TTL,
        agent_oauth_refresh_token_ttl_seconds=_REFRESH_TTL,
        agent_oauth_token_rpm=60,
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


@pytest.fixture
def hasher() -> Sha256SecretHasher:
    return Sha256SecretHasher()


@pytest.fixture
async def redis_client(app: Any) -> AsyncIterator[Any]:
    yield app.state.redis_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _signup_tenant_and_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    tenant_name: str,
    email: str,
) -> dict[str, Any]:
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

    login = await client.post(
        "/admin/auth/login", json={"email": email, "password": "correct horse battery"}
    )
    assert login.status_code == 200, f"login failed: {login.text}"
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "owner_jwt": login.json()["access_token"],
    }


@pytest.fixture
async def owner(client: httpx.AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    """Signup -> login. Returns tenant_id/user_id/owner_jwt for a fresh tenant."""
    return await _signup_tenant_and_user(
        client, db_session, tenant_name="AgentCo", email="owner@agentco.io"
    )


@pytest.fixture
async def other_tenant(client: httpx.AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    """A SEPARATE tenant+owner, for cross-tenant rejection tests."""
    return await _signup_tenant_and_user(
        client, db_session, tenant_name="OtherCo", email="owner@otherco.io"
    )


def mint_role_jwt(app: Any, *, tenant_id: uuid.UUID, role: Role, email: str) -> str:
    """Mint a JWT for an arbitrary role directly via token_service.issue().

    No `users` row is needed — this caller is only ever used for a role-check (no
    use case here queries the `users` table by this user_id), and audit_events.
    actor_user_id carries no FK constraint (confirmed: audit_events_orm.py has
    ``actor_user_id UUID NULL`` with no REFERENCES clause).
    """
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


async def mint_agent_token(
    app: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Seed an approved+minted agent token (bypasses the public poll endpoint —
    exercises the SAME repo/service code the real /oauth/token router calls)."""
    now = now or datetime.now(UTC)
    hasher = Sha256SecretHasher()
    device_code = secrets.token_urlsafe(32)
    user_code_str = "MNPQ-RSVW" + secrets.token_hex(4)

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(device_code),
            user_code_hash=hasher.hash(user_code_str),
            scope="proxy",
            interval_seconds=5,
            expires_at=now + timedelta(seconds=600),
        )
        auth = await repo.approve(
            authorization_id=auth.id, tenant_id=tenant_id, user_id=user_id, now=now
        )

    async with app.state.sessionmaker() as session:
        repo2 = SqlAlchemyAgentOAuthRepository(session)
        svc = AgentOAuthService(repo=repo2, hasher=hasher)
        minted = await svc.mint(
            authorization_id=auth.id,
            access_ttl_seconds=_ACCESS_TTL,
            refresh_ttl_seconds=None,
            now=now,
        )
    return {
        "access_token": minted.access_token,
        "token_id": minted.token.id,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }
