"""Suite-local fixtures/helpers for plan-rate-enforcement (TASK.md §3 CONTRACT, FROZEN @
v1). Reuses the top-level `app`/`client`/`db_session`/`settings` fixtures (real Postgres,
real Redis — tests/conftest.py); mirrors tests/plan_seat_cap/conftest.py's own
signup/seed helper style and tests/rate_limits/test_rate_limits.py's own real-Redis
`redis_client` + `_wire_*` seam-wiring idiom exactly.
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

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
COMPLETIONS = "/v1/chat/completions"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code!r}: {body}"
    return body


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str | None = None,
    email: str | None = None,
    password: str = "plan-rate-horse-battery-01",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id)."""
    name = tenant_name or f"RateEnforceCo-{uuid.uuid4().hex[:8]}"
    owner_email = email or f"owner-{uuid.uuid4().hex[:8]}@platerate.io"
    signup = await client.post(
        SIGNUP, json={"tenant_name": name, "email": owner_email, "password": password}
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post(LOGIN, json={"email": owner_email, "password": password})
    assert login.status_code == 200, f"login failed: {login.text}"
    return login.json()["access_token"], tenant_id


async def create_key(
    client: httpx.AsyncClient, *, jwt: str, name: str, rpm_limit: int | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if rpm_limit is not None:
        body["rpm_limit"] = rpm_limit
    resp = await client.post(ADMIN_KEYS, json=body, headers=bearer(jwt))
    assert resp.status_code == 201, f"key creation failed: {resp.text}"
    result: dict[str, Any] = resp.json()
    return result


async def seed_plan(
    db_session: AsyncSession,
    *,
    name: str,
    rpm_limit_default: int | None = None,
    tpm_limit_default: int | None = None,
) -> str:
    """Insert a `plans` row directly via ORM (create_all doesn't replay the migration's
    own seed INSERT — mirrors tests/plan_seat_cap/conftest.py's own `seed_plan`)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=None,
        budget_usd_monthly_default=None,
        rpm_limit_default=rpm_limit_default,
        tpm_limit_default=tpm_limit_default,
        model_allowlist=None,
        feature_flags=[],
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)


async def assign_plan(db_session: AsyncSession, *, tenant_id: str, plan_id: str | None) -> None:
    await db_session.execute(
        text("UPDATE tenants SET plan_id = :pid WHERE id = :tid"),
        {"pid": plan_id, "tid": tenant_id},
    )
    await db_session.commit()


async def set_tenant_rate_limits(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET rpm_limit = :rpm, tpm_limit = :tpm WHERE id = :tid"),
        {"rpm": rpm_limit, "tpm": tpm_limit, "tid": tenant_id},
    )
    await db_session.commit()


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on this worker's private logical db; flushed per test —
    mirrors tests/rate_limits/test_rate_limits.py's own `redis_client` fixture exactly."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model for proxy/completions tests."""
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


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from the rate_limits suite's own precedent."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-plan-rate-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body


def wire_rate_limiter(app: Any, redis: Any) -> None:
    from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter

    app.state.rate_limiter = RedisLuaRateLimiter(redis=redis)


def wire_budget_guard(app: Any, redis: Any) -> None:
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    app.state.budget_guard = RedisBudgetGuard(redis=redis, session_factory=app.state.sessionmaker)


def wire_plan_rate_limit_resolver(app: Any, *, session_factory: Any = None) -> None:
    """Wire a REAL PlanRateLimitResolver onto app.state — the seam
    proxy/api/deps.py::get_completion_use_case reads (getattr, None-default)."""
    from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import (
        PlanRateLimitResolver,
    )

    app.state.plan_rate_limit_resolver = PlanRateLimitResolver(
        session_factory=session_factory or app.state.sessionmaker
    )


__all__ = [
    "ADMIN_KEYS",
    "COMPLETIONS",
    "FakeCompletionUpstream",
    "assert_problem",
    "assign_plan",
    "auth_key",
    "bearer",
    "create_key",
    "seed_plan",
    "set_tenant_rate_limits",
    "signup_and_login",
    "wire_budget_guard",
    "wire_plan_rate_limit_resolver",
    "wire_rate_limiter",
]
