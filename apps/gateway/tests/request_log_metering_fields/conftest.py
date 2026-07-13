"""request-log-metering-fields test suite conftest.

Extends the root conftest to add a UsageLedgerFlusher background task so
fire-and-forget usage recording (usage_records.raw->>'request_id' correlation
assertions) propagates deterministically within test bodies. Pattern copied
verbatim from tests/guardrails/conftest.py / tests/retention_zdr/conftest.py.

Infrastructure:
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (falls back to the un-suffixed default)
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network)
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
        # signup-and-routing-authz S1: this suite bootstraps a tenant via signup.
        public_signup_enabled=True,  # type: ignore[call-arg]
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:  # type: ignore[override]
    """App fixture with a running UsageLedgerFlusher background task.

    Overrides the root conftest app fixture for this suite. The flusher runs
    continuously so a short asyncio.sleep in test bodies (or an explicit
    flush_once() call) is sufficient for fire-and-forget Redis-stream events to
    reach usage_records — needed by this task's request_id correlation scenarios.
    """
    application = create_app(settings)
    # credential-resolution-seam §3: stub the per-tenant credential resolver so this
    # suite's faked-upstream completions resolve a credential without seeding a real key.
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
