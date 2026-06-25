"""Suite-local fixtures for the device-authorization-endpoint tests (TASK.md §4).

Mirrors the global conftest pattern: real Postgres + Redis (db 9) per test.
Overrides `settings` to set the agent_oauth knobs needed for these scenarios.
Also provides a factory for building custom-settings apps (rpm=2, empty uri tests).

Redis isolation note: the global _isolate_stores autouse fixture clears usage:* keys
but not agent_oauth:authz:rl:* keys. This suite adds its own autouse fixture that
clears those keys before each test so rate-limit state never bleeds between tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

_REDIS_URL = "redis://localhost:6380/9"


async def _clear_rate_limit_keys() -> None:
    """Delete all agent_oauth rate-limit keys in the test Redis (db 9).

    Called before each test to prevent rate-limit state from bleeding across tests.
    Fail-open: a Redis outage simply skips the clear (test may see stale state but
    the Redis-down tests explicitly inject a broken limiter anyway).
    """
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
        redis_url="redis://localhost:6380/9",
        # agent_oauth knobs for the happy-path suite
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
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
    """App+client with rpm=2 for the per-IP rate-limit test."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        agent_oauth_verification_uri="https://app.test/activate",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=2,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()


@pytest.fixture
async def empty_uri_app_and_client() -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with verification_uri='' for the omit-complete test."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        agent_oauth_verification_uri="",
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()


@pytest.fixture
def broken_redis_limiter() -> Any:
    """A mock AgentOAuthIpRateLimiter whose Redis always raises RedisError."""
    from redis.exceptions import RedisError

    from gateway.agent_oauth.infrastructure.ip_rate_limiter import AgentOAuthIpRateLimiter

    mock_redis = AsyncMock()
    mock_redis.incr.side_effect = RedisError("redis is down")
    return AgentOAuthIpRateLimiter(mock_redis)
