"""Suite-local fixtures for the slo-metrics tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; an API key is created
via POST /admin/keys; usage_records rows are seeded DIRECTLY with controlled
`status`/`created_at`/`tenant_id`, then the endpoint is exercised over HTTP.

NOTE: usage_records.created_at is TIMESTAMPTZ in production (Alembic) but the test schema
is bootstrapped via Base.metadata.create_all where `Mapped[datetime]` maps to a NAIVE
TIMESTAMP column — asyncpg rejects an aware datetime there, so seeds strip tz to naive UTC
(the same gotcha the alerts_events_viewer and reconciliation_aggregate seed helpers use).
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
SLO = "/admin/slo"
PASSWORD = "correct horse battery"

# A fixed base UTC instant; tests seed rows relative to now so the window hits them.
# Using a very recent instant ensures window_hours=24 always covers it.
# Tests build their own `now - minutes` offsets as needed.


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


async def create_key(
    client: httpx.AsyncClient,
    token: str,
    *,
    name: str = "slo-test-key",
) -> str:
    """Create an API key via POST /admin/keys; return its key_id."""
    resp = await client.post(ADMIN_KEYS, headers=auth(token), json={"name": name})
    assert resp.status_code == 201, f"create key failed: {resp.text}"
    return str(resp.json()["key_id"])


async def mint_role_token(
    app: Any,
    session: AsyncSession,
    *,
    tenant_id: str,
    role: Role,
    email: str,
) -> str:
    """Insert a same-tenant user with `role` and mint a JWT for it."""
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


async def seed_usage(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    status: int,
    created_at: datetime.datetime | None = None,
) -> None:
    """Insert one usage_records row with the given status. created_at defaults to 5 min ago."""
    if created_at is None:
        # 5 minutes ago — safely within any 1-hour window
        created_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=5)
    ts = _naive(created_at)
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, usage_source)"
            " VALUES (:id, :tid, :kid, 'openai/gpt-4o', 0, 0, 0, :status,"
            "  '{}', :ts, 'catalog', 'frame')"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_id,
            "status": status,
            "ts": ts,
        },
    )
    await session.commit()
