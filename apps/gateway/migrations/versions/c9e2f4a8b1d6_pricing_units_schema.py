"""pricing_units_schema — add pricing_unit and unit_usd_per_unit to pricing_snapshots;
add pricing_unit and quantity to usage_records.

Revision ID: c9e2f4a8b1d6
Revises: b7c1d2e3f4a5
Create Date: 2026-06-12

Additive migration (pricing-units TASK.md §3 DDL):

  pricing_snapshots:
    ALTER TABLE pricing_snapshots ADD COLUMN pricing_unit TEXT NOT NULL DEFAULT 'per_token';
    ALTER TABLE pricing_snapshots ADD COLUMN unit_usd_per_unit NUMERIC(20,10) NULL;
    UPDATE pricing_snapshots SET pricing_unit = 'per_token' WHERE pricing_unit IS NULL;

  usage_records:
    ALTER TABLE usage_records ADD COLUMN pricing_unit TEXT NOT NULL DEFAULT 'per_token';
    ALTER TABLE usage_records ADD COLUMN quantity NUMERIC(18,6) NULL;

PostgreSQL applies the server_default instantly for new NOT NULL columns added with a
DEFAULT — no full-table rewrite on PG 11+.  All existing pricing_snapshots rows carry
pricing_unit='per_token' and unit_usd_per_unit=NULL (the per_token path never reads
unit_usd_per_unit, so NULL is safe).  All existing usage_records rows carry
pricing_unit='per_token' and quantity=NULL (token counts live in prompt_tokens /
completion_tokens; quantity=NULL encodes the per_token path for backward compat).

The explicit UPDATE after ADD COLUMN is belt-and-suspenders for rows inserted concurrently
or older PG versions that may not reflect the DEFAULT immediately on reads.

No index added in v7.  downgrade() drops the four columns only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e2f4a8b1d6"
down_revision: str | None = "b7c1d2e3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add pricing_unit + unit_usd_per_unit to pricing_snapshots;
    add pricing_unit + quantity to usage_records.
    """
    # --- pricing_snapshots ---
    op.add_column(
        "pricing_snapshots",
        sa.Column(
            "pricing_unit",
            sa.Text(),
            nullable=False,
            server_default="per_token",
        ),
    )
    op.add_column(
        "pricing_snapshots",
        sa.Column(
            "unit_usd_per_unit",
            sa.Numeric(20, 10),
            nullable=True,
        ),
    )
    # Belt-and-suspenders backfill: explicitly set per_token on all existing rows.
    op.execute("UPDATE pricing_snapshots SET pricing_unit = 'per_token' WHERE pricing_unit IS NULL")

    # --- usage_records ---
    op.add_column(
        "usage_records",
        sa.Column(
            "pricing_unit",
            sa.Text(),
            nullable=False,
            server_default="per_token",
        ),
    )
    op.add_column(
        "usage_records",
        sa.Column(
            "quantity",
            sa.Numeric(18, 6),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop pricing_unit + quantity from usage_records;
    drop unit_usd_per_unit + pricing_unit from pricing_snapshots.

    Safe: these columns are additive-only; no existing data depends on them at
    migration time.  Drop in reverse ADD order within each table.
    """
    # usage_records — drop quantity first (added last), then pricing_unit
    op.drop_column("usage_records", "quantity")
    op.drop_column("usage_records", "pricing_unit")

    # pricing_snapshots — drop unit_usd_per_unit first, then pricing_unit
    op.drop_column("pricing_snapshots", "unit_usd_per_unit")
    op.drop_column("pricing_snapshots", "pricing_unit")
