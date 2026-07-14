"""RED->GREEN suite — audit-remediation C1 (budget race).

RedisBudgetGuard.check() used to be an unlocked GET-then-compare: N concurrent
calls all read the SAME stale usage:spend:* counter and all pass, because
nothing increments that counter until well after each request completes
(usage/application/recorder.py's post-hoc real-cost increment). This suite
drives check() directly (unit-level, real Redis on the shared test db) with
asyncio.gather to prove the atomic check-and-reserve fix admits AT MOST the
number of requests the budget can actually afford — never more. The OLD code
would admit ALL of them (every concurrent GET reads the identical, still-stale
counter value).
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env

from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard
from gateway.core.errors import ProblemError


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on the shared test db (mirrors tests/budgets/
    test_budgets.py's own fixture — Lua scripting requires a real Redis, not a
    hand-rolled fake)."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def _spend_key(tenant_id: uuid.UUID) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:{tenant_id}:{yyyymm}"


async def _make_tenant(db_session: AsyncSession, *, budget: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, budget_usd_monthly) VALUES (:id, :name, :budget)"),
        {"id": str(tenant_id), "name": "Race Co", "budget": budget},
    )
    await db_session.commit()
    return tenant_id


async def test_concurrent_checks_never_admit_past_the_budget_ceiling(
    app: Any, db_session: AsyncSession, redis_client: Any
) -> None:
    """budget=$1.00, committed spend=$0.60, hold_estimate=$0.50 (default): the
    first admitted call reserves $0.50, pushing prospective spend to $1.10 for
    every subsequent concurrent call — so exactly ONE of 5 truly-concurrent
    check() calls may be admitted. The old unlocked GET-then-compare would admit
    all 5 (every call reads the SAME stale $0.60, comfortably under $1.00)."""
    tenant_id = await _make_tenant(db_session, budget="1.00")
    await redis_client.set(_spend_key(tenant_id), b"0.60")

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)

    async def _attempt() -> bool:
        try:
            await guard.check(tenant_id)
            return True
        except ProblemError:
            return False

    results = await asyncio.gather(*[_attempt() for _ in range(5)])
    admitted = sum(1 for r in results if r)
    assert admitted == 1, (
        f"expected exactly 1 of 5 concurrent requests admitted under a $1.00 budget "
        f"with $0.60 already committed, got {admitted} — the atomic check-and-reserve "
        f"must close the TOCTOU race (unlocked fetch-then-compare would admit all 5)"
    )


async def test_sequential_checks_still_enforce_the_same_ceiling(
    app: Any, db_session: AsyncSession, redis_client: Any
) -> None:
    """Non-concurrent sanity check: the atomic path must not change ordinary
    single-caller enforcement — budget=$5.00, spend=$0.00 admits until the
    reservations themselves reach the ceiling."""
    tenant_id = await _make_tenant(db_session, budget="1.00")
    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)

    outcomes = []
    for _ in range(4):
        try:
            await guard.check(tenant_id)
            outcomes.append(True)
        except ProblemError:
            outcomes.append(False)

    # $1.00 budget / $0.50 hold_estimate -> 2 admits (0.00->0.50, 0.50->1.00), then deny.
    assert outcomes == [True, True, False, False]


async def test_check_and_reserve_fails_open_when_redis_lacks_lua_support(
    app: Any, db_session: AsyncSession
) -> None:
    """A duck-typed fake redis exposing only `get` (no `register_script`) must still
    fail OPEN — mirrors tests/budgets/test_budgets.py::test_redis_down_allows_completion's
    existing 'availability-over-enforcement' contract; the lazy script registration
    must not turn a missing method into a hard failure for callers of check()."""

    class _NoScriptRedis:
        async def get(self, *args: Any, **kwargs: Any) -> None:
            return None

    tenant_id = await _make_tenant(db_session, budget="1.00")
    guard = RedisBudgetGuard(redis=_NoScriptRedis(), session_factory=app.state.sessionmaker)

    # Must not raise — AttributeError from the missing register_script is swallowed
    # by check()'s existing fail-open try/except.
    await guard.check(tenant_id)
