"""billing_owner_of_record — designate one billing owner-of-record per tenant.

Revision ID: f94771e4aa7c
Revises: 113ebdbe9f09
Create Date: 2026-07-16

billing-owner-of-record TASK.md §3 (FROZEN @ v1, M1/M8). Additive, single alembic head,
reversible:

  - ADD `tenants.billing_owner_user_id` (UUID, nullable) — FK -> users.id ON DELETE
    RESTRICT (mirrors `plan_id`'s own nullable-FK-override shape). `use_alter`-shaped at
    the ORM layer only (breaks the tenants<->users create_all cycle for the test suite);
    this migration adds it via a plain sequential add_column + create_foreign_key since
    both tables already exist at this revision — no ordering issue here.
  - Defense-in-depth CHECK `ck_tenants_platform_no_billing_owner` (mirrors
    `ck_tenants_platform_no_plan` / `ck_tenants_platform_no_account_type`): the reserved
    platform tenant can never carry a billing owner.
  - BACKFILL every existing `kind='customer'` tenant's billing_owner_user_id to its
    earliest currently-ACTIVE OWNER user (tie-break: earliest created_at, then id),
    falling back to its earliest currently-ACTIVE BILLING_ADMIN if no active OWNER
    exists, leaving NULL (never crashing) if neither exists. `kind='platform'` stays NULL
    (excluded from the backfill WHERE clause).

Downgrade (M8): fully reversible — DROP the CHECK, DROP the column. No data restoration
needed (the column is entirely backfill-derived, re-backfillable by re-running upgrade).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "f94771e4aa7c"
down_revision: str | None = "113ebdbe9f09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("billing_owner_user_id", PGUUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "tenants_billing_owner_user_id_fkey",
        "tenants",
        "users",
        ["billing_owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_tenants_platform_no_billing_owner",
        "tenants",
        "billing_owner_user_id IS NULL OR kind != 'platform'",
    )

    # BACKFILL (§3 CONTRACT, verbatim): earliest ACTIVE owner first, falling back to the
    # earliest ACTIVE billing_admin, NULL if neither exists. kind='platform' excluded.
    op.execute(
        sa.text(
            """
            UPDATE tenants t SET billing_owner_user_id = (
                SELECT u.id FROM users u
                WHERE u.tenant_id = t.id AND u.deactivated_at IS NULL
                  AND u.role IN ('owner', 'billing_admin')
                ORDER BY (u.role = 'owner') DESC, u.created_at ASC, u.id ASC
                LIMIT 1
            ) WHERE t.kind = 'customer'
            """
        )
    )


def downgrade() -> None:
    """Reverse in dependency order: constraint -> FK -> column. No data restoration
    needed — the column is entirely backfill-derived (M8)."""
    op.drop_constraint("ck_tenants_platform_no_billing_owner", "tenants", type_="check")
    op.drop_constraint("tenants_billing_owner_user_id_fkey", "tenants", type_="foreignkey")
    op.drop_column("tenants", "billing_owner_user_id")
