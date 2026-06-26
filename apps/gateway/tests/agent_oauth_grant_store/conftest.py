"""Fixtures for the agent-oauth-grant-store red suite.

These import `gateway.agent_oauth.*`, which does not exist until Build — so the suite
fails at COLLECTION with ImportError (the right red reason) before any implementation.

The repository is exercised DB-backed over the shared `db_session` (fresh schema per test
via Base.metadata.create_all); the service owns secret generation + hashing.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from gateway.agent_oauth.application.use_cases import AgentOAuthService
from gateway.agent_oauth.infrastructure.repository import SqlAlchemyAgentOAuthRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher


@pytest.fixture
def hasher() -> Sha256SecretHasher:
    return Sha256SecretHasher()


@pytest.fixture
def repo(db_session: AsyncSession) -> SqlAlchemyAgentOAuthRepository:
    return SqlAlchemyAgentOAuthRepository(db_session)


@pytest.fixture
def service(repo: SqlAlchemyAgentOAuthRepository, hasher: Sha256SecretHasher) -> AgentOAuthService:
    return AgentOAuthService(repo=repo, hasher=hasher)


async def _signup_tenant(
    client: httpx.AsyncClient, db_session: AsyncSession, *, tenant_name: str, email: str
) -> dict[str, uuid.UUID]:
    r = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": "correct horse battery"},
    )
    assert r.status_code == 201, f"signup failed: {r.text}"
    tenant_id = uuid.UUID(r.json()["tenant_id"])
    res = await db_session.execute(
        text("SELECT id FROM users WHERE tenant_id = :t"), {"t": tenant_id}
    )
    user_id = res.scalar_one()
    return {"tenant_id": tenant_id, "user_id": user_id}


@pytest.fixture
async def tenant_user(client: httpx.AsyncClient, db_session: AsyncSession) -> dict[str, uuid.UUID]:
    return await _signup_tenant(client, db_session, tenant_name="Acme", email="ada@acme.io")


@pytest.fixture
def make_tenant_user(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> Callable[[str, str], Awaitable[dict[str, uuid.UUID]]]:
    async def _make(tenant_name: str, email: str) -> dict[str, uuid.UUID]:
        return await _signup_tenant(client, db_session, tenant_name=tenant_name, email=email)

    return _make
