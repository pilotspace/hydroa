"""SSO-OIDC test suite conftest.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9
  - httpx.ASGITransport (no network)
  - FakeOidcExchanger injected via app.state.oidc_exchanger

Pattern mirrors guardrails/conftest.py and response_caching/conftest.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests import _redis_env

TEST_DATABASE_URL = _redis_env.TEST_DATABASE_URL
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"

# OIDC test constants — deterministic fake IdP parameters
FAKE_ISSUER = "https://fake-idp.test"
FAKE_CLIENT_ID = "test-client-id"
FAKE_CLIENT_SECRET = "test-client-secret-not-real"
FAKE_REDIRECT_URI = "http://gateway.test/auth/oidc/callback"
FAKE_DOMAIN = "example.com"
FAKE_OTHER_DOMAIN = "otherdomain.com"

# Encoded domain mapping: example.com → resolved at test time with real tenant_id
# The tenant_id is injected per-test after tenant creation.
FAKE_DOMAIN_MAPPING_TEMPLATE = '[{{"email_domain":"{domain}","tenant_id":"{tenant_id}"}}]'


@pytest.fixture
def oidc_settings_base() -> dict:
    """Base OIDC settings dict (without domain mapping — injected per test)."""
    return {
        "oidc_enabled": True,
        "oidc_issuer": FAKE_ISSUER,
        "oidc_client_id": FAKE_CLIENT_ID,
        "oidc_client_secret": FAKE_CLIENT_SECRET,
        "oidc_redirect_uri": FAKE_REDIRECT_URI,
    }


@pytest.fixture
def settings() -> Settings:
    """Base settings without OIDC enabled (disabled by default)."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
    )


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    """App fixture: clean schema per test. OIDC exchanger NOT wired (disabled by default)."""
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()
