"""Response-caching test conftest.

Extends the root conftest to:
1. Start a UsageLedgerFlusher background task so fire-and-forget usage recording
   propagates from Redis stream → usage_records within asyncio.sleep(0.1) waits.
   (The root conftest uses ASGITransport which does not trigger the app lifespan,
   so the flusher task never starts; this fixture fills that gap for this suite.)
NOTE: This file does NOT modify any frozen test file.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.credential_stub import install_stub_resolver
from gateway.usage.application.flusher import UsageLedgerFlusher

# payload-capture-store build-time fix: this suite-local override previously
# hardcoded the shared, un-suffixed `gateway_test` database, bypassing the
# GATEWAY_TEST_DATABASE_URL convention every other suite (via the root
# tests/conftest.py) already respects. On a Postgres instance shared across
# concurrent build worktrees, that hardcode collides with whichever sibling
# task's tables happen to exist in the literal `gateway_test` DB at the time.
# Falling back to the SAME literal default when the env var is unset keeps
# this 100% behavior-preserving for any caller that doesn't set it.
TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        # signup-and-routing-authz S1: this suite bootstraps a tenant via signup
        public_signup_enabled=True,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:  # type: ignore[override]
    """App fixture with a running UsageLedgerFlusher background task.

    Overrides the root conftest app fixture for this test module only.
    The flusher runs continuously so asyncio.sleep(0.1) in test bodies is
    sufficient for fire-and-forget Redis-stream events to reach usage_records.
    """
    application = create_app(settings)
    # credential-resolution-seam §3: stub the per-tenant credential resolver so this
    # suite's faked-upstream completions resolve a credential without seeding a real key.
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Start flusher background task with a very short interval so recording
    # fire-and-forget tasks propagate to usage_records within asyncio.sleep(0.1).
    redis_client = application.state.redis_client
    flusher = UsageLedgerFlusher(
        redis=redis_client,
        session_factory=application.state.sessionmaker,
    )
    application.state.flusher = flusher

    # Use 0.01s interval (vs default 1s) so asyncio.sleep(0.1) in tests is ample.
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

    # Cleanup: cancel flusher, do one final drain, dispose engine
    flusher_task.cancel()
    try:
        await flusher_task
    except asyncio.CancelledError:
        pass

    await engine.dispose()
