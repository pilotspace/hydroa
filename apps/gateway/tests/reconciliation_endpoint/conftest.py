"""Suite-local fixtures for the reconciliation-endpoint tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; rows are seeded DIRECTLY
into the append-only `usage_records` ledger with a controlled `created_at`/`cost_usd`/
`provider_cost`/`cost_basis`/`usage_source`, then the endpoint is exercised over HTTP. Admin
and member tokens (same tenant) are minted via `app.state.token_service.issue(...)` after a
direct `users` insert — the proven same-tenant-role pattern from the team_governance suite.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
RECON = "/admin/reconciliation"
PASSWORD = "correct horse battery"

# A fixed UTC day the tests seed into; the endpoint is called with start=end=INSIDE_DATE
# (end-inclusive → +1 day), so the half-open window is [INSIDE_DATE 00:00, next-day 00:00) —
# deterministic, independent of "now".
INSIDE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)
INSIDE_DATE = "2026-06-03"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def recon_params(
    *, window: str = "month", start: str | None = INSIDE_DATE, end: str | None = INSIDE_DATE
) -> dict[str, str]:
    """Build the default query params bracketing the INSIDE day."""
    params: dict[str, str] = {"window": window}
    if start is not None:
        params["start"] = start
    if end is not None:
        params["end"] = end
    return params


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str, str]:
    """Sign up a tenant+owner and create one key; return (owner_jwt, tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token = lr.json()["access_token"]
    kr = await client.post(ADMIN_KEYS, json={"name": f"{tenant_name}-key"}, headers=auth(token))
    assert kr.status_code == 201, f"key creation failed: {kr.text}"
    return token, tenant_id, kr.json()["key_id"]


async def mint_role_token(
    app: Any,
    session: AsyncSession,
    *,
    tenant_id: str,
    role: Role,
    email: str,
) -> str:
    """Insert a same-tenant user with `role` and mint a JWT for it (team_governance pattern)."""
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


async def seed_row(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    cost_usd: Decimal,
    created_at: datetime.datetime = INSIDE,
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
            # usage_records.created_at is naive TIMESTAMP in the test schema; strip tz like the
            # spend-windows seed helper (asyncpg rejects an aware datetime into a naive column).
            "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            "basis": cost_basis,
            "pcost": str(provider_cost) if provider_cost is not None else None,
            "src": usage_source,
        },
    )
