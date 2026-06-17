"""tiered_token_billing — add per-tier price columns to pricing_snapshots and
per-tier token-count columns to usage_records.

Revision ID: f3c8d1a6b9e4
Revises: e2b7f4c9a1d8
Create Date: 2026-06-17

Additive migration (tiered-token-billing TASK.md §3 DDL):

  pricing_snapshots:
    ADD COLUMN cached_input_usd_per_token NUMERIC(20,10) NULL;
    ADD COLUMN reasoning_usd_per_token    NUMERIC(20,10) NULL;
  usage_records:
    ADD COLUMN cached_tokens    INTEGER NOT NULL DEFAULT 0;
    ADD COLUMN reasoning_tokens INTEGER NOT NULL DEFAULT 0;

Both price columns are NULLABLE with no default — an existing snapshot has NULL
tier prices, so the recorder falls back to the base prompt/completion price (the
byte-identical flat path). The two usage_records columns are NOT NULL DEFAULT 0;
PostgreSQL applies the server_default instantly for new NOT NULL columns added with
a DEFAULT — no full-table rewrite on PG 11+. All existing usage_records rows carry
cached_tokens=0 and reasoning_tokens=0.

downgrade() drops the four columns in reverse ADD order within each table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c8d1a6b9e4"
down_revision: str | None = "e2b7f4c9a1d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the tier-price columns to pricing_snapshots and the tier-count columns
    to usage_records.
    """
    # --- pricing_snapshots: nullable tier prices ---
    op.add_column(
        "pricing_snapshots",
        sa.Column("cached_input_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )
    op.add_column(
        "pricing_snapshots",
        sa.Column("reasoning_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )

    # --- usage_records: tier counts (NOT NULL DEFAULT 0) ---
    op.add_column(
        "usage_records",
        sa.Column("cached_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_records",
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Drop the four additive columns in reverse ADD order within each table.

    Safe: additive-only; no existing data depends on them at migration time.
    """
    # usage_records — drop reasoning_tokens (added last), then cached_tokens
    op.drop_column("usage_records", "reasoning_tokens")
    op.drop_column("usage_records", "cached_tokens")

    # pricing_snapshots — drop reasoning_usd_per_token, then cached_input_usd_per_token
    op.drop_column("pricing_snapshots", "reasoning_usd_per_token")
    op.drop_column("pricing_snapshots", "cached_input_usd_per_token")
