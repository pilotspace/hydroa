"""provider_cost_reconciliation — stamp every usage_records row with the cost
basis that produced it, and persist the raw upstream-reported cost for audit.

Revision ID: a7d2e9c4f1b6
Revises: f3c8d1a6b9e4
Create Date: 2026-06-17

Additive migration (provider-cost-reconciliation TASK.md §3 DDL):

  usage_records:
    ADD COLUMN cost_basis    TEXT           NOT NULL DEFAULT 'catalog';
    ADD COLUMN provider_cost NUMERIC(20,10) NULL;

cost_basis is NOT NULL DEFAULT 'catalog' — every pre-existing row was billed by
catalog math, so the default reads true historically; PostgreSQL applies the
server_default instantly for a new NOT NULL column with a DEFAULT (no full-table
rewrite on PG 11+). provider_cost is NULLABLE with no default: it holds the RAW
upstream-reported cost (pre-markup) only on provider-basis rows; catalog rows
leave it NULL.

downgrade() drops the two columns in reverse ADD order.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7d2e9c4f1b6"
down_revision: str | None = "f3c8d1a6b9e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add cost_basis + provider_cost to usage_records (additive, instant on PG 11+)."""
    op.add_column(
        "usage_records",
        sa.Column("cost_basis", sa.Text(), nullable=False, server_default="catalog"),
    )
    op.add_column(
        "usage_records",
        sa.Column("provider_cost", sa.Numeric(20, 10), nullable=True),
    )


def downgrade() -> None:
    """Drop the two additive columns in reverse ADD order.

    Safe: additive-only; no existing data depends on them at migration time.
    """
    op.drop_column("usage_records", "provider_cost")
    op.drop_column("usage_records", "cost_basis")
