"""Suite-local fixtures/helpers for plan-tiers-and-base-fee (TASK.md §3 — FROZEN @ v1).

Reuses the top-level `app`/`client`/`db_session` fixtures (real Postgres) plus TWO
cross-suite imports (established precedent — tests/seat_billing/conftest.py itself
cross-imports from tests/invoice_generation/conftest.py and tests/plan_seat_cap/conftest.py;
this suite mirrors that exact idiom):
  - tests/invoice_generation/conftest.py: `signup_tenant`/`make_generator`/
    `seed_usage_record` — this task's own base-fee fold is exercised via the SAME
    InvoiceGenerator wiring invoice-generation's own suite already established.
  - tests/seat_billing/conftest.py: `assign_plan`/`get_invoice_detail`/`lines_of_type` —
    the SAME invoice-detail read helpers seat-billing's own suite already established
    (this task's 'base' line is read back through the identical detail endpoint).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

# Re-exported for `from .conftest import X` convenience in every plan_tiers_and_base_fee
# test file.
from tests.invoice_generation.conftest import (  # noqa: F401
    AUGUST_START,
    JULY_START,
    make_generator,
    mint_role_token,
    seed_usage_record,
    signup_tenant,
)
from tests.seat_billing.conftest import (  # noqa: F401
    assign_plan,
    get_invoice_detail,
    lines_of_type,
)


async def seed_plan_with_base_price(
    db_session: AsyncSession,
    *,
    name: str,
    base_price: str | None,
    seat_cap: int | None = None,
) -> str:
    """Insert a `plans` row directly via ORM with an explicit base_price_usd_monthly —
    create_all doesn't replay the migration's own seed INSERT (mirrors
    tests/seat_billing/conftest.py's own `seed_plan_with_seat_price`)."""
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name=name,
        display_name=name.title(),
        seat_cap=seat_cap,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
        base_price_usd_monthly=Decimal(base_price) if base_price is not None else None,
    )
    db_session.add(row)
    await db_session.commit()
    return str(row.id)
