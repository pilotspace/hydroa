"""response_caching — add cache_enabled to api_keys and tenants tables.

Revision ID: 1c4e7a9f3b2d
Revises: a4f8c2e1b9d3
Create Date: 2026-06-11

Additive migration (response-caching TASK.md §3 DDL):
  ALTER TABLE api_keys ADD COLUMN cache_enabled BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE tenants  ADD COLUMN cache_enabled BOOLEAN NOT NULL DEFAULT false;

  No new tables; EXPECTED_TABLES manifest unchanged.
  Both columns have server_default false so all existing rows default to disabled.

Downgrade:
  DROP COLUMN api_keys.cache_enabled
  DROP COLUMN tenants.cache_enabled
  (safe — additive columns with server default; no existing code references them pre-migration)

Migration chain: ... → a4f8c2e1b9d3 (team-governance) → 1c4e7a9f3b2d (response-caching)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1c4e7a9f3b2d"
down_revision: str | None = "a4f8c2e1b9d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cache_enabled BOOLEAN NOT NULL DEFAULT false to api_keys and tenants."""
    op.add_column(
        "api_keys",
        sa.Column(
            "cache_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "cache_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop cache_enabled columns from api_keys and tenants."""
    op.drop_column("api_keys", "cache_enabled")
    op.drop_column("tenants", "cache_enabled")
