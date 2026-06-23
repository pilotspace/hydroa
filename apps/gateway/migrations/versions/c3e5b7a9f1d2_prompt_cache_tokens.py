"""prompt_cache_tokens — add cache_creation_usd_per_token to pricing_snapshots and
cache_creation_tokens to usage_records.

Revision ID: c3e5b7a9f1d2
Revises: a2c4e6f8b0d1
Create Date: 2026-06-23

Additive migration (prompt-cache-passthrough TASK.md §3 DDL):

  pricing_snapshots:
    ADD COLUMN cache_creation_usd_per_token NUMERIC(20,10) NULL;

  usage_records:
    ADD COLUMN cache_creation_tokens INTEGER NOT NULL DEFAULT 0;

The price column is NULLABLE with no default — an existing snapshot has NULL,
so the recorder falls back to the prompt rate (byte-identical flat path).
The usage_records column is NOT NULL DEFAULT 0; PostgreSQL applies the
server_default instantly for new NOT NULL columns added with a DEFAULT —
no full-table rewrite on PG 11+. All existing rows carry cache_creation_tokens=0.

downgrade() drops both columns in reverse ADD order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3e5b7a9f1d2"
down_revision: str | None = "a2c4e6f8b0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cache-creation price to pricing_snapshots and count to usage_records."""
    # --- pricing_snapshots: nullable cache_creation tier price ---
    op.add_column(
        "pricing_snapshots",
        sa.Column("cache_creation_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )

    # --- usage_records: cache_creation count (NOT NULL DEFAULT 0) ---
    op.add_column(
        "usage_records",
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Drop both additive columns in reverse ADD order.

    Safe: additive-only; no existing data depends on them at migration time.
    """
    op.drop_column("usage_records", "cache_creation_tokens")
    op.drop_column("pricing_snapshots", "cache_creation_usd_per_token")
