"""RED suite — 'base' invoice line folded by InvoiceGenerator (plan-tiers-and-base-fee
TASK.md §3 — FROZEN @ v1, M2/R3, + the idempotency edge case).

RED before BUILD: `PlanRow.base_price_usd_monthly` / `_load_base_price` / the 'base' line
fold do not exist yet, so every base-fee assertion below fails — the honest
missing-implementation red.

DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    JULY_START,
    assign_plan,
    get_invoice_detail,
    lines_of_type,
    make_generator,
    mint_role_token,
    seed_plan_with_base_price,
    seed_usage_record,
    signup_tenant,
)

NIL_SEAT_KEY_ID = "00000000-0000-0000-0000-000000000000"


async def _generate(app: Any, tenant_id: str, period_start: Any = JULY_START) -> str | None:
    generator = make_generator(app)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    return str(invoice_id) if invoice_id is not None else None


# ---------------------------------------------------------------------------
# M2 — a base-fee plan tenant is billed a flat base line even at $0 usage
# ---------------------------------------------------------------------------


async def test_base_fee_plan_zero_usage_gets_flat_base_line(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Given a tenant on a $99.00 base-fee plan with ZERO usage_records for the period,
    generate_for_tenant inserts an invoice with exactly one 'base' line == 99.00, and
    total_usd includes the 99.00 base amount."""
    _owner, tid = await signup_tenant(client, tenant_name="Base Fee Co", email="bf@basefee.io")
    plan_id = await seed_plan_with_base_price(db_session, name="team-basefee", base_price="99.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None, "an invoice must be inserted even at $0 usage"

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bf-sub@basefee.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=invoice_id)

    base_lines = lines_of_type(detail, "base")
    assert len(base_lines) == 1, f"expected exactly one 'base' line, found {len(base_lines)}"
    line = base_lines[0]
    assert Decimal(str(line["amount_usd"])) == Decimal("99.00")
    assert line["model_id"] == "base"
    assert line["team_id"] is None
    assert line["key_id"] == NIL_SEAT_KEY_ID
    assert Decimal(str(detail["total_usd"])) == Decimal("99.00")


async def test_base_fee_folds_alongside_usage_into_total(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """A base-fee plan tenant WITH usage gets both a 'usage' line and a 'base' line, and
    total_usd is the sum of both (the base line is independent of usage/seat count)."""
    owner, tid = await signup_tenant(client, tenant_name="Base Usage Co", email="bu@basefee.io")
    plan_id = await seed_plan_with_base_price(db_session, name="team-basefee-2", base_price="99.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="10.00")

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bu-sub@basefee.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=invoice_id)

    base_lines = lines_of_type(detail, "base")
    usage_lines = lines_of_type(detail, "usage")
    assert len(base_lines) == 1
    assert len(usage_lines) == 1
    assert Decimal(str(detail["total_usd"])) == Decimal("109.00")


# ---------------------------------------------------------------------------
# R3 — a NULL-base-price / unplanned tenant's invoice never carries a base line
# ---------------------------------------------------------------------------


async def test_null_base_price_plan_gets_zero_base_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Given a tenant on a plan with base_price_usd_monthly NULL (enterprise-shaped),
    generate_for_tenant writes ZERO 'base' lines, ever — never a $0.00 line row."""
    owner, tid = await signup_tenant(client, tenant_name="Null Base Co", email="nb@basefee.io")
    plan_id = await seed_plan_with_base_price(
        db_session, name="enterprise-basefee", base_price=None
    )
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="5.00")

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nb-sub@basefee.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=invoice_id)

    base_lines = lines_of_type(detail, "base")
    assert base_lines == []
    assert Decimal(str(detail["total_usd"])) == Decimal("5.00")


async def test_unplanned_tenant_gets_zero_base_lines(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """An unplanned tenant (plan_id NULL, today's universal starting state) gets ZERO
    'base' lines — byte-identical to pre-task behavior."""
    owner, tid = await signup_tenant(client, tenant_name="Unplanned Co", email="up@basefee.io")
    await seed_usage_record(db_session, tenant_id=tid, cost_usd="3.00")

    invoice_id = await _generate(app, tid)
    assert invoice_id is not None

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="up-sub@basefee.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=invoice_id)

    assert lines_of_type(detail, "base") == []
    assert Decimal(str(detail["total_usd"])) == Decimal("3.00")


# ---------------------------------------------------------------------------
# Edge case — an already-issued invoice never gets a duplicate base line on re-run
# ---------------------------------------------------------------------------


async def test_rerun_does_not_duplicate_base_line(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """Given an invoice already exists for (tenant_id, period_start) including its base
    line, calling generate_for_tenant again for the same period is a silent ON CONFLICT
    DO NOTHING no-op (returns None) and never writes a second 'base' line."""
    owner, tid = await signup_tenant(client, tenant_name="Rerun Co", email="rr@basefee.io")
    plan_id = await seed_plan_with_base_price(db_session, name="team-basefee-3", base_price="99.00")
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)

    first_id = await _generate(app, tid)
    assert first_id is not None

    second_id = await _generate(app, tid)
    assert second_id is None, "a re-run for the same (tenant_id, period_start) must no-op"

    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="rr-sub@basefee.io")
    detail = await get_invoice_detail(client, token=token, invoice_id=first_id)
    assert len(lines_of_type(detail, "base")) == 1, "no second 'base' line is ever written"
