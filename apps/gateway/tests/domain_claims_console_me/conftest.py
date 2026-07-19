"""Suite-local fixtures for domain-claims-console's CR gateway half (TASK.md §4 CR
2026-07-19 — MeResponse.tenant_name).

Duplicated (not cross-imported) from tests/domain_auto_assign_login/conftest.py per
this repo's established convention (sibling suites duplicate the tiny fixture rather
than cross-import between test packages). Trimmed to only what a signup+login+/me
round trip needs — no OIDC/SAML/DNS machinery. Real Postgres at
GATEWAY_TEST_DATABASE_URL; httpx.ASGITransport (no network).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests import _redis_env

TEST_DATABASE_URL = os.environ.get("GATEWAY_TEST_DATABASE_URL", _redis_env.TEST_DATABASE_URL)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"  # noqa: S105
TEST_REDIS_URL = _redis_env.TEST_REDIS_URL

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ME = "/admin/auth/me"

DEFAULT_PASSWORD = "correct horse battery staple"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=TEST_REDIS_URL,
        public_signup_enabled=True,
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
    async with httpx.AsyncClient(
        transport=transport, base_url="http://gw.test", follow_redirects=False
    ) as c:
        yield c


@pytest.fixture
async def db_session(app: object) -> AsyncIterator[AsyncSession]:
    async with app.state.sessionmaker() as session:  # type: ignore[attr-defined]
        yield session


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = DEFAULT_PASSWORD,
) -> tuple[uuid.UUID, str]:
    """Bootstrap a REAL tenant+owner and log in; returns (tenant_id, bearer_token)."""
    signup_resp = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password}
    )
    assert signup_resp.status_code == 201, signup_resp.text
    tenant_id = uuid.UUID(signup_resp.json()["tenant_id"])

    login_resp = await client.post(LOGIN, json={"email": email, "password": password})
    assert login_resp.status_code == 200, login_resp.text
    return tenant_id, str(login_resp.json()["access_token"])
