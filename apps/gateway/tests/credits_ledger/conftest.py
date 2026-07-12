"""Fixtures for the credits-ledger red suite (TASK.md §4).

Reuses the project-wide `app`/`client`/`db_session` fixtures (tests/conftest.py) and
the signup->login->create-key pattern from tests/budgets/test_budgets.py plus the
superadmin-minting pattern from tests/platform_tenant_directory.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_T = TypeVar("_T")


@pytest.fixture
def credit_guard(app: Any) -> Any:
    """Wire the REAL PostgresCreditGuard onto app.state — overrides the
    credits_gate_enabled=False default (mirrors tests/budgets wiring RedisBudgetGuard
    directly, independent of any config flag)."""
    from gateway.credits.infrastructure.postgres_guard import PostgresCreditGuard

    guard = PostgresCreditGuard(
        session_factory=app.state.sessionmaker,
        metrics=app.state.metrics_registry,
    )
    app.state.credit_guard = guard
    return guard


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create API key; returns ids + plaintext key (owner role)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "CreditsCo",
            "email": "owner@creditsco.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@creditsco.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "credits-ci"},
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
    """Insert a minimal active model (no pricing needed for admission-only tests)."""
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


@pytest.fixture
async def active_model_with_pricing(db_session: AsyncSession) -> dict[str, Any]:
    """Insert model + pricing snapshot for settle-path tests (real cost computation)."""
    model_id = "openai/gpt-4o"
    snapshot_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.01, 0.01, now())"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": snapshot_id, "m": model_id},
    )
    await db_session.commit()
    return {"model_id": model_id, "snapshot_id": snapshot_id}


def _issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id (mirrors tests/platform_tenant_directory)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code, resp.text


async def seed_tenant(db_session: AsyncSession, tenant_id: uuid.UUID, *, name: str = "Direct-Guard-Test-Co") -> None:
    """Insert a minimal customer tenant row — direct-guard tests use a fresh random
    tenant_id per test but tenant_credit_balances.tenant_id FK-REFERENCES tenants(id)
    ON DELETE RESTRICT (§3 schema), so a real tenant row must exist first."""
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT (id) DO NOTHING"),
        {"id": str(tenant_id), "name": name},
    )
    await db_session.commit()


async def seed_balance(
    db_session: AsyncSession,
    tenant_id: str,
    *,
    balance_usd: str,
    grace_usd: str = "0",
) -> None:
    """Directly seed tenant_credit_balances (bypasses the ledger — test setup only).

    Lazily creates the parent tenants row too (FK REFERENCES tenants(id) ON DELETE
    RESTRICT, §3 schema) — direct-guard tests use a fresh random tenant_id per test
    with no signup flow, so nothing else would ever create it.
    """
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name) VALUES (:t, 'Direct-Guard-Test-Co')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"t": tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO tenant_credit_balances (tenant_id, balance_usd, grace_usd)"
            " VALUES (:t, :b, :g)"
            " ON CONFLICT (tenant_id) DO UPDATE SET balance_usd = :b, grace_usd = :g"
        ),
        {"t": tenant_id, "b": balance_usd, "g": grace_usd},
    )
    await db_session.commit()


async def ledger_count(db_session: AsyncSession, tenant_id: str) -> int:
    row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM credit_ledger WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).fetchone()
    assert row is not None
    return int(row[0])


async def _set_budget(db_session: AsyncSession, tenant_id: str, budget: str | None) -> None:
    """Directly write budget_usd_monthly on the tenant row (mirrors tests/budgets)."""
    await db_session.execute(
        text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
        {"b": budget, "tid": tenant_id},
    )
    await db_session.commit()


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before/after (mirrors tests/budgets)."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def poll_until(
    predicate: Callable[[], Awaitable[_T | None]],
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> _T:
    """Poll an async predicate until it returns a truthy value or timeout_s elapses.

    Needed because settle/release fire via nested asyncio.ensure_future fire-and-forget
    tasks (usage_recorder.record_with_outcome's done-callback -> credit_guard.settle/
    release) that complete strictly AFTER the HTTP response returns — there is no
    synchronous signal the test can await directly.
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: _T | None = None
    while asyncio.get_event_loop().time() < deadline:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(interval_s)
    raise AssertionError(f"poll_until: condition not met within {timeout_s}s (last={last!r})")


async def get_balance_row(db_session: AsyncSession, tenant_id: str) -> tuple[str, str] | None:
    row = (
        await db_session.execute(
            text(
                "SELECT balance_usd, grace_usd FROM tenant_credit_balances WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).fetchone()
    if row is None:
        return None
    return str(row[0]), str(row[1])
