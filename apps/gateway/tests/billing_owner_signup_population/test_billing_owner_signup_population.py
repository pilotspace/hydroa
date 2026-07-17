"""RED suite — billing-owner populated on signup (billing-owner-signup-population TASK.md
§1 Accept / §3 CONTRACT, FROZEN @ v1).

Closes the billing-owner-of-record milestone-goal gap: the parent task backfilled only
EXISTING tenants; a brand-new signup left `tenants.billing_owner_user_id` NULL. This suite
asserts a fresh tenant carries its founding OWNER as billing-owner-of-record from creation.

RED at Ground SHA 3c27af5: `SqlAlchemyIdentityRepository.create_tenant_with_owner` never
sets `billing_owner_user_id`, so the pointer is NULL and each assertion below fails until
BUILD adds the post-flush assignment. DO NOT weaken these tests to pass — that is Build's
job (the fix is one flush + one assignment inside the existing `begin()` block).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.infrastructure.orm import PlanRow

SIGNUP = "/admin/auth/signup"
PASSWORD = "billing-owner-signup-horse-01"


async def _seed_free_plan(db_session: AsyncSession) -> uuid.UUID:
    """Seed the `free` plan (personal signup's default) — create_all doesn't replay the
    migration seed; mirrors the account_type_discriminator suite's own seed idiom."""
    row = PlanRow(
        id=uuid.uuid4(),
        name="free",
        display_name="Free",
        seat_cap=1,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _signup(client: httpx.AsyncClient, *, account_type: str | None) -> httpx.Response:
    body: dict[str, Any] = {
        "tenant_name": f"BillOwnerCo-{uuid.uuid4().hex[:8]}",
        "email": f"owner-{uuid.uuid4().hex[:8]}@billowner.io",
        "password": PASSWORD,
    }
    if account_type is not None:
        body["account_type"] = account_type
    return await client.post(SIGNUP, json=body)


async def _billing_owner(db_session: AsyncSession, tenant_id: str) -> str | None:
    result = await db_session.execute(
        text("SELECT billing_owner_user_id FROM tenants WHERE id = :id"),
        {"id": tenant_id},
    )
    val = result.scalar_one()
    return str(val) if val is not None else None


async def test_business_signup_sets_billing_owner_to_founding_owner(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """§1 Accept — a business signup's tenant.billing_owner_user_id == the created OWNER's id."""
    resp = await _signup(client, account_type="business")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    tenant_id, user_id = body["tenant_id"], body["user_id"]

    billing_owner = await _billing_owner(db_session, tenant_id)
    assert billing_owner is not None, "fresh tenant must not be left without a billing owner"
    assert billing_owner == user_id


async def test_personal_signup_sets_billing_owner_to_founding_owner(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """§1 Accept — the personal-signup path (lands on free plan) also carries the pointer."""
    await _seed_free_plan(db_session)
    resp = await _signup(client, account_type="personal")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    tenant_id, user_id = body["tenant_id"], body["user_id"]

    billing_owner = await _billing_owner(db_session, tenant_id)
    assert billing_owner is not None
    assert billing_owner == user_id


async def test_billing_owner_matches_the_sole_owner_row(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """The pointer resolves to the tenant's actual OWNER user (not a dangling / cross id)."""
    resp = await _signup(client, account_type="business")
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["tenant_id"]

    billing_owner = await _billing_owner(db_session, tenant_id)
    owner = await db_session.execute(
        text("SELECT id, role FROM users WHERE tenant_id = :id"),
        {"id": tenant_id},
    )
    rows = owner.all()
    assert len(rows) == 1
    owner_id, owner_role = str(rows[0][0]), str(rows[0][1])
    assert owner_role == "owner"
    assert billing_owner == owner_id
