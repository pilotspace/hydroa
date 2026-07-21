"""Suite-local fixtures for access_requests (signup-refusal-router TASK.md §4 — gateway
half, not covered by the dashboard-scoped test-author pass; written at BUILD per the
task's own "Open item for the orchestrator" flag).

Mirrors tests/member_invite_acceptance/conftest.py's `low_rpm_app_and_client` factory
pattern exactly (same shape, new knob name) for the dedicated rate-limit test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver
from tests import _redis_env

_REDIS_URL = _redis_env.TEST_REDIS_URL


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
    """App+client with access_request_rpm=2, for the rate-limit test."""
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        access_request_rpm=2,  # type: ignore[call-arg]
        retention_check_interval_seconds=0,
        public_signup_enabled=True,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()
