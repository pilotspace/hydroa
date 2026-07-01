"""catalog_input_modalities — add input_modalities column to models table.

Revision ID: c2e4a6f8b0d3
Revises: e2f4a6b8c0d1
Create Date: 2026-06-30

Additive migration (model-input-capabilities TASK.md §3 DDL):
  ALTER TABLE models ADD COLUMN input_modalities TEXT NOT NULL DEFAULT 'text';
  UPDATE models SET input_modalities = 'audio' WHERE modality = 'audio_stt';

PostgreSQL applies the server_default instantly for new NOT NULL columns added
with a DEFAULT — no full-table rewrite is needed (fast DDL on PG 11+).
Every pre-existing row immediately has input_modalities='text' (the conservative
chat/embedding/image/audio_tts default).  The one-line UPDATE then re-derives
the correct value for audio_stt rows (audio), which is the only non-text default.

Properties:
  - Instant DDL on PG 11+ (no full-table rewrite for the ADD COLUMN).
  - The UPDATE is O(N) on audio_stt rows only (expected to be a tiny fraction
    of the catalog; no index needed for this one-time migration).
  - No pricing_snapshots change.
  - Both statements run in a single implicit transaction (Alembic default).
  - Downgrade drops the column only (safe — no other table references it).

Contract reference: model-input-capabilities TASK.md §3, frozen @ v1.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2e4a6f8b0d3"
down_revision: str | None = "e2f4a6b8c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add input_modalities column to the models table.

    Step 1: ADD COLUMN with server_default 'text' — all existing rows get 'text'.
    Step 2: UPDATE audio_stt rows to 'audio' — the only non-text default.
    """
    op.add_column(
        "models",
        sa.Column(
            "input_modalities",
            sa.Text(),
            nullable=False,
            server_default="text",
        ),
    )
    # Re-derive the correct value for audio_stt rows.
    # chat / embedding / image / audio_tts all remain 'text' (already correct).
    op.execute("UPDATE models SET input_modalities = 'audio' WHERE modality = 'audio_stt'")


def downgrade() -> None:
    """Drop input_modalities column from the models table.

    Safe: the column is additive-only; no other table or migration references it.
    """
    op.drop_column("models", "input_modalities")
