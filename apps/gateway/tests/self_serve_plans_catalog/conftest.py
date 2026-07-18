"""Fixtures for the self-serve-plans-catalog red suite (TASK.md §4).

Mirrors the seed/fixture style of `tests/self_serve_checkout/conftest.py` (same signup->login
pattern, same direct role-token minting) but seeds an EXTRA business-audience self-serve tier
(`team_plus`) so the business-tenant scenario has a non-empty, meaningfully-ordered result —
the shared self-serve-checkout catalog only has ONE self-serve business tier (`team`), which
would make a business-tenant assertion vacuous (always an empty list) if reused verbatim.

RED before Build: `GET /admin/plans` (plural) is not mounted yet -> 404, the correct RED
failure for this suite (`gateway.tenants.application.self_serve_plans` does not exist either).
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

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PASSWORD = "correct horse battery catalog"

ADMIN_PLANS = "/admin/plans"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mint_role_token(app: Any, *, tenant_id: str, role: Role, email: str) -> str:
    """Mint a Bearer directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=uuid.UUID(tenant_id), role=role, email=email
    )
    return str(token)


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
    return {"tenant_id": tenant_id, "jwt": login.json()["access_token"]}


# ---------------------------------------------------------------------------
# Plan catalog seeding — the shipped 5-tier catalog PLUS one extra business
# self-serve tier (team_plus) so the business-tenant scenario is non-vacuous.
# ---------------------------------------------------------------------------

_CATALOG: dict[str, dict[str, Any]] = {
    "free": {"base": None, "audience": "personal", "self_serve": True},
    "starter": {"base": "1.00", "audience": "personal", "self_serve": True},
    "pro": {"base": "20.00", "audience": "personal", "self_serve": True},
    "team": {"base": "99.00", "audience": "business", "self_serve": True},
    "team_plus": {"base": "199.00", "audience": "business", "self_serve": True},
    "enterprise": {"base": None, "audience": "business", "self_serve": False},
}


async def _seed_catalog(db_session: AsyncSession) -> dict[str, str]:
    from gateway.tenants.infrastructure.orm import PlanRow

    ids: dict[str, str] = {}
    for name, spec in _CATALOG.items():
        pid = uuid.uuid4()
        row = PlanRow(
            id=pid,
            name=name,
            display_name=name.replace("_", " ").title(),
            base_price_usd_monthly=(Decimal(spec["base"]) if spec["base"] is not None else None),
            self_serve=spec["self_serve"],
            audience=spec["audience"],
        )
        db_session.add(row)
        ids[name] = str(pid)
    await db_session.commit()
    return ids


@pytest.fixture
async def catalog(db_session: AsyncSession) -> dict[str, str]:
    return await _seed_catalog(db_session)


async def set_tenant_plan(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    plan_id: str | None,
    account_type: str,
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET plan_id = :pid, account_type = :at WHERE id = :tid"),
        {"pid": plan_id, "at": account_type, "tid": tenant_id},
    )
    await db_session.commit()


@pytest.fixture
async def personal_free_owner(
    client: httpx.AsyncClient, db_session: AsyncSession, catalog: dict[str, str]
) -> dict[str, str]:
    """A personal tenant on the `free` plan, with a real OWNER JWT."""
    who = await signup_owner(client, tenant_name="PersonalFreeCo", email="owner@personalfreeco.io")
    await set_tenant_plan(
        db_session, tenant_id=who["tenant_id"], plan_id=catalog["free"], account_type="personal"
    )
    return who


@pytest.fixture
async def personal_pro_owner(
    client: httpx.AsyncClient, db_session: AsyncSession, catalog: dict[str, str]
) -> dict[str, str]:
    """A personal tenant on the `pro` plan (base 20) — used to prove NULL-first ordering
    (`free`'s NULL base_price_usd_monthly must sort before `starter`'s 1.00)."""
    who = await signup_owner(client, tenant_name="PersonalProCo", email="owner@personalproco.io")
    await set_tenant_plan(
        db_session, tenant_id=who["tenant_id"], plan_id=catalog["pro"], account_type="personal"
    )
    return who


@pytest.fixture
async def business_team_owner(
    client: httpx.AsyncClient, db_session: AsyncSession, catalog: dict[str, str]
) -> dict[str, str]:
    """A business tenant on the `team` plan."""
    who = await signup_owner(client, tenant_name="BusinessCo", email="owner@businessco.io")
    await set_tenant_plan(
        db_session, tenant_id=who["tenant_id"], plan_id=catalog["team"], account_type="business"
    )
    return who
