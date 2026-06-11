"""Semantic-cache test conftest.

Extends the root conftest to:
1. Start a UsageLedgerFlusher background task so fire-and-forget usage recording
   propagates from Redis stream → usage_records within asyncio.sleep(0.1) waits.
   (The root conftest uses ASGITransport which does not trigger the app lifespan,
   so the flusher task never starts; this fixture fills that gap for this suite.)
NOTE: This file mirrors tests/response_caching/conftest.py — same pattern.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.usage.application.flusher import UsageLedgerFlusher

TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:  # type: ignore[override]
    """App fixture with a running UsageLedgerFlusher background task.

    Overrides the root conftest app fixture for this suite only.
    The flusher runs continuously so asyncio.sleep(0.1) in test bodies is
    sufficient for fire-and-forget Redis-stream events to reach usage_records.
    """
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Start flusher with a very short interval so asyncio.sleep(0.1) in tests is ample.
    redis_client = application.state.redis_client
    flusher = UsageLedgerFlusher(
        redis=redis_client,
        session_factory=application.state.sessionmaker,
    )
    application.state.flusher = flusher

    async def _fast_flusher() -> None:
        while True:
            try:
                await flusher.flush_once()
            except Exception:
                pass
            await asyncio.sleep(0.01)

    flusher_task = asyncio.create_task(_fast_flusher())
    application.state.flusher_task = flusher_task

    yield application

    # Cleanup: cancel flusher, dispose engine
    flusher_task.cancel()
    try:
        await flusher_task
    except asyncio.CancelledError:
        pass

    await engine.dispose()

