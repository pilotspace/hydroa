"""Shared fixtures: app wired to a real Postgres (fresh schema per test).

Store isolation (test-db-isolation, v12): a global autouse fixture surgically clears the
leaked usage state in the test Redis (db 9) before each test — XTRIM usage:events (so the
flusher's consumer group SURVIVES) + DEL usage:spend:* — so a leaked undelivered stream
entry from one suite cannot be consumed by a later flusher-driving suite and INSERTed as a
usage_record against a freshly-recreated schema (the FK-violation flake). The per-test
drop_all/create_all already isolates Postgres; this closes the Redis channel that was
un-isolated, WITHOUT a blanket FLUSHDB (which would destroy the consumer group).
"""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


async def _clear_usage_leaks_if_reachable(redis_url: str) -> None:
    """Clear leaked usage state in the test Redis (db 9); no-op if Redis is unreachable.

    SURGICAL — must NOT destroy the flusher's consumer group: a blanket FLUSHDB deletes
    the `usage:events` stream together with its `ledger-flusher` consumer group, which
    breaks every flusher-driving suite (NOGROUP on XREADGROUP) and makes the suite 3x
    slower as those tests retry on broken state. Instead:
      - XTRIM usage:events to 0 entries → clears the leaked UNDELIVERED backlog (the
        FK-violation channel) while PRESERVING the consumer group.
      - DEL the usage:spend:* counters → clears the "varying counts" channel.
    Graceful degradation (no-op when Redis absent) keeps the no-infra `make test-fast`
    suites runnable; redis-dependent suites still fail via their own redis_client fixtures.
    """
    r = aioredis.from_url(redis_url)
    try:
        await r.xtrim("usage:events", maxlen=0, approximate=False)
        spend_keys = [k async for k in r.scan_iter(match="usage:spend:*", count=500)]
        if spend_keys:
            await r.delete(*spend_keys)
    except (RedisError, OSError):
        # redis.exceptions.ConnectionError/TimeoutError subclass RedisError, NOT the
        # builtin ConnectionError — catch RedisError so an unreachable Redis is a no-op.
        return
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _isolate_stores(settings: Settings) -> AsyncIterator[None]:
    """Clear leaked usage state in the test Redis (db 9) BEFORE each test.

    Setup-only: each test starts with no inherited usage:events backlog / spend counters,
    so a leaked undelivered stream entry from one suite cannot be consumed by a later
    flusher-driving suite and INSERTed as a usage_record against a freshly-recreated
    schema (the FK-violation flake). The clear is SURGICAL (XTRIM + targeted DEL), never
    a FLUSHDB, so the flusher's consumer group survives. We do NOT cancel pending tasks
    (that kills the pytest-asyncio/anyio runner); function-scoped event loops already kill
    a test's leaked tasks at loop close.
    """
    await _clear_usage_leaks_if_reachable(settings.redis_url)
    yield


@pytest.fixture
def settings() -> Settings:
    # Redis db 9 matches every suite's redis_client fixture (flushed per test).
    # Before team-governance the app default (db 0) silently diverged from the
    # db the suites seed/inspect — v3 suites masked it by rewiring
    # app.state.budget_guard per test; aligning here removes the footgun.
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session
