"""service-tiers — per-key/tenant capacity-preference tier + priority markup override
+ served-tier billing discriminators.

Revision ID: 4583689a7b8b
Revises: b3e6a1d9f4c7
Create Date: 2026-07-13

Additive migration (service-tiers TASK.md §3 DDL — FROZEN @ v1):
  ALTER TABLE api_keys ADD COLUMN tier TEXT NULL
    CHECK (tier IS NULL OR tier IN ('priority', 'standard'));
  ALTER TABLE tenants  ADD COLUMN default_tier TEXT NOT NULL DEFAULT 'standard'
    CHECK (default_tier IN ('priority', 'standard'));

  CREATE TABLE tenant_priority_markup_overrides (
    id          UUID          PRIMARY KEY,
    tenant_id   UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    markup_pct  NUMERIC(7,4)  NOT NULL CHECK (markup_pct >= 0),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (tenant_id)
  );

  ALTER TABLE usage_records ADD COLUMN tier_served TEXT NOT NULL DEFAULT 'standard';
  ALTER TABLE usage_records ADD COLUMN tier_capacity_degraded BOOLEAN NOT NULL DEFAULT false;

No backfill needed anywhere: api_keys.tier is NULL-default (inherits the tenant's
default_tier, itself defaulted to 'standard' — byte-identical to pre-task behavior for
every existing row); usage_records.tier_served/tier_capacity_degraded are append-only
discriminator columns (mirrors cost_basis/usage_source convention exactly) — every
pre-existing row is honestly "standard"/false (tiering did not exist yet).

tenant_priority_markup_overrides mirrors tenant_region_multiplier_overrides' own
absent-row-means-fall-back-to-seed convention (no FK/enum on a resolved value; here
there is nothing to look up by string, so no such concern applies at all).

Downgrade: drop tenant_priority_markup_overrides (safe, additive); drop the four new
columns (safe — additive, no other table/row depends on them).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4583689a7b8b"
down_revision: str | None = "b3e6a1d9f4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tier columns to api_keys/tenants/usage_records + the new
    tenant_priority_markup_overrides table."""
    op.add_column("api_keys", sa.Column("tier", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_api_keys_tier_valid",
        "api_keys",
        "tier IS NULL OR tier IN ('priority', 'standard')",
    )

    op.add_column(
        "tenants",
        sa.Column("default_tier", sa.Text(), nullable=False, server_default="standard"),
    )
    op.create_check_constraint(
        "ck_tenants_default_tier_valid",
        "tenants",
        "default_tier IN ('priority', 'standard')",
    )

    op.create_table(
        "tenant_priority_markup_overrides",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("markup_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", name="uq_tenant_priority_markup_overrides_tenant"
        ),
        sa.CheckConstraint(
            "markup_pct >= 0", name="ck_tenant_priority_markup_overrides_markup_nonneg"
        ),
    )

    op.add_column(
        "usage_records",
        sa.Column("tier_served", sa.Text(), nullable=False, server_default="standard"),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "tier_capacity_degraded",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Drop the new columns + tenant_priority_markup_overrides table (safe, additive)."""
    op.drop_column("usage_records", "tier_capacity_degraded")
    op.drop_column("usage_records", "tier_served")
    op.drop_table("tenant_priority_markup_overrides")
    op.drop_constraint("ck_tenants_default_tier_valid", "tenants", type_="check")
    op.drop_column("tenants", "default_tier")
    op.drop_constraint("ck_api_keys_tier_valid", "api_keys", type_="check")
    op.drop_column("api_keys", "tier")
