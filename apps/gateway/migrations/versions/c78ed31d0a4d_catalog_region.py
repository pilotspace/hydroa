"""catalog_region — add region column to models table.

Revision ID: c78ed31d0a4d
Revises: f1ef6b05a732
Create Date: 2026-07-12

Additive migration (region-catalog-dimension TASK.md §3 DDL):
  ALTER TABLE models ADD COLUMN region TEXT NOT NULL DEFAULT 'global';

No backfill UPDATE — unlike catalog_input_modalities (c2e4a6f8b0d3), 'global' is
honestly correct for EVERY existing row: every pre-this-task row was synced from
OpenRouter, which carries no per-model residency guarantee today (TASK.md §0
Issue #6). PostgreSQL applies the server_default instantly for new NOT NULL
columns added with a DEFAULT — no full-table rewrite (fast DDL on PG 11+).

Contract reference: region-catalog-dimension TASK.md §3, frozen @ v1.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c78ed31d0a4d"
down_revision: str | None = "f1ef6b05a732"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add region column to the models table.

    Every existing row gets region='global' via server_default — no differentiated
    backfill needed (honestly correct for 100% of today's catalog).
    """
    op.add_column(
        "models",
        sa.Column(
            "region",
            sa.Text(),
            nullable=False,
            server_default="global",
        ),
    )


def downgrade() -> None:
    """Drop region column from the models table.

    Safe: the column is additive-only; no other table or migration references it.
    """
    op.drop_column("models", "region")
