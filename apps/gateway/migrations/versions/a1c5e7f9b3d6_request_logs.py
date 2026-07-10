"""request_logs — opt-in, PII-scrubbed request/response payload capture store.

Revision ID: a1c5e7f9b3d6
Revises: 511ad8a7b65e
Create Date: 2026-07-10

payload-capture-store TASK.md §3 (FROZEN @ v1). Creates the request_logs table plus
the two per-tenant/per-key opt-in toggle columns:

  CREATE TABLE request_logs (
      id                 UUID PRIMARY KEY
      tenant_id          UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
      key_id             UUID NOT NULL              -- no FK (append-only-ledger,
                                                      -- mirrors usage_records)
      team_id            UUID NULL                  -- no FK (team deletion must not cascade)
      model_id           TEXT NOT NULL
      status_code        INTEGER NOT NULL
      stream             BOOLEAN NOT NULL DEFAULT false
      cached             BOOLEAN NOT NULL DEFAULT false
      request_body       JSONB NULL                 -- null when metadata-only
      response_body      JSONB NULL                 -- null when metadata-only, or never
                                                      -- reached upstream
      guardrail_verdict  JSONB NULL                 -- reserved, unpopulated in v1
      scrub_status       TEXT NOT NULL DEFAULT 'scrubbed'
      truncated          BOOLEAN NOT NULL DEFAULT false
      cost_usd           NUMERIC(14,8) NULL         -- denormalized display snapshot only,
                                                      -- never billing truth
      created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX ix_request_logs_tenant_created ON request_logs (tenant_id, created_at);
  CREATE INDEX ix_request_logs_created_at ON request_logs (created_at);
  CREATE INDEX ix_request_logs_tenant_key ON request_logs (tenant_id, key_id);

  ALTER TABLE tenants ADD COLUMN payload_capture_enabled BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE api_keys ADD COLUMN capture_enabled BOOLEAN NOT NULL DEFAULT false;

Downgrade: drops the two additive columns, then the three indexes, then the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1c5e7f9b3d6"
down_revision: str | None = "511ad8a7b65e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create request_logs + its 3 indexes; add the two opt-in toggle columns."""
    op.create_table(
        "request_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("stream", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("guardrail_verdict", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "scrub_status", sa.Text(), nullable=False, server_default=sa.text("'scrubbed'")
        ),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cost_usd", sa.Numeric(14, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_request_logs_tenant_created", "request_logs", ["tenant_id", "created_at"]
    )
    op.create_index("ix_request_logs_created_at", "request_logs", ["created_at"])
    op.create_index("ix_request_logs_tenant_key", "request_logs", ["tenant_id", "key_id"])

    op.add_column(
        "tenants",
        sa.Column(
            "payload_capture_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "capture_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    """Drop the two additive columns, the 3 indexes, then request_logs."""
    op.drop_column("api_keys", "capture_enabled")
    op.drop_column("tenants", "payload_capture_enabled")
    op.drop_index("ix_request_logs_tenant_key", table_name="request_logs")
    op.drop_index("ix_request_logs_created_at", table_name="request_logs")
    op.drop_index("ix_request_logs_tenant_created", table_name="request_logs")
    op.drop_table("request_logs")
