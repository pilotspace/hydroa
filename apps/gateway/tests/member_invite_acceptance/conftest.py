"""Suite-local fixtures for member-invite-acceptance (TASK.md §4).

The global `apps/gateway/tests/conftest.py` `app`/`client`/`db_session`/`settings` fixtures
are inherited UNCHANGED for every ordinary test in this suite — this task's
`invite_preview_rpm=30` / `invite_accept_rpm=10` defaults are comfortably above any single
test's call count, so no override is needed there. Only the two dedicated rate-limit tests
need an app wired with an artificially low rpm; this mirrors
`tests/device_approval_flow/conftest.py`'s own `low_rpm_app_and_client` factory pattern
exactly (same shape, new knob names).

Redis rate-limit key isolation is already handled globally: `tests/conftest.py`'s autouse
`_isolate_stores` fixture clears EVERY Redis key (except the `usage:events` stream) before
each test, so this suite needs no additional local clearing fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
    """App+client with invite_preview_rpm=2 / invite_accept_rpm=2, for the rate-limit tests.

    NOTE (pre-Build): `Settings.model_config` sets `extra="ignore"` (core/config.py:84), so
    passing these two kwargs before Build adds the corresponding fields is silently accepted
    (no ValidationError) — this fixture never fails to construct, pre- or post-Build. What
    makes the rate-limit tests correctly RED pre-Build is that `/invites/{token}...` does not
    exist as a route at all yet (a plain 404 with the wrong body), not this fixture.
    """
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        invite_preview_rpm=2,  # type: ignore[call-arg]
        invite_accept_rpm=2,  # type: ignore[call-arg]
        retention_check_interval_seconds=0,
        # signup-and-routing-authz S1: this suite bootstraps a tenant via signup
        public_signup_enabled=True,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()


@pytest.fixture
async def low_rpm_db_session(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> AsyncIterator[AsyncSession]:
    application, _client = low_rpm_app_and_client
    async with application.state.sessionmaker() as session:
        yield session
