"""RED suite — account_type discriminator (account-type-discriminator TASK.md §3, FROZEN @ v1).

App-level signup behavior against the real Postgres `app`/`client`/`db_session` fixtures
(tests/conftest.py, public_signup_enabled=True). The migration's own backfill + free-plan
seed live in tests/migrations/test_account_type_backfill.py; here the schema comes from create_all
(ORM metadata), so the free plan is seeded via ORM before a personal signup.

RED at Ground SHA 3c27af5: `TenantRow.account_type` does not exist, and `SignupRequest` has no
`account_type` field — so the `SELECT account_type ...` assertions raise UndefinedColumn and the
personal-plan assertion never holds until BUILD adds the column, the signup field, and the
plan provisioning. DO NOT weaken these tests to pass — that is Build's job.

M6 reconciliation (plan-tiers-and-base-fee TASK.md §3, FROZEN @ v1): the personal-signup
default plan was renamed `individual` -> `pro`; a NEW `free` tier is now the default a
personal signup lands on. Every assertion here keeps the SAME behavior shape (personal
signup lands on the seeded default plan) — only the seeded/asserted plan name changed.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.infrastructure.orm import PlanRow

SIGNUP = "/admin/auth/signup"
PASSWORD = "account-type-horse-battery-01"


async def _seed_free_plan(db_session: AsyncSession) -> uuid.UUID:
    """Seed the `free` plan via ORM (create_all doesn't replay the migration seed) —
    mirrors tests/plan_seat_cap/conftest.py's own seed_plan idiom. M6 reconciliation
    (plan-tiers-and-base-fee TASK.md §3): personal signup's default plan is renamed from
    `individual` (now `pro`) to the NEW `free` tier — same behavior asserted, name updated."""
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
        "tenant_name": f"AcctCo-{uuid.uuid4().hex[:8]}",
        "email": f"owner-{uuid.uuid4().hex[:8]}@accttype.io",
        "password": PASSWORD,
    }
    if account_type is not None:
        body["account_type"] = account_type
    return await client.post(SIGNUP, json=body)


async def _tenant_row(db_session: AsyncSession, tenant_id: str) -> Any:
    result = await db_session.execute(
        text("SELECT account_type, plan_id FROM tenants WHERE id = :id"),
        {"id": tenant_id},
    )
    return result.one()


async def _owner_role(db_session: AsyncSession, tenant_id: str) -> tuple[int, str]:
    result = await db_session.execute(
        text("SELECT count(*), min(role) FROM users WHERE tenant_id = :id"),
        {"id": tenant_id},
    )
    n, role = result.one()
    return int(n), str(role)


async def test_personal_signup_lands_on_free_plan(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2/M3 — account_type=personal → tenant on the free plan, sole OWNER member.
    M6 reconciliation: was test_personal_signup_lands_on_individual_plan — same behavior
    (personal signup lands on the seeded default plan), plan renamed individual -> free."""
    free_id = await _seed_free_plan(db_session)
    resp = await _signup(client, account_type="personal")
    assert resp.status_code == 201, resp.text
    tenant_id = resp.json()["tenant_id"]

    row = await _tenant_row(db_session, tenant_id)
    assert row.account_type == "personal"
    assert row.plan_id == free_id
    n, role = await _owner_role(db_session, tenant_id)
    assert n == 1 and role == "owner"


async def test_business_signup_is_unplanned(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2/M4 — account_type=business → account_type='business', plan_id NULL (as today)."""
    resp = await _signup(client, account_type="business")
    assert resp.status_code == 201, resp.text
    row = await _tenant_row(db_session, resp.json()["tenant_id"])
    assert row.account_type == "business"
    assert row.plan_id is None


async def test_omitted_account_type_defaults_business(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """M2 — omitted account_type → business, plan_id NULL (byte-identical to pre-discriminator)."""
    resp = await _signup(client, account_type=None)
    assert resp.status_code == 201, resp.text
    row = await _tenant_row(db_session, resp.json()["tenant_id"])
    assert row.account_type == "business"
    assert row.plan_id is None


async def test_invalid_account_type_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """R1 — account_type not in {personal,business} → 422, no tenant/user row created."""
    before = await db_session.execute(text("SELECT count(*) FROM tenants"))
    n_before = int(before.scalar_one())
    resp = await _signup(client, account_type="enterprise")
    assert resp.status_code == 422, resp.text
    after = await db_session.execute(text("SELECT count(*) FROM tenants"))
    assert int(after.scalar_one()) == n_before


async def test_personal_signup_without_free_plan_fails_loud(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """R3 — the free plan seed missing at personal signup → loud failure (>=500), never a
    personal tenant silently left unplanned. M6 reconciliation: was
    test_personal_signup_without_individual_plan_fails_loud — same >=500 assertion, plan
    renamed individual -> free."""
    # deliberately do NOT seed the free plan
    resp = await _signup(client, account_type="personal")
    assert resp.status_code >= 500, resp.text


async def test_platform_tenant_stays_null_account_type(db_session: AsyncSession) -> None:
    """R2 — the reserved platform tenant can never carry account_type (defense-in-depth CHECK)."""
    from sqlalchemy.exc import IntegrityError

    plat_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": plat_id},
    )
    await db_session.commit()
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text("UPDATE tenants SET account_type = 'personal' WHERE id = :id"),
            {"id": plat_id},
        )
        await db_session.commit()
    await db_session.rollback()
