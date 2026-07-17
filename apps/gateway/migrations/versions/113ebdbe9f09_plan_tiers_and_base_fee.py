"""plan_tiers_and_base_fee — 5-tier plans catalog + base_price_usd_monthly.

Revision ID: 113ebdbe9f09
Revises: a7c3e9f1b2d4
Create Date: 2026-07-16

plan-tiers-and-base-fee TASK.md §3 (FROZEN @ v1). Additive + data-restructure, single
alembic head, reversible:

  - ADD `plans.base_price_usd_monthly` (NUMERIC(12,2), nullable) + CHECK
    `ck_plans_base_price_positive` (NULL or > 0) — mirrors `seat_price_usd_monthly`'s own
    additive column+CHECK precedent (f1ef6b05a732_seat_billing.py).
  - id-preserving UPDATEs (never DELETE+reinsert — `plan_id` FK is `ON DELETE RESTRICT`,
    task-1's own docstring: "do NOT delete it"):
      starter    -> display_name='Starter', seat_cap=1 (was 3), base_price_usd_monthly=1.00
      individual -> name='pro', display_name='Pro',    base_price_usd_monthly=20.00
      team       -> base_price_usd_monthly=99.00
      enterprise -> base_price_usd_monthly stays NULL (no UPDATE needed)
  - INSERT one NEW `free` row (seat_cap=1, base_price_usd_monthly=NULL — invented
    placeholder budget=5.00/rpm=30/tpm=20000, DATA not SHAPE, mirrors a7c3e9f1b2d4's own
    `individual` seed note) — the personal-signup default (tenants/application/
    use_cases.py repoints the `"individual"` literal to `"free"` in this SAME task).

Final catalog (§3 CONTRACT, id-preserving for starter/pro/team/enterprise):
  free       | Free       | base_price=NULL  | seat_cap=1     (NEW row)
  starter    | Starter    | base_price=1.00  | seat_cap=1     (repurposed, was cap=3)
  pro        | Pro        | base_price=20.00 | seat_cap=1     (renamed from `individual`)
  team       | Team       | base_price=99.00 | seat_cap=NULL
  enterprise | Enterprise | base_price=NULL  | seat_cap=NULL

Downgrade (M5): fully reversible — restores EXACTLY task-1's post-a7c3e9f1b2d4 catalog:
DELETE the free row, rename pro back to individual (base_price NULL), restore starter's
display_name/seat_cap to 'Starter'/3, clear team's base_price, drop the CHECK + column. A
REAL dependent test (tests/migrations/test_account_type_backfill.py::
test_downgrade_removes_column_and_seed) downgrades PAST this migration to a7c3e9f1b2d4's
own parent (22164094fd6b) and re-checks state there — this migration's downgrade MUST hand
back a clean `individual` row for that migration's own (untouched) downgrade to then
delete; not merely "additive-safe", a byte-exact round-trip.

⚠ STARTER-PLAN REPURPOSE COLLISION (§0 Issues/Risks, Tin-directed, out of scope to
reassign): `plans.name='starter'` is live-assignable via the superadmin plan-assign
endpoint — repurposing this SAME row's shape in place also silently relabels/re-caps any
business tenant already assigned to it. No known production tenant confirmed on `starter`
today (unverified).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from gateway.core.ids import uuid7

# revision identifiers, used by Alembic.
revision: str = "113ebdbe9f09"
down_revision: str | None = "a7c3e9f1b2d4"
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
    sa.column("base_price_usd_monthly", sa.Numeric(12, 2)),
)


def upgrade() -> None:
    op.add_column(
        "plans",
        sa.Column("base_price_usd_monthly", sa.Numeric(12, 2), nullable=True),
    )
    op.create_check_constraint(
        "ck_plans_base_price_positive",
        "plans",
        "base_price_usd_monthly IS NULL OR base_price_usd_monthly > 0",
    )

    # id-preserving UPDATEs (never DELETE+reinsert — plan_id FK is ON DELETE RESTRICT).
    op.execute(
        sa.text(
            "UPDATE plans SET display_name = 'Starter', seat_cap = 1,"
            " base_price_usd_monthly = '1.00' WHERE name = 'starter'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE plans SET name = 'pro', display_name = 'Pro',"
            " base_price_usd_monthly = '20.00' WHERE name = 'individual'"
        )
    )
    op.execute(sa.text("UPDATE plans SET base_price_usd_monthly = '99.00' WHERE name = 'team'"))
    # enterprise: base_price_usd_monthly stays NULL — no UPDATE needed.

    # NEW `free` row — the personal-signup default (⚠ invented placeholder
    # budget/rpm/tpm — DATA not SHAPE, mirrors a7c3e9f1b2d4's own `individual` seed note).
    op.bulk_insert(
        _PLANS_TABLE,
        [
            {
                "id": uuid7(),
                "name": "free",
                "display_name": "Free",
                "seat_cap": 1,
                "budget_usd_monthly_default": Decimal("5.00"),
                "rpm_limit_default": 30,
                "tpm_limit_default": 20000,
                "base_price_usd_monthly": None,
            }
        ],
    )


def downgrade() -> None:
    """Reverse in dependency order: data -> constraint -> column. Restores EXACTLY
    task-1's post-a7c3e9f1b2d4 catalog (M5) — see module docstring."""
    op.execute(sa.text("DELETE FROM plans WHERE name = 'free'"))
    op.execute(
        sa.text(
            "UPDATE plans SET name = 'individual', display_name = 'Individual',"
            " base_price_usd_monthly = NULL WHERE name = 'pro'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE plans SET display_name = 'Starter', seat_cap = 3,"
            " base_price_usd_monthly = NULL WHERE name = 'starter'"
        )
    )
    op.execute(sa.text("UPDATE plans SET base_price_usd_monthly = NULL WHERE name = 'team'"))
    op.drop_constraint("ck_plans_base_price_positive", "plans", type_="check")
    op.drop_column("plans", "base_price_usd_monthly")
