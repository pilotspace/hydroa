"""Suite-local fixtures for tenant-usage-costs-api (OpenAI-wire usage/costs read API).

Infrastructure: real Postgres (:5433 gateway_test) — the shared root conftest.py provides
settings/app/client/db_session. These fixtures mint two tenants via the real signup→login→
create-key flow (so the raw ``sk-`` key authenticates through AuthzUseCase exactly as a real
OpenAI-SDK client would) and seed real ``usage_records`` rows with controlled created_at / model /
cost, mirroring tests/operator_wide_reconciliation/conftest.py::seed_row.

RED-PHASE NOTE: the endpoints under test (/v1/organization/usage/completions and
/v1/organization/costs) do NOT exist yet — every request currently 404s. The suite is red for the
RIGHT reason (missing implementation), which is what the §4 gate requires before Build.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
PASSWORD = "correct horse battery staple"

COMPLETIONS_PATH = "/v1/organization/usage/completions"
COSTS_PATH = "/v1/organization/costs"

# A fixed anchor day so bucket boundaries are deterministic regardless of wall clock.
DAY0 = datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=datetime.UTC)
DAY1 = DAY0 + datetime.timedelta(days=1)
DAY2 = DAY0 + datetime.timedelta(days=2)


def unix(dt: datetime.datetime) -> int:
    return int(dt.timestamp())


def auth_header(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _mk_tenant(client: httpx.AsyncClient, *, name: str, email: str) -> dict[str, str]:
    signup = await client.post(
        SIGNUP, json={"tenant_name": name, "email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    token = (
        await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    ).json()["access_token"]
    created = await client.post(
        ADMIN_KEYS, json={"name": f"{name}-key"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


@pytest.fixture
async def tenant_a(client: httpx.AsyncClient) -> dict[str, str]:
    return await _mk_tenant(client, name="Acme", email="a@acme.io")


@pytest.fixture
async def tenant_b(client: httpx.AsyncClient) -> dict[str, str]:
    return await _mk_tenant(client, name="Beta", email="b@beta.io")


@pytest.fixture
async def second_key_a(client: httpx.AsyncClient, tenant_a: dict[str, str]) -> str:
    """A SECOND key under tenant A (for group_by=api_key_id / api_key_ids filter tests)."""
    created = await client.post(
        ADMIN_KEYS,
        json={"name": "acme-key-2"},
        headers={"Authorization": f"Bearer {tenant_a['jwt']}"},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["key_id"])


@pytest.fixture
async def seed_usage(db_session: AsyncSession) -> Any:
    """Insert one usage_records row with controlled tenant/key/model/tokens/cost/created_at."""

    async def _seed(
        *,
        tenant_id: str,
        key_id: str,
        model_id: str = "openai/gpt-4o",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: Decimal = Decimal("0"),
        created_at: datetime.datetime = DAY0,
        status: int = 200,
    ) -> None:
        await db_session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
                "  cost_usd, status, raw, created_at)"
                " VALUES (:id, :tid, :kid, :mid, :pt, :ct, :cost, :st, '{}', :ts)"
            ),
            {
                "id": str(uuid.uuid4()),
                "tid": tenant_id,
                "kid": key_id,
                "mid": model_id,
                "pt": prompt_tokens,
                "ct": completion_tokens,
                "cost": str(cost_usd),
                "st": status,
                "ts": created_at.astimezone(datetime.UTC).replace(tzinfo=None),
            },
        )
        await db_session.commit()

    return _seed


@pytest.fixture
async def ledger_count(db_session: AsyncSession) -> Any:
    async def _count() -> int:
        row = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).fetchone()
        return int(row[0]) if row else 0

    return _count
