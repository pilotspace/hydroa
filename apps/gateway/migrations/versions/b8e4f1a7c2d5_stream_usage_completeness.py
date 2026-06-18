"""stream_usage_completeness — stamp every usage_records row with the provenance
of its billed usage so a stream whose terminal usage frame was missing/partial is
never a SILENT $0.

Revision ID: b8e4f1a7c2d5
Revises: a7d2e9c4f1b6
Create Date: 2026-06-18

Additive migration (stream-usage-completeness TASK.md §3 DDL):

  usage_records:
    ADD COLUMN usage_source TEXT NOT NULL DEFAULT 'frame';

usage_source is NOT NULL DEFAULT 'frame' — every pre-existing row, and every
non-stream caller (completions / embeddings / images / audio / cache hits), is a
real 'frame' usage source, so the default reads true historically; PostgreSQL
applies the server_default instantly for a new NOT NULL column with a DEFAULT
(no full-table rewrite on PG 11+). A streaming chat record whose upstream omitted
or partialed the terminal usage frame is written as 'stream_fallback'.

downgrade() drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f1a7c2d5"
down_revision: str | None = "a7d2e9c4f1b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add usage_source to usage_records (additive, instant on PG 11+)."""
    op.add_column(
        "usage_records",
        sa.Column("usage_source", sa.Text(), nullable=False, server_default="frame"),
    )


def downgrade() -> None:
    """Drop the additive column.

    Safe: additive-only; no existing data depends on it at migration time.
    """
    op.drop_column("usage_records", "usage_source")
