"""Failing-first (red) suite for budgets (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/budgets/TASK.md §2.
Redis at redis://localhost:6380/9 (dev compose); flushed per test via the
redis_client fixture inherited from tests/usage/test_usage_metering.py pattern.

Fakes injected:
  - FakeCompletionUpstream  — reused from proxy test pattern
  - BrokenRedis             — simulates Redis unavailability in guard
  - app.state.budget_guard  — set by individual tests where needed

All assertions are behavioral (HTTP responses + DB state + upstream call counts).
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env

COMPLETIONS = "/v1/chat/completions"
ADMIN_BUDGET = "/admin/budget"

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from proxy test pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-budget-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-b1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


class BrokenRedis:
    """Raises on every call — simulates Redis being down in the budget guard."""

    async def get(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xack(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create API key; returns ids + plaintext key (owner role)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "BudgetCo",
            "email": "owner@budgetco.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@budgetco.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "budget-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model (no pricing needed for budget guard tests)."""
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.commit()
    return model_id


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def bearer(jwt: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {jwt}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status
    assert resp.json()["code"] == code


def _spend_key(tenant_id: str) -> str:
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:{tenant_id}:{yyyymm}"


async def _set_budget(db_session: AsyncSession, tenant_id: str, budget: str | None) -> None:
    """Directly write budget_usd_monthly on the tenant row."""
    await db_session.execute(
        text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
        {"b": budget, "tid": tenant_id},
    )
    await db_session.commit()


# ---------------------------------------------------------------------------
# § Scenario 1 — under-budget completion succeeds
# ---------------------------------------------------------------------------


async def test_under_budget_completion_succeeds(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """budget=10.00, counter=5.00 → 200; upstream called once."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # type: ignore[import]

    await _set_budget(db_session, api_key["tenant_id"], "10.00")
    await redis_client.set(_spend_key(api_key["tenant_id"]), b"5.00")

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ---------------------------------------------------------------------------
# § Scenario 2 — spend at budget blocks completion, zero upstream, zero ledger
# ---------------------------------------------------------------------------


async def test_spend_at_budget_blocks_completion_zero_upstream_zero_ledger(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """budget=10.00, counter=10.00 → 402 ERR_BUDGET_EXCEEDED; upstream.calls==0; no new ledger row."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # type: ignore[import]

    await _set_budget(db_session, api_key["tenant_id"], "10.00")
    await redis_client.set(_spend_key(api_key["tenant_id"]), b"10.00")

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    # Capture ledger count before
    count_before = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")
    assert upstream.calls == 0

    count_after = (await db_session.execute(text("SELECT COUNT(*) FROM usage_records"))).scalar()
    assert count_after == count_before, "No ledger row must be written for a blocked request"


# ---------------------------------------------------------------------------
# § Scenario 3 — NULL budget is unlimited
# ---------------------------------------------------------------------------


async def test_null_budget_unlimited(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """budget=NULL, counter=9999.99 → 200; guard passes unconditionally."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # type: ignore[import]

    await _set_budget(db_session, api_key["tenant_id"], None)
    await redis_client.set(_spend_key(api_key["tenant_id"]), b"9999.99")

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ---------------------------------------------------------------------------
# § Scenario 4 — missing counter (no spend yet) allows completion
# ---------------------------------------------------------------------------


async def test_missing_counter_allows_completion(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
    redis_client: Any,
) -> None:
    """budget=10.00, no Redis counter key → treat as 0.00 → 200."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # type: ignore[import]

    await _set_budget(db_session, api_key["tenant_id"], "10.00")
    # Deliberately do NOT set any spend counter in Redis

    guard = RedisBudgetGuard(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ---------------------------------------------------------------------------
# § Scenario 5 — Redis unavailable allows completion (availability-over-enforcement)
# ---------------------------------------------------------------------------


async def test_redis_down_allows_completion(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    """Redis down in guard → log warning + allow; upstream called once; no 402."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard  # type: ignore[import]

    await _set_budget(db_session, api_key["tenant_id"], "1.00")

    # Guard backed by broken Redis — must swallow error and allow
    guard = RedisBudgetGuard(redis=BrokenRedis(), session_factory=app.state.sessionmaker)
    app.state.budget_guard = guard

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hello"}]},
        headers=auth_key(api_key["key"]),
    )

    assert resp.status_code == 200
    assert upstream.calls == 1


# ---------------------------------------------------------------------------
# § Scenario 6 — PUT then GET budget roundtrip
# ---------------------------------------------------------------------------


async def test_put_get_budget_roundtrip(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
) -> None:
    """PUT /admin/budget → 200 echo; GET /admin/budget → echoes ceiling + spent=0.00."""
    put_resp = await client.put(
        ADMIN_BUDGET,
        json={"budget_usd_monthly": "25.00"},
        headers=bearer(api_key["jwt"]),
    )
    assert put_resp.status_code == 200
    assert put_resp.json()["budget_usd_monthly"] == "25.00"

    get_resp = await client.get(ADMIN_BUDGET, headers=bearer(api_key["jwt"]))
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["budget_usd_monthly"] == "25.00"
    assert body["spent_usd_month"] == "0.00"


# ---------------------------------------------------------------------------
# § Scenario 7 — member role is forbidden from PUT budget
# ---------------------------------------------------------------------------


async def test_member_forbidden_put_budget(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
) -> None:
    """member-role JWT → 403 ERR_AUTH_FORBIDDEN on PUT; budget unchanged."""
    # Record budget before the attempt
    await _set_budget(db_session, api_key["tenant_id"], "50.00")

    # Create a member user under the same tenant (insert directly — no signup API for members)
    member_id = str(uuid.uuid4())
    import hashlib

    dummy_hash = "$argon2id$v=19$m=65536,t=3,p=4$dummysalt$dummyhash"
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:uid, :tid, :email, :ph, 'member')"
        ),
        {
            "uid": member_id,
            "tid": api_key["tenant_id"],
            "email": "member@budgetco.io",
            "ph": dummy_hash,
        },
    )
    await db_session.commit()

    # Issue a JWT directly for the member (reuse the token service from app state)
    from gateway.tenants.domain.entities import Role

    member_token, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_id),
        tenant_id=uuid.UUID(api_key["tenant_id"]),
        role=Role.MEMBER,
        email="member@budgetco.io",
    )

    put_resp = await client.put(
        ADMIN_BUDGET,
        json={"budget_usd_monthly": "5.00"},
        headers=bearer(member_token),
    )
    assert_problem(put_resp, 403, "ERR_AUTH_FORBIDDEN")

    # Budget must be unchanged
    row = (
        await db_session.execute(
            text("SELECT budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None
    # budget_usd_monthly stays 50.00 (was set above); Decimal string comparison
    from decimal import Decimal

    assert Decimal(str(row[0])) == Decimal("50.00")


# ---------------------------------------------------------------------------
# § Scenario 8 — negative budget value rejected (422)
# ---------------------------------------------------------------------------


async def test_negative_budget_rejected(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """PUT /admin/budget with negative value → 422 ERR_PAYLOAD_INVALID; budget unchanged."""
    await _set_budget(db_session, api_key["tenant_id"], "20.00")

    put_resp = await client.put(
        ADMIN_BUDGET,
        json={"budget_usd_monthly": "-1.00"},
        headers=bearer(api_key["jwt"]),
    )
    assert_problem(put_resp, 422, "ERR_PAYLOAD_INVALID")

    # Budget unchanged
    row = (
        await db_session.execute(
            text("SELECT budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None
    from decimal import Decimal

    assert Decimal(str(row[0])) == Decimal("20.00")


# ---------------------------------------------------------------------------
# § Scenario 9 — tenant isolation: A's budget invisible to B
# ---------------------------------------------------------------------------


async def test_tenant_isolation_budget(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """GET /admin/budget as tenant B returns B's budget (7.00), not A's (50.00)."""
    # Tenant A
    signup_a = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "TenantAlpha",
            "email": "owner@alpha.io",
            "password": "correct horse battery",
        },
    )
    assert signup_a.status_code == 201
    tenant_a_id = signup_a.json()["tenant_id"]
    jwt_a = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@alpha.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    await _set_budget(db_session, tenant_a_id, "50.00")

    # Tenant B
    signup_b = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "TenantBeta",
            "email": "owner@beta.io",
            "password": "correct horse battery",
        },
    )
    assert signup_b.status_code == 201
    tenant_b_id = signup_b.json()["tenant_id"]
    jwt_b = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@beta.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    await _set_budget(db_session, tenant_b_id, "7.00")

    # Act: tenant B reads their own budget
    get_resp = await client.get(ADMIN_BUDGET, headers=bearer(jwt_b))
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["budget_usd_monthly"] == "7.00", "Tenant B must see their own budget only"

    # Tenant A's budget is still 50.00
    row_a = (
        await db_session.execute(
            text("SELECT budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": tenant_a_id},
        )
    ).fetchone()
    assert row_a is not None
    from decimal import Decimal

    assert Decimal(str(row_a[0])) == Decimal("50.00"), "Tenant A's budget must be unchanged"

    # Tenant A's data must NOT appear in tenant B's response
    _ = jwt_a  # referenced to avoid F841; A's JWT is only used to confirm isolation
    assert body["budget_usd_monthly"] != "50.00"
