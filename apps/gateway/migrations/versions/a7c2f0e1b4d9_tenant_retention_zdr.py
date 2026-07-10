"""tenant_retention_zdr — per-tenant retention window + Zero-Data-Retention columns.

Revision ID: a7c2f0e1b4d9
Revises: 511ad8a7b65e
Create Date: 2026-07-10

Additive migration (tenant-retention-zdr TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN retention_window_days INTEGER NULL;
  ALTER TABLE tenants ADD COLUMN zdr_enabled BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE tenants ADD COLUMN zdr_enabled_at TIMESTAMPTZ NULL;

  No new tables; EXPECTED_TABLES manifest unchanged (mirrors guardrails-core precedent,
  migrations/versions/d4e7f1a2b3c5_guardrails_core.py).

  retention_window_days defaults NULL (inherits operator per-table defaults — byte-
  identical default state for every pre-existing AND every newly-signed-up tenant).
  zdr_enabled defaults false (server_default) so all existing rows are unaffected.

Downgrade:
  DROP COLUMN tenants.zdr_enabled_at / zdr_enabled / retention_window_days
  (safe — additive columns with server defaults / nullable; no pre-migration code
  references them)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c2f0e1b4d9"
down_revision: str | None = "010e6f83a709"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add retention_window_days / zdr_enabled / zdr_enabled_at to tenants."""
    op.add_column("tenants", sa.Column("retention_window_days", sa.Integer(), nullable=True))
    op.add_column(
        "tenants",
        sa.Column(
            "zdr_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("tenants", sa.Column("zdr_enabled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop the three tenant-retention-zdr columns from tenants."""
    op.drop_column("tenants", "zdr_enabled_at")
    op.drop_column("tenants", "zdr_enabled")
    op.drop_column("tenants", "retention_window_days")
