"""Suite-local fixtures for the alerts-events-viewer tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; alert rows are seeded
DIRECTLY into the `alert_events` table with a controlled `created_at`/`tenant_id`/`event_type`/
`delivered_at`, then the endpoint is exercised over HTTP. Member tokens (same tenant) are minted
via `app.state.token_service.issue(...)` after a direct `users` insert — the proven
same-tenant-role pattern from the reconciliation_endpoint suite.

NOTE: alert_events.created_at / delivered_at are TIMESTAMPTZ in production (Alembic) but the
test schema is bootstrapped via Base.metadata.create_all, where `Mapped[datetime]` maps to a
NAIVE TIMESTAMP column — asyncpg rejects an aware datetime there, so seeds strip tz to naive UTC
(the same gotcha the usage_records seed helper handles).
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
ALERTS = "/admin/alerts"
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


async def seed_alert(
    session: AsyncSession,
    *,
    tenant_id: str | None,
    event_type: str,
    created_at: datetime.datetime = BASE,
    delivered_at: datetime.datetime | None = None,
    payload: dict[str, object] | None = None,
    key_id: str | None = None,
) -> str:
    """Insert one alert_events row (controlled tenant_id/created_at/delivered_at). Returns id."""
    row_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO alert_events"
            " (id, tenant_id, key_id, event_type, payload, created_at, delivered_at, dedupe_key)"
            " VALUES (:id, :tid, :kid, :etype, CAST(:payload AS JSONB), :ts, :delivered, :dedupe)"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "kid": key_id,
            "etype": event_type,
            "payload": json.dumps(payload if payload is not None else {}),
            "ts": _naive(created_at),
            "delivered": _naive(delivered_at) if delivered_at is not None else None,
            # dedupe_key is UNIQUE NOT NULL; tests don't assert on it — make it unique per row.
            "dedupe": f"{event_type}:{row_id}",
        },
    )
    await session.commit()
    return row_id
