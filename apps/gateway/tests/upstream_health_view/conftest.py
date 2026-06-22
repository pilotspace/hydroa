"""Suite-local fixtures for the upstream-health-view tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; health rows are seeded
DIRECTLY into the `alert_events` table as SYSTEM events (tenant_id NULL) with a controlled
`created_at`/`event_type`, then `GET /admin/health/upstreams` is exercised over HTTP. Member /
admin tokens (same tenant) are minted via `app.state.token_service.issue(...)` after a direct
`users` insert — the proven same-tenant-role pattern reused from the alerts_events_viewer suite.

NOTE: alert_events.created_at is TIMESTAMPTZ in production (Alembic) but the test schema is
bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a NAIVE TIMESTAMP
column — asyncpg rejects an aware datetime there, so seeds strip tz to naive UTC.
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
HEALTH = "/admin/health/upstreams"
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
    """Insert a same-tenant user with `role` and mint a JWT for it (alerts-viewer pattern)."""
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


async def seed_health_event(
    session: AsyncSession,
    *,
    event_type: str,
    created_at: datetime.datetime = BASE,
    payload: dict[str, object] | None = None,
    tenant_id: str | None = None,
) -> str:
    """Insert one alert_events health row. Defaults to a SYSTEM row (tenant_id NULL);
    pass tenant_id to seed a tenant-OWNED row (used to prove the NULL-tenant filter
    strictly excludes tenant-owned rows from the derivation). Returns id."""
    row_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO alert_events"
            " (id, tenant_id, key_id, event_type, payload, created_at, delivered_at, dedupe_key)"
            " VALUES (:id, :tid, NULL, :etype, CAST(:payload AS JSONB), :ts, NULL, :dedupe)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "etype": event_type,
            "payload": json.dumps(
                payload
                if payload is not None
                else {"consecutive_failures": 3, "url": "https://openrouter.ai/api/v1"}
            ),
            "ts": _naive(created_at),
            # dedupe_key is UNIQUE NOT NULL; tests don't assert on it — make it unique per row.
            "dedupe": f"{event_type}:{row_id}",
        },
    )
    await session.commit()
    return row_id


async def alert_row_count(session: AsyncSession) -> int:
    row = (await session.execute(text("SELECT COUNT(*) FROM alert_events"))).fetchone()
    return int(row[0]) if row else 0
