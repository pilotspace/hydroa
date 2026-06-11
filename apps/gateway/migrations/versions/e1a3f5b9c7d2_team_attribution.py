"""team_attribution — add team_id UUID NULL column to usage_records.

Revision ID: e1a3f5b9c7d2
Revises: d4e7f1a2b3c5
Create Date: 2026-06-11

Additive migration (team-attribution TASK.md §3 DDL):
  ALTER TABLE usage_records ADD COLUMN team_id UUID NULL;
  CREATE INDEX ix_usage_records_tenant_team ON usage_records (tenant_id, team_id);

  No FK to teams — append-only ledger; team deletion must not cascade-delete
  ledger rows (historical attribution must survive team deletion).
  No new tables; EXPECTED_TABLES manifest unchanged.

Downgrade:
  DROP INDEX IF EXISTS ix_usage_records_tenant_team;
  ALTER TABLE usage_records DROP COLUMN IF EXISTS team_id;
  (safe — additive nullable column; downgrade removes only this migration's artefacts)

Migration chain: ... → d4e7f1a2b3c5 (guardrails-core) → e1a3f5b9c7d2 (team-attribution)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "e1a3f5b9c7d2"
down_revision: str | None = "d4e7f1a2b3c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add team_id UUID NULL column + composite index to usage_records."""
    op.add_column(
        "usage_records",
        sa.Column("team_id", PGUUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_usage_records_tenant_team",
        "usage_records",
        ["tenant_id", "team_id"],
    )


def downgrade() -> None:
    """Drop composite index and team_id column from usage_records."""
    op.drop_index("ix_usage_records_tenant_team", table_name="usage_records")
    op.drop_column("usage_records", "team_id")
