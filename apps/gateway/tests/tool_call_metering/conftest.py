"""Fixtures for the tool-call-metering red/green suite (TASK.md §4, FROZEN @ v1).

Reuses the project-wide `app`/`client`/`db_session` fixtures (tests/conftest.py), the
signup->login->create-key pattern (mirrors tests/mcp_connector/conftest.py's `owner`),
and the real-Redis-on-db-9 pattern (mirrors tests/usage/test_usage_metering.py).

`create_all`-based test schema does NOT run this task's data-only seed migration
(b64d469b341e) — `seed_mcp_tool_call_pricing` below inserts the SAME two rows directly,
mirroring tests/usage/test_usage_metering.py's `active_model_with_pricing` fixture
pattern for every non-migration-focused test in this suite. The migration itself is
exercised separately in tests/migrations/test_tool_call_metering_seed.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env

MCP_TOOL_CALL_MODEL_ID = "mcp_tool_call"
DEFAULT_UNIT_USD_PER_UNIT = "0.0025"  # $2.50 / 1k tool calls (Tin, freeze 2026-07-14)


async def seed_mcp_tool_call_pricing(
    db_session: AsyncSession,
    *,
    unit_usd_per_unit: str | None = DEFAULT_UNIT_USD_PER_UNIT,
    active: bool = False,
    modality: str = "tool_call",
    insert_pricing_snapshot: bool = True,
) -> None:
    """Insert the models row + (optionally) its pricing_snapshots row directly —
    the functional-test-schema equivalent of migration b64d469b341e's upgrade().

    unit_usd_per_unit=None (default insert_pricing_snapshot=True) -> insert the
    pricing_snapshots row WITH unit_usd_per_unit bound to SQL NULL — the M11/R1
    "carries a NULL unit_usd_per_unit" condition, which is the one that actually
    reaches recorder.py's `unit_price_missing_for_non_token_unit` warning branch
    (that branch only runs INSIDE the `if pricing is not None:` guard — i.e. a
    snapshot ROW must exist for it to fire at all).

    insert_pricing_snapshot=False -> skip the pricing_snapshots row entirely (the
    OTHER, distinct "row absent" condition M11 also names — _fetch_latest_pricing
    then returns None, so recorder.py's per-tool_call dispatch/warning never runs;
    the row silently reverts to pricing_unit='per_token'/quantity=NULL instead).
    """
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider, region)"
            " VALUES (:id, 'MCP Tool Call', NULL, :active, :modality, 'hydroa', 'global')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": MCP_TOOL_CALL_MODEL_ID, "active": active, "modality": modality},
    )
    if insert_pricing_snapshot:
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
                "  pricing_unit, unit_usd_per_unit, captured_at)"
                " VALUES (:sid, :id, 0, 0, 'per_tool_call', :price, now())"
            ),
            {"sid": str(uuid.uuid4()), "id": MCP_TOOL_CALL_MODEL_ID, "price": unit_usd_per_unit},
        )
    await db_session.commit()


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before/after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def _signup_owner(client: httpx.AsyncClient, *, tenant_name: str, email: str) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": "correct horse battery staple"},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    login = await client.post(
        "/admin/auth/login",
        json={"email": email, "password": "correct horse battery staple"},
    )
    assert login.status_code == 200, login.text
    jwt = login.json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "tool-call-metering-ci"},
        headers={"Authorization": f"Bearer {jwt}"},
    )
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": jwt,
        "email": email,
    }


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create API key (OWNER role); tenant gets default markup_pct=20."""
    n = uuid.uuid4().hex[:8]
    return await _signup_owner(client, tenant_name=f"ToolCallCo-{n}", email=f"owner-{n}@toolcallmetering.io")


@pytest.fixture
async def owner_b(client: httpx.AsyncClient) -> dict[str, str]:
    """Second, independent tenant — for cross-tenant isolation scenarios."""
    n = uuid.uuid4().hex[:8]
    return await _signup_owner(client, tenant_name=f"ToolCallCoB-{n}", email=f"owner-b-{n}@toolcallmetering.io")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class FakeUsageRecorder:
    """Duck-typed UsageRecorder fake — records every call() kwargs; optionally raises."""

    supported_extras: frozenset[str] = frozenset(
        {"pricing_unit", "quantity", "tags"}
    )

    def __init__(self, *, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def record(self, **kwargs: Any) -> None:
        if self._raises is not None:
            raise self._raises
        self.calls.append(kwargs)


class FakeRedis:
    """Minimal fake of the redis.asyncio SET NX EX surface used by the dedupe gate."""

    def __init__(self, *, raises: Exception | None = None) -> None:
        self._claimed: set[str] = set()
        self._raises = raises

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        del value, ex
        if self._raises is not None:
            raise self._raises
        if not nx:
            self._claimed.add(key)
            return True
        if key in self._claimed:
            return None  # NX semantics: key already present -> SET fails
        self._claimed.add(key)
        return True
