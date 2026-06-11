"""semantic_caching — add semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false to tenants.

Revision ID: f1b2c3d4e5a6
Revises: e1a3f5b9c7d2
Create Date: 2026-06-11

Additive migration (semantic-cache TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false;

  Per-tenant opt-in flag for the normalized near-duplicate semantic cache layer (v5).
  Default false = semantic layer inactive for all pre-existing tenants.
  No new tables; EXPECTED_TABLES manifest unchanged.

Downgrade:
  ALTER TABLE tenants DROP COLUMN IF EXISTS semantic_cache_enabled;
  (safe — additive column with server default; no existing code references it pre-migration)

Migration chain: ... → e1a3f5b9c7d2 (team-attribution) → f1b2c3d4e5a6 (semantic-caching)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1b2c3d4e5a6"
down_revision: str | None = "e1a3f5b9c7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add semantic_cache_enabled BOOLEAN NOT NULL DEFAULT false to tenants."""
    op.add_column(
        "tenants",
        sa.Column(
            "semantic_cache_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop semantic_cache_enabled column from tenants."""
    op.drop_column("tenants", "semantic_cache_enabled")
