"""tenant_residency_region — per-tenant fail-closed residency pin.

Revision ID: b3e6a1d9f4c7
Revises: d608e56c7485
Create Date: 2026-07-12

Additive migration (residency-policy TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN residency_region TEXT NULL;
  ALTER TABLE tenants ADD COLUMN residency_region_updated_at TIMESTAMPTZ NULL;

  No new tables; EXPECTED_TABLES manifest unchanged (mirrors
  a7c2f0e1b4d9_tenant_retention_zdr.py precedent).

  residency_region defaults NULL (no pin) — byte-identical default state for every
  pre-existing AND every newly-signed-up tenant; values are 'us' | 'eu' | 'ap' (the
  four-value catalog Region Literal minus 'global' — a tenant never pins to "global").
  residency_region_updated_at is set on every actual value CHANGE (set, change, or
  clear to NULL) — unlike zdr_enabled_at this one updates on every transition.

Downgrade:
  DROP COLUMN tenants.residency_region_updated_at / residency_region (safe — additive
  nullable columns; no pre-migration code references them)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3e6a1d9f4c7"
down_revision: str | None = "d608e56c7485"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add residency_region / residency_region_updated_at to tenants."""
    op.add_column("tenants", sa.Column("residency_region", sa.Text(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("residency_region_updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the two tenant-residency-region columns from tenants."""
    op.drop_column("tenants", "residency_region_updated_at")
    op.drop_column("tenants", "residency_region")
