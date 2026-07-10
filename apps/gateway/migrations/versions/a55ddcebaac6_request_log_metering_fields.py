"""request_log_metering_fields — latency + token counts + billing-correlation id.

Revision ID: a55ddcebaac6
Revises: a1c5e7f9b3d6
Create Date: 2026-07-10

request-log-metering-fields TASK.md §3 (FROZEN @ v1) — change-request on top of the
FROZEN payload-capture-store contract. Adds 5 additive NULLABLE columns to request_logs
(no default, no backfill):

  ALTER TABLE request_logs ADD COLUMN request_id        UUID    NULL;
  ALTER TABLE request_logs ADD COLUMN latency_ms         INTEGER NULL;
  ALTER TABLE request_logs ADD COLUMN prompt_tokens      INTEGER NULL;
  ALTER TABLE request_logs ADD COLUMN completion_tokens  INTEGER NULL;
  ALTER TABLE request_logs ADD COLUMN total_tokens       INTEGER NULL;

  CREATE INDEX ix_request_logs_request_id ON request_logs (request_id)
    WHERE request_id IS NOT NULL;

Plus one INDEX-ONLY addition on the FROZEN usage_records table (no column, no schema
change to its column list) supporting the reverse lookup from a request_logs row to its
billing row via the existing `raw` JSONB extras seam:

  CREATE INDEX ix_usage_records_request_id ON usage_records ((raw ->> 'request_id'))
    WHERE raw ->> 'request_id' IS NOT NULL;

Downgrade: drop both indexes, then the 5 request_logs columns (symmetric, reversible;
usage_records itself is never touched beyond the index).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a55ddcebaac6"
down_revision: str | None = "a1c5e7f9b3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the 5 additive NULLABLE request_logs columns + the 2 supporting indexes."""
    op.add_column(
        "request_logs",
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
    )
    op.add_column(
        "request_logs",
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_request_logs_request_id",
        "request_logs",
        ["request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    # Index-only — no column added to the FROZEN usage_records table.
    op.create_index(
        "ix_usage_records_request_id",
        "usage_records",
        [sa.text("(raw ->> 'request_id')")],
        postgresql_where=sa.text("raw ->> 'request_id' IS NOT NULL"),
    )


def downgrade() -> None:
    """Drop both indexes, then the 5 additive request_logs columns."""
    op.drop_index("ix_usage_records_request_id", table_name="usage_records")
    op.drop_index("ix_request_logs_request_id", table_name="request_logs")
    op.drop_column("request_logs", "total_tokens")
    op.drop_column("request_logs", "completion_tokens")
    op.drop_column("request_logs", "prompt_tokens")
    op.drop_column("request_logs", "latency_ms")
    op.drop_column("request_logs", "request_id")
