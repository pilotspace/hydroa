"""tenant_plan_rate_limit_columns — tenants.rpm_limit / tenants.tpm_limit overrides.

Revision ID: 22164094fd6b
Revises: d4a1b8c3f6e2
Create Date: 2026-07-15

plan-rate-enforcement TASK.md §3 (FROZEN @ v1) M0 — additive, no backfill:
  ALTER TABLE tenants ADD COLUMN rpm_limit INTEGER NULL;
  ALTER TABLE tenants ADD COLUMN tpm_limit INTEGER NULL;
  ALTER TABLE tenants ADD CONSTRAINT ck_tenants_rpm_limit_positive
    CHECK (rpm_limit IS NULL OR rpm_limit > 0);
  ALTER TABLE tenants ADD CONSTRAINT ck_tenants_tpm_limit_positive
    CHECK (tpm_limit IS NULL OR tpm_limit > 0);

Mirrors `budget_usd_monthly` / `seat_cap`'s own nullable-override + `> 0 OR NULL` check
shape exactly (see plan_catalog's own `ck_tenants_seat_cap_positive` precedent). Every
existing row gets NULL (no server_default needed for a nullable column) — a tenant with
no explicit rpm_limit/tpm_limit override falls through to its plan's own
`rpm_limit_default`/`tpm_limit_default` (already columns on `plans`, see 1e66a2cb51a6)
via `resolve_entitlements`'s tenant-override → plan-default → unlimited precedence
(TASK.md §3 M1). No tenant in this table today has ever had a tenant-layer rate ceiling
enforced against it, so NULL for every existing row is exactly byte-identical to
pre-migration behavior — inert until a superadmin explicitly sets an override.

Downgrade: drop the two CHECK constraints then the two columns (safe, additive-only —
mirrors tenant_billing_mode's own add-then-drop-only precedent, migration d4a1b8c3f6e2).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "22164094fd6b"
down_revision: str | None = "d4a1b8c3f6e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.rpm_limit / tenants.tpm_limit (nullable) + their CHECK constraints."""
    op.add_column("tenants", sa.Column("rpm_limit", sa.Integer(), nullable=True))
    op.add_column("tenants", sa.Column("tpm_limit", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_tenants_rpm_limit_positive",
        "tenants",
        "rpm_limit IS NULL OR rpm_limit > 0",
    )
    op.create_check_constraint(
        "ck_tenants_tpm_limit_positive",
        "tenants",
        "tpm_limit IS NULL OR tpm_limit > 0",
    )


def downgrade() -> None:
    """Reverse in dependency order: constraints -> columns (safe, additive)."""
    op.drop_constraint("ck_tenants_tpm_limit_positive", "tenants", type_="check")
    op.drop_constraint("ck_tenants_rpm_limit_positive", "tenants", type_="check")
    op.drop_column("tenants", "tpm_limit")
    op.drop_column("tenants", "rpm_limit")
