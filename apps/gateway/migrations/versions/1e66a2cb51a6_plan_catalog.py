"""plan_catalog — tier catalog (`plans` table) + additive tenant plan/seat_cap association.

Revision ID: 1e66a2cb51a6
Revises: 1193bc6178f3
Create Date: 2026-07-04

Creates the `plans` reference table (plan-catalog TASK.md §3, FROZEN @ v1): a named,
superadmin-assignable governance profile for a customer tenant's usage ceilings — seat
headcount, $ budget default, rate-limit defaults. Seeded in this SAME migration with
exactly 3 rows (starter/team/enterprise) via `op.bulk_insert` — no application code path
creates/edits/deletes a `plans` row in v1 (superadmin tier-DEFINITION CRUD is an explicit
non-goal; the table exists so a later task can add that without a schema redesign).

Also adds TWO additive, nullable columns to the existing `tenants` table:
  - `plan_id` (FK -> plans.id, ON DELETE RESTRICT) — NULL = unplanned, the universal
    starting state for every pre-existing AND every newly-signed-up tenant (no backfill,
    no auto-assignment at signup).
  - `seat_cap` (INTEGER, CHECK > 0 if set) — a per-tenant override, independent of the
    assigned plan's own seat_cap (Enterprise/custom negotiation).

Defense-in-depth (M3/R8): a NEW CHECK constraint `ck_tenants_platform_no_plan` ensures the
platform tenant (`kind='platform'`) can never hold a plan_id, even if application code
were bypassed — mirrors this same table's own existing `ck_tenants_kind` /
`tenants_platform_kind_uidx` precedent (see 3fc2328e5e82_platform_tenant_seed.py).

Seed values (⚠ INVENTED placeholders per TASK.md §1's own top-ranked assumption — DATA
rows, not a contract SHAPE; seat_cap values trace exactly to /pricing's own "up to 3
users"/"Unlimited users" copy, the $/rpm/tpm numbers do not trace to any confirmed
commercial source):
  ('starter',    'Starter',    seat_cap=3,    budget=50.00,  rpm=60,  tpm=40000)
  ('team',       'Team',       seat_cap=NULL, budget=500.00, rpm=600, tpm=400000)
  ('enterprise', 'Enterprise', seat_cap=NULL, budget=NULL,   rpm=NULL, tpm=NULL)

Downgrade: safe, additive-only — no pre-existing data depends on either new tenants
column or the new table at migration time (mirrors tenant_model_presets's own
additive-migration downgrade note).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from gateway.core.ids import uuid7

# revision identifiers, used by Alembic.
revision: str = "1e66a2cb51a6"
down_revision: str | None = "1193bc6178f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PLANS_TABLE = sa.table(
    "plans",
    sa.column("id", PGUUID(as_uuid=True)),
    sa.column("name", sa.Text()),
    sa.column("display_name", sa.Text()),
    sa.column("seat_cap", sa.Integer()),
    sa.column("budget_usd_monthly_default", sa.Numeric(12, 2)),
    sa.column("rpm_limit_default", sa.Integer()),
    sa.column("tpm_limit_default", sa.Integer()),
)


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("seat_cap", sa.Integer(), nullable=True),
        sa.Column("budget_usd_monthly_default", sa.Numeric(12, 2), nullable=True),
        sa.Column("rpm_limit_default", sa.Integer(), nullable=True),
        sa.Column("tpm_limit_default", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", name="plans_name_key"),
        sa.CheckConstraint("seat_cap IS NULL OR seat_cap > 0", name="ck_plans_seat_cap_positive"),
        sa.CheckConstraint(
            "budget_usd_monthly_default IS NULL OR budget_usd_monthly_default > 0",
            name="ck_plans_budget_default_positive",
        ),
        sa.CheckConstraint(
            "rpm_limit_default IS NULL OR rpm_limit_default > 0",
            name="ck_plans_rpm_default_positive",
        ),
        sa.CheckConstraint(
            "tpm_limit_default IS NULL OR tpm_limit_default > 0",
            name="ck_plans_tpm_default_positive",
        ),
    )

    op.bulk_insert(
        _PLANS_TABLE,
        [
            {
                "id": uuid7(),
                "name": "starter",
                "display_name": "Starter",
                "seat_cap": 3,
                "budget_usd_monthly_default": "50.00",
                "rpm_limit_default": 60,
                "tpm_limit_default": 40000,
            },
            {
                "id": uuid7(),
                "name": "team",
                "display_name": "Team",
                "seat_cap": None,
                "budget_usd_monthly_default": "500.00",
                "rpm_limit_default": 600,
                "tpm_limit_default": 400000,
            },
            {
                "id": uuid7(),
                "name": "enterprise",
                "display_name": "Enterprise",
                "seat_cap": None,
                "budget_usd_monthly_default": None,
                "rpm_limit_default": None,
                "tpm_limit_default": None,
            },
        ],
    )

    op.add_column(
        "tenants",
        sa.Column("plan_id", PGUUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tenants_plan_id_fkey",
        "tenants",
        "plans",
        ["plan_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "tenants",
        sa.Column("seat_cap", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_tenants_seat_cap_positive",
        "tenants",
        "seat_cap IS NULL OR seat_cap > 0",
    )
    op.create_check_constraint(
        "ck_tenants_platform_no_plan",
        "tenants",
        "plan_id IS NULL OR kind != 'platform'",
    )


def downgrade() -> None:
    """Reverse in dependency order: constraints -> columns -> table."""
    op.drop_constraint("ck_tenants_platform_no_plan", "tenants", type_="check")
    op.drop_constraint("ck_tenants_seat_cap_positive", "tenants", type_="check")
    op.drop_constraint("tenants_plan_id_fkey", "tenants", type_="foreignkey")
    op.drop_column("tenants", "seat_cap")
    op.drop_column("tenants", "plan_id")
    op.drop_table("plans")
