"""Fixtures for the self-serve-checkout red suite (TASK.md §4).

Reuses the project-wide `app`/`client`/`db_session` fixtures (tests/conftest.py). Mirrors
the signup->login pattern from tests/plans/test_plan_router.py and the direct role-token
minting + platform-tenant resolution from tests/credits_ledger/conftest.py.

RED before Build: `gateway.payments.*` does not exist yet, the `/admin/checkout/*` routes
are not mounted, and `Permission.BILLING_MANAGE` is absent -> ImportError / 404 / 403, the
correct RED failure for this suite.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
PASSWORD = "correct horse battery checkout"

PLAN_UPGRADE = "/admin/checkout/plan-upgrade"
CREDIT_TOPUP = "/admin/checkout/credit-topup"


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
# Plan catalog seeding (create_all builds the schema; plans table starts empty).
# Seeds the shipped 5-tier catalog WITH the new self_serve / audience signals.
# ---------------------------------------------------------------------------

_CATALOG: dict[str, dict[str, Any]] = {
    "free": {"base": None, "audience": "personal", "self_serve": True, "seat_cap": 1},
    "starter": {"base": "1.00", "audience": "personal", "self_serve": True, "seat_cap": 1},
    "pro": {"base": "20.00", "audience": "personal", "self_serve": True, "seat_cap": 1},
    "team": {"base": "99.00", "audience": "business", "self_serve": True, "seat_cap": None},
    "enterprise": {"base": None, "audience": "business", "self_serve": False, "seat_cap": None},
}


async def _seed_catalog(db_session: AsyncSession) -> dict[str, str]:
    from gateway.tenants.infrastructure.orm import PlanRow

    ids: dict[str, str] = {}
    for name, spec in _CATALOG.items():
        pid = uuid.uuid4()
        row = PlanRow(
            id=pid,
            name=name,
            display_name=name.title(),
            seat_cap=spec["seat_cap"],
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
async def personal_starter_owner(
    client: httpx.AsyncClient, db_session: AsyncSession, catalog: dict[str, str]
) -> dict[str, str]:
    """A personal tenant on the `starter` plan, with a real OWNER JWT."""
    who = await signup_owner(client, tenant_name="PersonalCo", email="owner@personalco.io")
    await set_tenant_plan(
        db_session, tenant_id=who["tenant_id"], plan_id=catalog["starter"], account_type="personal"
    )
    return who


@pytest.fixture
async def personal_pro_owner(
    client: httpx.AsyncClient, db_session: AsyncSession, catalog: dict[str, str]
) -> dict[str, str]:
    """A personal tenant on the `pro` plan (base 20)."""
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


# ---------------------------------------------------------------------------
# DB assertion helpers
# ---------------------------------------------------------------------------


async def tenant_plan_id(db_session: AsyncSession, tenant_id: str) -> str | None:
    row = (
        await db_session.execute(
            text("SELECT plan_id FROM tenants WHERE id = :t"), {"t": tenant_id}
        )
    ).fetchone()
    assert row is not None
    return str(row[0]) if row[0] is not None else None


async def ledger_count(db_session: AsyncSession, tenant_id: str) -> int:
    row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM credit_ledger WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).fetchone()
    assert row is not None
    return int(row[0])


async def balance_of(db_session: AsyncSession, tenant_id: str) -> Decimal:
    row = (
        await db_session.execute(
            text("SELECT balance_usd FROM tenant_credit_balances WHERE tenant_id = :t"),
            {"t": tenant_id},
        )
    ).fetchone()
    if row is None:
        return Decimal("0")
    return Decimal(str(row[0]))


async def session_count(db_session: AsyncSession, tenant_id: str) -> int:
    row = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM checkout_sessions WHERE tenant_id = :t"), {"t": tenant_id}
        )
    ).fetchone()
    assert row is not None
    return int(row[0])


async def session_status(db_session: AsyncSession, session_id: str) -> str | None:
    row = (
        await db_session.execute(
            text("SELECT status FROM checkout_sessions WHERE id = :i"), {"i": session_id}
        )
    ).fetchone()
    return str(row[0]) if row is not None else None


async def find_audit(db_session: AsyncSession, action: str) -> dict[str, Any] | None:
    row = (
        await db_session.execute(
            text(
                "SELECT tenant_id, actor_user_id, actor_email, action, metadata"
                " FROM audit_events WHERE action = :a ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": action},
        )
    ).fetchone()
    if row is None:
        return None
    return {
        "tenant_id": str(row[0]) if row[0] is not None else None,
        "actor_user_id": str(row[1]) if row[1] is not None else None,
        "actor_email": row[2],
        "action": row[3],
        "metadata": row[4],
    }


async def poll_until[T](
    predicate: Callable[[], Awaitable[T | None]],
    *,
    timeout_s: float = 5.0,
    interval_s: float = 0.05,
) -> T:
    """Poll an async predicate until truthy or timeout (audit is fire-and-forget)."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    last: T | None = None
    while asyncio.get_event_loop().time() < deadline:
        last = await predicate()
        if last:
            return last
        await asyncio.sleep(interval_s)
    raise AssertionError(f"poll_until: condition not met within {timeout_s}s (last={last!r})")
