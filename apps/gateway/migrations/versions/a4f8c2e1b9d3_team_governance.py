"""team_governance — add team_budget_usd column to teams table.

Revision ID: a4f8c2e1b9d3
Revises: 3a7f1c9e2b5d
Create Date: 2026-06-11

Additive migration (team-governance TASK.md §3 DDL):
  ALTER TABLE teams ADD COLUMN team_budget_usd NUMERIC(12,2) NULL;

  No new tables; EXPECTED_TABLES manifest unchanged.
  The teams table already exists (created in 3a7f1c9e2b5d_teams_core).

Downgrade: DROP COLUMN teams.team_budget_usd
           (safe — additive column; no existing code references it pre-migration)

Migration chain: ... → 3a7f1c9e2b5d (teams-core) → a4f8c2e1b9d3 (team-governance)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f8c2e1b9d3"
down_revision: str | None = "3a7f1c9e2b5d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add team_budget_usd NUMERIC(12,2) NULL column to teams table."""
    op.add_column(
        "teams",
        sa.Column(
            "team_budget_usd",
            sa.Numeric(12, 2),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop team_budget_usd column from teams table."""
    op.drop_column("teams", "team_budget_usd")
