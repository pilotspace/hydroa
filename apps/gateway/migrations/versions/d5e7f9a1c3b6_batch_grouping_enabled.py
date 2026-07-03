"""batch_grouping_enabled — add batch_grouping_enabled BOOLEAN NOT NULL DEFAULT false to tenants.

Revision ID: d5e7f9a1c3b6
Revises: e5a7c9b1d3f6
Create Date: 2026-07-03

Additive migration (batch-auto-grouping TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN batch_grouping_enabled BOOLEAN NOT NULL DEFAULT false;

  Per-tenant opt-in flag for automatic diversion of eligible /v1/chat/completions
  requests into the existing batch-job-store pipeline (v57). Default false =
  every pre-existing tenant stays byte-identical (M9).
  No new tables; EXPECTED_TABLES manifest unchanged.

Downgrade:
  ALTER TABLE tenants DROP COLUMN IF EXISTS batch_grouping_enabled;
  (safe — additive column with server default; no existing code references it pre-migration)

Migration chain: ... → e5a7c9b1d3f6 (batch_jobs) → d5e7f9a1c3b6 (batch_grouping_enabled)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d5e7f9a1c3b6"
down_revision: str | None = "e5a7c9b1d3f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add batch_grouping_enabled BOOLEAN NOT NULL DEFAULT false to tenants."""
    op.add_column(
        "tenants",
        sa.Column(
            "batch_grouping_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop batch_grouping_enabled column from tenants."""
    op.drop_column("tenants", "batch_grouping_enabled")
