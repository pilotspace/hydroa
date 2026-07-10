"""gpt_realtime_audio_columns — add dual-stream (audio) price/count columns to
pricing_snapshots and usage_records.

Revision ID: a4c6e8b0d2f3
Revises: c2e4a6f8b0d3
Create Date: 2026-07-01

Additive migration (gpt-realtime-schema-migration TASK.md §3 DDL):

  pricing_snapshots:
    ADD COLUMN audio_prompt_usd_per_token     NUMERIC(20,10) NULL;
    ADD COLUMN audio_completion_usd_per_token NUMERIC(20,10) NULL;
    ADD COLUMN audio_cached_usd_per_token     NUMERIC(20,10) NULL;
  usage_records:
    ADD COLUMN audio_prompt_tokens      INTEGER NOT NULL DEFAULT 0;
    ADD COLUMN audio_completion_tokens  INTEGER NOT NULL DEFAULT 0;
    ADD COLUMN audio_cached_tokens      INTEGER NOT NULL DEFAULT 0;

Mirrors f3c8d1a6b9e4 (tiered_token_billing): the 3 price columns are NULLABLE with
no default — an existing/single-stream snapshot has NULL audio prices, so nothing
reads them until gpt-realtime-relay-billing lands. The 3 usage_records columns are
NOT NULL DEFAULT 0; PostgreSQL applies the server_default instantly for new NOT
NULL columns added with a DEFAULT — no full-table rewrite on PG 11+. All existing
usage_records rows carry audio_prompt_tokens=audio_completion_tokens=
audio_cached_tokens=0.

downgrade() drops the six columns in reverse ADD order within each table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c6e8b0d2f3"
down_revision: str | None = "c2e4a6f8b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the audio-price columns to pricing_snapshots and the audio-count
    columns to usage_records.
    """
    # --- pricing_snapshots: nullable audio-stream prices ---
    op.add_column(
        "pricing_snapshots",
        sa.Column("audio_prompt_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )
    op.add_column(
        "pricing_snapshots",
        sa.Column("audio_completion_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )
    op.add_column(
        "pricing_snapshots",
        sa.Column("audio_cached_usd_per_token", sa.Numeric(20, 10), nullable=True),
    )

    # --- usage_records: audio-stream token counts (NOT NULL DEFAULT 0) ---
    op.add_column(
        "usage_records",
        sa.Column("audio_prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_records",
        sa.Column("audio_completion_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "usage_records",
        sa.Column("audio_cached_tokens", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Drop the six additive columns in reverse ADD order within each table.

    Safe: additive-only; no existing data depends on them at migration time.
    """
    # usage_records — drop in reverse ADD order
    op.drop_column("usage_records", "audio_cached_tokens")
    op.drop_column("usage_records", "audio_completion_tokens")
    op.drop_column("usage_records", "audio_prompt_tokens")

    # pricing_snapshots — drop in reverse ADD order
    op.drop_column("pricing_snapshots", "audio_cached_usd_per_token")
    op.drop_column("pricing_snapshots", "audio_completion_usd_per_token")
    op.drop_column("pricing_snapshots", "audio_prompt_usd_per_token")
