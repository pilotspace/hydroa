"""catalog_modality_provider — add modality and provider columns to models table.

Revision ID: b7c1d2e3f4a5
Revises: a9b3c4d5e6f7
Create Date: 2026-06-12

Additive migration (provider-seam TASK.md §3 DDL):
  ALTER TABLE models ADD COLUMN modality TEXT NOT NULL DEFAULT 'chat';
  ALTER TABLE models ADD COLUMN provider TEXT NOT NULL DEFAULT 'openrouter';

PostgreSQL applies the server_default instantly for new NOT NULL columns added
with a DEFAULT — no full-table rewrite is needed (fast DDL on PG 11+).
Every pre-existing row immediately has modality='chat', provider='openrouter'
which preserves v6-identical behavior for all existing catalog rows.

No index is added in v7.  A partial index on (modality, provider, active) is
a follow-up optimization (catalog table is small; full scans are acceptable).

No data migration.  Downgrade drops the two columns only (safe — no data depends
on these columns at migration time).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7c1d2e3f4a5"
down_revision: str | None = "a9b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add modality and provider columns to the models table with server defaults."""
    op.add_column(
        "models",
        sa.Column(
            "modality",
            sa.Text(),
            nullable=False,
            server_default="chat",
        ),
    )
    op.add_column(
        "models",
        sa.Column(
            "provider",
            sa.Text(),
            nullable=False,
            server_default="openrouter",
        ),
    )


def downgrade() -> None:
    """Drop modality and provider columns from the models table.

    Safe: these columns are additive-only; no existing data depends on them
    at migration time.  Drop provider first (added last).
    """
    op.drop_column("models", "provider")
    op.drop_column("models", "modality")
