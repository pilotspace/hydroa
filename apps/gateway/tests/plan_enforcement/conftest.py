"""Suite-local fixtures for plan-enforcement (TASK.md §3 — FROZEN @ v1).

Real Postgres (GATEWAY_TEST_DATABASE_URL, defaults gateway_test) via the root `client`/
`db_session`/`app` fixtures. Mirrors tests/plan_catalog + tests/batches/
test_batch_auto_grouping.py's own seeding/signup/token-mint helpers.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PASSWORD = "correct horse battery plan-enforcement"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> dict[str, str]:
    """Register a tenant, log in, create an API key. Returns {tenant_id, jwt, key}."""
    signup = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, f"login failed: {login.text}"
    jwt = login.json()["access_token"]

    created = await client.post(
        "/admin/keys", json={"name": f"ci-key-{tenant_name}"}, headers=auth(jwt)
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "tenant_id": tenant_id,
        "jwt": jwt,
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
    }


def mint_role_token(app: Any, *, tenant_id: str, role: Role, email: str) -> str:
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), role=role, email=email
    )
    return str(token)


async def seed_plan(
    db_session: AsyncSession,
    *,
    name: str,
    budget_usd_monthly_default: str | Decimal | None = None,
    model_allowlist: list[str] | None = None,
    feature_flags: list[str] | None = None,
) -> str:
    """Insert a `plans` row directly via ORM (create_all doesn't replay the migration's own
    seed INSERT — mirrors tests/plan_catalog's own `seeded_plans` fixture pattern)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=None,
        budget_usd_monthly_default=(
            Decimal(str(budget_usd_monthly_default))
            if budget_usd_monthly_default is not None
            else None
        ),
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=model_allowlist,
        feature_flags=feature_flags if feature_flags is not None else [],
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)


async def assign_plan(db_session: AsyncSession, *, tenant_id: str, plan_id: str) -> None:
    await db_session.execute(
        text("UPDATE tenants SET plan_id = :pid WHERE id = :tid"),
        {"pid": plan_id, "tid": tenant_id},
    )
    await db_session.commit()


async def set_tenant_budget(
    db_session: AsyncSession, *, tenant_id: str, budget: str | None
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
        {"b": budget, "tid": tenant_id},
    )
    await db_session.commit()


async def seed_active_model(db_session: AsyncSession, *, model_id: str) -> str:
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": model_id},
    )
    await db_session.commit()
    return model_id


def spend_key(tenant_id: str) -> str:
    import datetime

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:{tenant_id}:{yyyymm}"


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


__all__ = [
    "PASSWORD",
    "assert_problem",
    "assign_plan",
    "auth",
    "mint_role_token",
    "seed_active_model",
    "seed_plan",
    "set_tenant_budget",
    "signup_owner",
    "spend_key",
]
