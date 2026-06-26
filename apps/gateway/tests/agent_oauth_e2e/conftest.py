"""Suite-local fixtures for agent-oauth-harness-e2e (TASK.md §4).

Wires a real Postgres + Redis (db 9) test environment. Provides helpers to mint
live agent tokens and seed the per-key spend counter for budget tests.

Redis isolation: clears agent_oauth:poll:*, agent_oauth:authz:rl:*, and
usage:spend:key:* keys before each test so state never bleeds between tests.
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

# Default token knobs used across tests
_ACCESS_TTL = 3600
_REFRESH_TTL = 2592000


async def _clear_test_redis_state() -> None:
    """Clear agent_oauth rate-limit keys and spend counters before each test."""
    r = aioredis.from_url(_REDIS_URL)
    try:
        poll_keys = [k async for k in r.scan_iter(match="agent_oauth:poll:*", count=500)]
        rl_keys = [k async for k in r.scan_iter(match="agent_oauth:authz:rl:*", count=500)]
        spend_keys = [k async for k in r.scan_iter(match="usage:spend:key:*", count=500)]
        keys = poll_keys + rl_keys + spend_keys
        if keys:
            await r.delete(*keys)
    except (RedisError, OSError):
        pass
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _clear_state() -> None:  # pyright: ignore[reportUnusedFunction]
    """Clear relevant Redis state BEFORE each test."""
    await _clear_test_redis_state()


@pytest.fixture
def settings() -> Settings:
    """Override global settings fixture with agent_oauth knobs + default budget cap."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=30,
        agent_oauth_access_token_ttl_seconds=_ACCESS_TTL,
        agent_oauth_refresh_token_ttl_seconds=_REFRESH_TTL,
        agent_oauth_token_rpm=60,
        # agent_oauth_default_budget_usd uses the default ($100.00)
        retention_check_interval_seconds=0,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[Any]:
    """App fixture wired to real Postgres + Redis (db 9)."""
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
# Helpers
# ---------------------------------------------------------------------------


async def _signup_tenant_and_user(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    *,
    tenant_name: str = "AgentCo",
    email: str = "agent@agentco.io",
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


async def mint_agent_token(
    app: Any,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    now: datetime | None = None,
    access_ttl_seconds: int = _ACCESS_TTL,
    refresh_ttl_seconds: int = _REFRESH_TTL,
) -> tuple[str, Any]:
    """Seed an approved+minted agent token. Returns (plaintext_access_token, token_row)."""
    now = now or datetime.now(UTC)
    hasher = Sha256SecretHasher()
    device_code = secrets.token_urlsafe(32)
    user_code_str = "MNPQ-RSVW"

    async with app.state.sessionmaker() as session:
        repo = SqlAlchemyAgentOAuthRepository(session)
        # Create pending
        auth = await repo.create_pending(
            device_code_hash=hasher.hash(device_code),
            user_code_hash=hasher.hash(user_code_str),
            scope="proxy",
            interval_seconds=5,
            expires_at=now + timedelta(seconds=600),
        )
        # Approve
        auth = await repo.approve(
            authorization_id=auth.id,
            tenant_id=tenant_id,
            user_id=user_id,
            now=now,
        )

    # Mint using a fresh session (avoids state issues)
    async with app.state.sessionmaker() as session:
        repo2 = SqlAlchemyAgentOAuthRepository(session)
        svc = AgentOAuthService(repo=repo2, hasher=hasher)
        minted = await svc.mint(
            authorization_id=auth.id,
            access_ttl_seconds=access_ttl_seconds,
            refresh_ttl_seconds=refresh_ttl_seconds,
            now=now,
        )
    return minted.access_token, minted.token


@pytest.fixture
async def agent_token(app: Any, tenant_user: dict[str, uuid.UUID]) -> dict[str, Any]:
    """A live (approved, minted, unexpired, unrevoked) agent token for tenant_user."""
    access_token, token_row = await mint_agent_token(
        app,
        tenant_id=tenant_user["tenant_id"],
        user_id=tenant_user["user_id"],
    )
    return {
        "access_token": access_token,
        "token_id": token_row.id,
        "tenant_id": tenant_user["tenant_id"],
    }


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key (sk- prefix)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "KeyCo",
            "email": "key@keyco.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "key@keyco.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model + pricing snapshot."""
    from sqlalchemy import text

    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.0000025, 0.00001, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id
