"""claude-gateway-protocol-compat — per-tenant non-Claude-failover opt-in flag.

Revision ID: c1a2b3d4e5f6
Revises: 4583689a7b8b
Create Date: 2026-07-14

Additive migration (claude-gateway-protocol-compat TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN allow_non_claude_failover BOOLEAN NOT NULL DEFAULT false;

No backfill needed: every pre-existing tenant defaults to false (the disclosed,
opt-in-required state — mirrors zdr_enabled/semantic_cache_enabled's own
additive-boolean-column, no-backfill convention exactly).

Downgrade: drop the column (safe, additive — no other table/row depends on it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1a2b3d4e5f6"
down_revision: str | None = "b64d469b341e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.allow_non_claude_failover (NOT NULL DEFAULT false)."""
    op.add_column(
        "tenants",
        sa.Column(
            "allow_non_claude_failover",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Drop the column (safe, additive)."""
    op.drop_column("tenants", "allow_non_claude_failover")
