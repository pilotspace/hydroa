"""Suite-local fixtures for reconcile-cost-basis-filter (v33).

Exercises the application-layer reconciliation primitives directly against the real
Postgres ledger (localhost:5433/gateway_test). Rows are seeded into usage_records with a
controlled created_at; a tenant + key are created over HTTP so the FK chain is satisfied.
Mirrors tests/operator_wide_reconciliation/conftest.py.
"""

from __future__ import annotations

import datetime
import uuid

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
PASSWORD = "correct horse battery"

# Fixed UTC instant the tests seed into, with a half-open window that contains it.
INSIDE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)
WINDOW_FROM = datetime.datetime(2026, 6, 3, 0, 0, 0, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 6, 4, 0, 0, 0, tzinfo=datetime.UTC)


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner and create one key; return (tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    token = (
        await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    ).json()["access_token"]
    kr = await client.post(
        ADMIN_KEYS, json={"name": f"{tenant_name}-key"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert kr.status_code == 201, f"key creation failed: {kr.text}"
    return tenant_id, kr.json()["key_id"]


async def seed_row(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    cost_usd: str,
    provider_cost: str | None,
    cost_basis: str,
    created_at: datetime.datetime = INSIDE,
    usage_source: str = "frame",
) -> None:
    """Insert one usage_records row with full reconciliation columns (controlled created_at)."""
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, provider_cost, usage_source)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0,"
            "  :cost, 200, '{}', :ts, :basis, :pcost, :src)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_id,
            "mid": "openai/gpt-4o",
            "cost": cost_usd,
            "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            "basis": cost_basis,
            "pcost": provider_cost,
            "src": usage_source,
        },
    )
    await session.commit()
