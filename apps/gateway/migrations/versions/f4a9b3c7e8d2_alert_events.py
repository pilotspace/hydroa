"""alert_events — minimal alert event table for soft-budget crossing and health-alerting.

Revision ID: f4a9b3c7e8d2
Revises: c3f8a2e1d5b7
Create Date: 2026-06-11

Creates the alert_events table (minimal DDL owned by spend-windows task):
  - id UUID PK
  - tenant_id UUID NOT NULL FK → tenants.id ON DELETE CASCADE
  - key_id UUID NULL (some events are tenant-level)
  - event_type TEXT NOT NULL
  - payload JSONB NOT NULL
  - created_at TIMESTAMPTZ NOT NULL DEFAULT now()
  - delivered_at TIMESTAMPTZ NULL  (health-alerting sets this)
  - dedupe_key TEXT NOT NULL UNIQUE

Partial index for efficient undelivered query (health-alerting uses this):
  CREATE INDEX alert_events_undelivered_idx ON alert_events (created_at)
    WHERE delivered_at IS NULL;

Health-alerting task extends this table with webhook_url, retry_count, etc. as additive columns.
CROSS-TASK FREEZE: dedupe_key UNIQUE + payload JSONB schema for soft_budget_exceeded are FROZEN.

Downgrade: drops partial index then table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f4a9b3c7e8d2"
down_revision: str | None = "c3f8a2e1d5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create alert_events table with UNIQUE dedupe_key and partial index."""
    op.create_table(
        "alert_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False, unique=True),
    )
    # Partial index for efficient undelivered query — health-alerting uses this
    op.create_index(
        "alert_events_undelivered_idx",
        "alert_events",
        ["created_at"],
        postgresql_where=sa.text("delivered_at IS NULL"),
    )


def downgrade() -> None:
    """Drop partial index and alert_events table."""
    op.drop_index("alert_events_undelivered_idx", table_name="alert_events")
    op.drop_table("alert_events")
