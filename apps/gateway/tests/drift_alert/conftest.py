"""Suite-local fixtures for the drift-alert tests (TASK.md §4).

Real Postgres + the root `client`/`db_session`/`app` fixtures. Tenants are created via the
canonical signup flow (the `usage_records.tenant_id`/`key_id` FKs need real rows); unbilled-upstream
rows (`provider_cost > 0 ∧ cost_usd = 0`, `cost_basis='provider'`) are seeded directly with
`created_at = now(UTC)` so they fall in the checker's current-UTC-day window. The
`ReconciliationDriftChecker` is then constructed with `session_factory = app.state.sessionmaker`
and `check_once()` is exercised directly (the health-alerting checker-test pattern).
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
PASSWORD = "correct horse battery"
DRIFT_EVENT = "reconciliation_drift"


def today_dedupe_key() -> str:
    """The dedupe_key the checker uses for today's UTC window."""
    return f"reconciliation_drift:{datetime.datetime.now(datetime.UTC):%Y%m%d}"


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner and create one key; return (tenant_id, key_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    token = lr.json()["access_token"]
    kr = await client.post(
        ADMIN_KEYS,
        json={"name": f"{tenant_name}-key"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert kr.status_code == 201, f"key creation failed: {kr.text}"
    return tenant_id, kr.json()["key_id"]


async def seed_unbilled(
    session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    provider_cost: Decimal,
    usage_source: str = "stream_fallback",
) -> None:
    """Insert one unbilled-upstream usage_records row (provider_cost>0, cost_usd=0) dated now()."""
    now_naive = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    await session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, provider_cost, usage_source)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0,"
            "  0, 200, '{}', :ts, 'provider', :pcost, :src)"
        ),
        {
            "id": str(uuid.uuid4()),
            "tid": tenant_id,
            "kid": key_id,
            "mid": "openai/gpt-4o",
            "ts": now_naive,
            "pcost": str(provider_cost),
            "src": usage_source,
        },
    )


async def get_drift_rows(session_factory: Any) -> list[dict[str, Any]]:
    """All alert_events rows of event_type='reconciliation_drift', newest first."""
    async with session_factory() as session:
        result = await session.execute(
            text("SELECT * FROM alert_events WHERE event_type = :et ORDER BY created_at DESC"),
            {"et": DRIFT_EVENT},
        )
        return [dict(row) for row in result.mappings().all()]


async def count_usage_rows(session_factory: Any) -> int:
    """Total usage_records rows — proves the checker never wrote to the ledger."""
    async with session_factory() as session:
        return int((await session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar_one())


class BoomSessionFactory:
    """A session_factory whose use raises — to prove check_once swallows cycle errors."""

    def __call__(self) -> Any:
        raise RuntimeError("boom: session factory unavailable")
