"""account_type_discriminator — personal|business tenant flavor + seeded `individual` plan.

Revision ID: a7c3e9f1b2d4
Revises: 22164094fd6b
Create Date: 2026-07-16

account-type-discriminator TASK.md §3 (FROZEN @ v1). Additive, single alembic head, reversible:

  - ADD `tenants.account_type` (TEXT, nullable) — the personal|business flavor of a customer
    tenant; NULL on the reserved platform tenant.
  - Two CHECK constraints (defense-in-depth, mirror `ck_tenants_platform_no_plan`):
      ck_tenants_account_type            — value ∈ {personal, business} OR NULL
      ck_tenants_platform_no_account_type — platform (`kind='platform'`) can never carry one
  - BACKFILL every existing `kind='customer'` row to 'business' (recon 2026-07-16 decision);
    `kind='platform'` rows stay NULL.
  - SEED one `individual` plan row (seat_cap=1) via `op.bulk_insert` — mirrors
    1e66a2cb51a6_plan_catalog.py's own seed-in-migration idiom (plans are seed-only; no runtime
    CRUD). Price-neutral: `seat_price_usd_monthly`/base price left NULL — the flat base price for
    ALL plans (incl. individual) is added later by the `enterprise-base-fee` task.

Seed values (⚠ INVENTED placeholders, DATA not contract SHAPE — mirrors plan_catalog's own note):
  ('individual', 'Individual', seat_cap=1, budget=20.00, rpm=60, tpm=40000)

Downgrade: safe, additive-only — DELETE the individual plan row (RESTRICT FK: no tenant may still
reference it at downgrade time), drop the two CHECKs, drop the column.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from gateway.core.ids import uuid7

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f1b2d4"
down_revision: str | None = "22164094fd6b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLANS_TABLE = sa.table(
    "plans",
    sa.column("id", PGUUID(as_uuid=True)),
    sa.column("name", sa.Text()),
    sa.column("display_name", sa.Text()),
    sa.column("seat_cap", sa.Integer()),
    sa.column("budget_usd_monthly_default", sa.Numeric()),
    sa.column("rpm_limit_default", sa.Integer()),
    sa.column("tpm_limit_default", sa.Integer()),
)


def upgrade() -> None:
    op.add_column("tenants", sa.Column("account_type", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_tenants_account_type",
        "tenants",
        "account_type IS NULL OR account_type IN ('personal', 'business')",
    )
    op.create_check_constraint(
        "ck_tenants_platform_no_account_type",
        "tenants",
        "account_type IS NULL OR kind != 'platform'",
    )
    # Backfill: every existing customer tenant is a business account; platform stays NULL.
    op.execute("UPDATE tenants SET account_type = 'business' WHERE kind = 'customer'")
    # Seed the individual plan (personal tier). Idempotent-safe: name is UNIQUE.
    op.bulk_insert(
        _PLANS_TABLE,
        [
            {
                "id": uuid7(),
                "name": "individual",
                "display_name": "Individual",
                "seat_cap": 1,
                "budget_usd_monthly_default": Decimal("20.00"),
                "rpm_limit_default": 60,
                "tpm_limit_default": 40000,
            }
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE name = 'individual'")
    op.drop_constraint("ck_tenants_platform_no_account_type", "tenants", type_="check")
    op.drop_constraint("ck_tenants_account_type", "tenants", type_="check")
    op.drop_column("tenants", "account_type")
