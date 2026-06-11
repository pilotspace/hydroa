"""Shared fixtures: app wired to a real Postgres (fresh schema per test)."""

import os
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


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
