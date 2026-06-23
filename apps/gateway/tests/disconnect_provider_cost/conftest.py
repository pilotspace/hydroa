"""Suite-local fixtures for disconnect-provider-cost (v33).

Exercises the recorder -> flusher -> ledger round-trip (mirrors
provider_generation_id_capture) plus direct SQL seeds for the audit. A DB-backed
api_key (distinct tenant/email) satisfies the FK chain; a dedicated Redis index keeps
the recorder/flusher round-trip isolated from other suites.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

INSIDE = datetime.datetime(2026, 6, 3, 12, 0, 0, tzinfo=datetime.UTC)
WINDOW_FROM = datetime.datetime(2026, 6, 3, 0, 0, 0, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 6, 4, 0, 0, 0, tzinfo=datetime.UTC)


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    """Signup -> login -> create key (DB-backed; distinct tenant/email)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "DisconnectCostTest",
            "email": "disconnect-cost-test@example.io",
            "password": "disconnect cost battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "disconnect-cost-test@example.io",
                "password": "disconnect cost battery",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "disconnect-cost-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }


async def seed_priced_model(db_session: AsyncSession, model_id: str) -> None:
    """Insert a model + a per-token pricing snapshot so the recorder can cost a row."""
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "Disconnect cost model"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.00001, 0.00003, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()


async def seed_disconnect_row(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    key_id: str,
    provider_generation_id: str | None,
    usage_source: str,
    provider_cost: str | None = None,
    cost_usd: str = "0",
    cost_basis: str = "catalog",
    created_at: datetime.datetime = INSIDE,
) -> None:
    """Insert one usage_records row directly (for the audit case)."""
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, created_at, cost_basis, provider_cost,"
            "  usage_source, provider_generation_id)"
            " VALUES (:id, :tid, :kid, :mid, 0, 0,"
            "  :cost, 200, '{}', :ts, :basis, :pcost, :src, :gid)"
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
            "gid": provider_generation_id,
        },
    )
    await db_session.commit()
