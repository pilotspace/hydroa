"""Failing-first (red) suite for GET /admin/plan (billing-ui TASK.md §3 M8, FROZEN @ v1).

Mirrors tests/budgets/test_budgets.py's own fixture pattern (real Postgres via the root
`client`/`db_session`/`app` fixtures) and tests/plan_enforcement/conftest.py's seed/assign
helpers (duplicated locally, mirroring test_budgets.py's own local `api_key` fixture
precedent rather than cross-importing a sibling suite's conftest).

RED before Build: `gateway.tenants.api.plan_router` does not exist yet -> ImportError /
route not registered -> 404, the correct RED failure for this suite.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

ADMIN_PLAN = "/admin/plan"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PASSWORD = "correct horse battery plan-router"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> dict[str, str]:
    signup = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, f"login failed: {login.text}"
    jwt = login.json()["access_token"]

    return {"tenant_id": tenant_id, "jwt": jwt}


def mint_role_token(app: Any, *, tenant_id: str, role: Role, email: str) -> str:
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), role=role, email=email
    )
    return str(token)


async def seed_plan(
    db_session: AsyncSession,
    *,
    name: str,
    display_name: str | None = None,
    seat_cap: int | None = None,
    budget_usd_monthly_default: str | Decimal | None = None,
    rpm_limit_default: int | None = None,
    tpm_limit_default: int | None = None,
    model_allowlist: list[str] | None = None,
    feature_flags: list[str] | None = None,
) -> str:
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=display_name or name.title(),
        seat_cap=seat_cap,
        budget_usd_monthly_default=(
            Decimal(str(budget_usd_monthly_default))
            if budget_usd_monthly_default is not None
            else None
        ),
        rpm_limit_default=rpm_limit_default,
        tpm_limit_default=tpm_limit_default,
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


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await signup_owner(client, tenant_name="PlanRouterCo", email="owner@planrouter.io")


# ---------------------------------------------------------------------------
# M8 — RBAC: any authenticated role passes (mirrors GET /admin/budget)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role",
    [Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER],
)
async def test_any_authenticated_role_passes(
    client: httpx.AsyncClient,
    app: Any,
    owner: dict[str, str],
    role: Role,
) -> None:
    """M8 — every role (incl. operator/viewer/member, who lack INVOICES_READ) gets a 200."""
    token = mint_role_token(
        app, tenant_id=owner["tenant_id"], role=role, email=f"{role.value}@planrouter.io"
    )

    resp = await client.get(ADMIN_PLAN, headers=auth(token))

    assert resp.status_code == 200, f"role {role} expected 200, got {resp.status_code}: {resp.text}"


async def test_unauthenticated_request_is_rejected(client: httpx.AsyncClient) -> None:
    """No Authorization header -> 401, never a 200 with degraded trust."""
    resp = await client.get(ADMIN_PLAN)

    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# R6 / M7 — unplanned tenant: plan: null, 200, grandfathered-unlimited semantics
# ---------------------------------------------------------------------------


async def test_unplanned_tenant_with_explicit_budget_returns_plan_null_and_tenant_budget(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    """R6 — plan_id IS NULL -> plan: null, 200 (never an error); the tenant's own explicit
    budget still resolves and renders (the data remains meaningful)."""
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget="30.00")

    resp = await client.get(ADMIN_PLAN, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] is None
    assert body["resolved"]["effective_budget_usd_monthly"] == "30.00"
    assert body["resolved"]["plan_model_allowlist"] is None
    assert body["resolved"]["plan_feature_flags"] == []


async def test_unplanned_tenant_with_no_explicit_budget_resolves_null_ceiling(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    """M7 — no plan, no explicit budget -> effective_budget_usd_monthly is null (the UI
    renders "Unlimited" for this — never a fabricated ceiling)."""
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget=None)

    resp = await client.get(ADMIN_PLAN, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] is None
    assert body["resolved"]["effective_budget_usd_monthly"] is None


# ---------------------------------------------------------------------------
# M7/M8 — planned tenant: full plan summary + resolved entitlements
# ---------------------------------------------------------------------------


async def test_planned_tenant_returns_plan_summary_and_resolved_entitlements(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    plan_id = await seed_plan(
        db_session,
        name="team",
        display_name="Team",
        seat_cap=25,
        budget_usd_monthly_default="500.00",
        rpm_limit_default=600,
        tpm_limit_default=120000,
        model_allowlist=None,
        feature_flags=["logs_explorer", "batch"],
    )
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget=None)

    resp = await client.get(ADMIN_PLAN, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan"] == {
        "id": plan_id,
        "name": "team",
        "display_name": "Team",
        "seat_cap": 25,
        "rpm_limit_default": 600,
        "tpm_limit_default": 120000,
    }
    assert body["resolved"]["effective_budget_usd_monthly"] == "500.00"
    assert body["resolved"]["plan_model_allowlist"] is None
    assert sorted(body["resolved"]["plan_feature_flags"]) == ["batch", "logs_explorer"]


async def test_planned_tenant_explicit_budget_still_wins_over_plan_default(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    """Precedence (plan-enforcement M1, unchanged): explicit tenant setting > plan default."""
    plan_id = await seed_plan(db_session, name="team", budget_usd_monthly_default="500.00")
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_budget(db_session, tenant_id=owner["tenant_id"], budget="10.00")

    resp = await client.get(ADMIN_PLAN, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    assert resp.json()["resolved"]["effective_budget_usd_monthly"] == "10.00"


async def test_planned_tenant_with_model_allowlist_returns_it_verbatim(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    owner: dict[str, str],
) -> None:
    plan_id = await seed_plan(
        db_session,
        name="starter",
        model_allowlist=["openai/gpt-4o-mini"],
        feature_flags=["logs_explorer"],
    )
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.get(ADMIN_PLAN, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    assert resp.json()["resolved"]["plan_model_allowlist"] == ["openai/gpt-4o-mini"]
