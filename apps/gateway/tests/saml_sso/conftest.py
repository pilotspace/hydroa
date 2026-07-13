"""SAML-SSO test suite conftest.

Infrastructure (real, no mocks — matches sso_oidc/conftest.py conventions):
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (db-suffix "saml" per the
    build-team shared context: gateway_test_saml)
  - Real Redis at redis://localhost:6380/9 — the production RedisSamlRequestStore
    / RedisSamlReplayCache adapters are used AS-IS (app.state seams left at
    their production-default None so main.py's real DI wiring constructs
    them), never faked — a security-critical replay store's atomicity is
    exactly what these tests must exercise for real.
  - httpx.ASGITransport (no network)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests import _redis_env

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    _redis_env.TEST_DATABASE_URL,
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"
TEST_REDIS_URL = _redis_env.TEST_REDIS_URL

SP_ENTITY_ID_BASE = "https://gw.test/saml/sp"
ACS_URL = "https://gw.test/auth/saml/acs"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: bootstrap via signup
        saml_sp_entity_id_base=SP_ENTITY_ID_BASE,
        saml_acs_url=ACS_URL,
    )


async def _clear_saml_redis_keys() -> None:
    """Surgical pre-test clear of any leaked saml:* keys — mirrors the root
    conftest's per-suite isolation posture without touching other namespaces."""
    r = aioredis.from_url(TEST_REDIS_URL)
    try:
        keys = [k async for k in r.scan_iter(match="saml:*", count=1000)]
        if keys:
            await r.delete(*keys)
    except Exception:  # noqa: BLE001 — best-effort cleanup, never fail a test on this
        pass
    finally:
        await r.aclose()


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    """App fixture: clean schema per test; real Redis for saml:* stores."""
    await _clear_saml_redis_keys()
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()
    await _clear_saml_redis_keys()


@pytest.fixture
async def client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://gw.test") as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session
