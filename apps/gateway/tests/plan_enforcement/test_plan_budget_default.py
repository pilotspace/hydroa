"""RED suite: M2/R1 — plan budget-default enforcement at RedisBudgetGuard (TASK.md §3,
FROZEN @ v1).

Mirrors tests/budgets/test_budgets.py's own fixture pattern exactly (real Postgres + real
Redis db 9). RED until RedisBudgetGuard._fetch_budget consults a LEFT JOIN plans.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    assert_problem,
    assign_plan,
    auth,
    seed_active_model,
    seed_plan,
    set_tenant_budget,
    signup_owner,
    spend_key,
)
from tests import _redis_env

COMPLETIONS = "/v1/chat/completions"


class FakeCompletionUpstream:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-plan-budget-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    import redis.asyncio as aioredis

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await signup_owner(client, tenant_name="PlanBudgetCo", email="owner@planbudget.io")


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    return await seed_active_model(db_session, model_id="openai/gpt-4o")


def _install(app: Any, redis_client: Any, upstream: FakeCompletionUpstream) -> None:
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard
    app.state.completion_upstream = upstream


# ---------------------------------------------------------------------------
# M2/R1 — planned tenant with no explicit budget is blocked at the plan's default
# ---------------------------------------------------------------------------


async def test_planned_tenant_no_explicit_budget_blocked_at_plan_default(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    plan_id = await seed_plan(db_session, name="starter", budget_usd_monthly_default="50.00")
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget=None)
    await redis_client.set(spend_key(owner["tenant_id"]), b"50.00")

    upstream = FakeCompletionUpstream()
    _install(app, redis_client, upstream)

    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0

    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before, "no ledger row for a blocked request"

    # tenant's own row is unchanged
    row = (
        await db_session.execute(
            text("SELECT plan_id, budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).fetchone()
    assert str(row[0]) == plan_id
    assert row[1] is None


async def test_planned_tenant_under_plan_default_proceeds(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    plan_id = await seed_plan(db_session, name="starter", budget_usd_monthly_default="50.00")
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget=None)
    await redis_client.set(spend_key(owner["tenant_id"]), b"10.00")

    upstream = FakeCompletionUpstream()
    _install(app, redis_client, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


async def test_explicit_tenant_budget_still_wins_outright_over_plan_default(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """M2 (unchanged) — explicit tenants.budget_usd_monthly overrides the plan default."""
    plan_id = await seed_plan(db_session, name="team", budget_usd_monthly_default="500.00")
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget="10.00")
    await redis_client.set(spend_key(owner["tenant_id"]), b"10.00")

    upstream = FakeCompletionUpstream()
    _install(app, redis_client, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0


async def test_key_level_budget_wins_outright_and_tenant_check_never_reached(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """M2 (unchanged) — a key with its own hard monthly_budget_usd is authoritative; the
    plan's 500.00 default is never consulted (most-specific-wins)."""
    plan_id = await seed_plan(db_session, name="team", budget_usd_monthly_default="500.00")
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    key_id = owner["key_id"]
    await db_session.execute(
        text("UPDATE api_keys SET monthly_budget_usd = :b WHERE id = :kid"),
        {"b": "20.00", "kid": key_id},
    )
    await db_session.commit()
    await redis_client.set(
        f"usage:spend:key:{key_id}:{datetime.datetime.now(datetime.UTC).strftime('%Y%m')}", b"20.00"
    )

    upstream = FakeCompletionUpstream()
    _install(app, redis_client, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0


async def test_unplanned_tenant_no_explicit_budget_resolves_unlimited(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """M1/M7 — no plan assigned, no explicit budget -> unlimited, byte-identical to
    pre-task behavior (the pre-existing test_null_budget_unlimited scenario)."""
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget=None)
    await redis_client.set(spend_key(owner["tenant_id"]), b"9999.99")

    upstream = FakeCompletionUpstream()
    _install(app, redis_client, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(owner["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1
