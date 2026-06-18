"""Suite-local fixtures for reconciliation-aggregate tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test, schema rebuilt per test) + the root
`client`/`db_session` fixtures. A tenant is created via the canonical signup flow (the
`usage_records.tenant_id` FK requires a real tenant); rows are then seeded DIRECTLY into the
ledger with a controlled `created_at`, `cost_usd`, `provider_cost`, `cost_basis`, and
`usage_source` so the pure `reconcile_window` aggregate can be exercised deterministically.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"

# A fixed UTC window the tests seed into.
WINDOW_FROM = datetime.datetime(2026, 6, 1, 0, 0, 0, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 6, 8, 0, 0, 0, tzinfo=datetime.UTC)
INSIDE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner and create one key; return (tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": "correct horse battery"},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": "correct horse battery"})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token = lr.json()["access_token"]
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
    cost_usd: Decimal,
    created_at: datetime.datetime,
    provider_cost: Decimal | None = None,
    cost_basis: str = "catalog",
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
            "cost": str(cost_usd),
            # usage_records.created_at is TIMESTAMP (naive); strip tz like the spend-windows
            # seed helper does (asyncpg rejects an aware datetime into a naive column).
            "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            "basis": cost_basis,
            "pcost": str(provider_cost) if provider_cost is not None else None,
            "src": usage_source,
        },
    )


async def count_rows(session: AsyncSession) -> int:
    """Total usage_records rows — used by RA7 to prove the read never wrote."""
    row = (await session.execute(text("SELECT COUNT(*) FROM usage_records"))).fetchone()
    return int(row[0]) if row else 0


def _money(value: Any) -> Decimal:
    return Decimal(str(value))
