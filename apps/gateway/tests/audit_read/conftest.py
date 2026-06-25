"""Suite-local fixtures for the audit-read tests (TASK.md §3).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; audit_events rows are seeded
DIRECTLY into the `audit_events` table with controlled fields, then the endpoint is exercised
over HTTP. Role tokens (same tenant) are minted via `app.state.token_service.issue(...)` after a
direct `users` insert — the proven same-tenant-role pattern from the reconciliation_endpoint suite.

NOTE: audit_events.created_at is TIMESTAMPTZ in production (Alembic) but the test schema is
bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a NAIVE TIMESTAMP
column — asyncpg rejects an aware datetime there, so seeds strip tz to naive UTC (same gotcha as
alerts_events_viewer conftest).
"""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
AUDIT = "/admin/audit"
PASSWORD = "correct horse battery"

# A fixed base UTC instant the tests seed around (offsets keep ordering deterministic).
BASE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _naive(dt: datetime.datetime) -> datetime.datetime:
    """asyncpg rejects an aware datetime into the create_all naive TIMESTAMP column."""
    return dt.astimezone(datetime.UTC).replace(tzinfo=None)


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner; return (owner_jwt, tenant_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def mint_role_token(
    app: Any,
    session: AsyncSession,
    *,
    tenant_id: str,
    role: Role,
    email: str,
) -> str:
    """Insert a same-tenant user with `role` and mint a JWT for it (reconciliation pattern)."""
    user_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "role": str(role)},
    )
    await session.commit()
    token, _ = app.state.token_service.issue(
        user_id=uuid.UUID(user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def seed_audit_event(
    session: AsyncSession,
    *,
    tenant_id: str,
    actor_user_id: str | None = None,
    actor_email: str | None = "actor@example.com",
    action: str = "api_key.create",
    target_type: str | None = "api_key",
    target_id: str | None = None,
    result: str = "success",
    metadata: dict[str, object] | None = None,
    created_at: datetime.datetime = BASE,
) -> str:
    """Insert one audit_events row (controlled fields). Returns id."""
    row_id = str(uuid.uuid4())
    if target_id is None:
        target_id = str(uuid.uuid4())
    if actor_user_id is None:
        actor_user_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO audit_events"
            " (id, tenant_id, actor_user_id, actor_email, action, target_type,"
            "  target_id, result, metadata, created_at)"
            " VALUES (:id, :tid, :auid, :aemail, :action, :ttype,"
            "         :tid2, :result, CAST(:metadata AS JSONB), :ts)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "auid": actor_user_id,
            "aemail": actor_email,
            "action": action,
            "ttype": target_type,
            "tid2": target_id,
            "result": result,
            "metadata": json.dumps(metadata if metadata is not None else {}),
            "ts": _naive(created_at),
        },
    )
    await session.commit()
    return row_id
