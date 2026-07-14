"""retention_zdr test conftest.

Extends the root conftest to:
1. Start a UsageLedgerFlusher background task so fire-and-forget usage recording
   propagates from Redis stream -> usage_records within asyncio.sleep(0.1) waits.
   (The root conftest uses ASGITransport which does not trigger the app lifespan,
   so the flusher task never starts; this fixture fills that gap for this suite —
   mirrors tests/response_caching/conftest.py.) Needed by
   test_zdr_completion_skips_cache_but_bills_usage (M8: usage_records must still be
   billed under ZDR even though caching is skipped).

NOTE: This file does NOT modify any frozen test file; it only overrides the `app`
fixture for this test package, same convention as tests/response_caching/conftest.py.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.usage.application.flusher import UsageLedgerFlusher
from tests.credential_stub import install_stub_resolver
from tests import _redis_env

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    _redis_env.TEST_DATABASE_URL,
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # type: ignore[call-arg]
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:  # type: ignore[override]
    """App fixture with a running UsageLedgerFlusher background task.

    Overrides the root conftest app fixture for this test package only.
    The flusher runs continuously so asyncio.sleep(0.1) in test bodies is
    sufficient for fire-and-forget Redis-stream events to reach usage_records.
    """
    application = create_app(settings)
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

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

    flusher_task.cancel()
    try:
        await flusher_task
    except asyncio.CancelledError:
        pass

    await engine.dispose()
