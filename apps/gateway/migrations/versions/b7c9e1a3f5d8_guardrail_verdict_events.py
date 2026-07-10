"""guardrail_verdict_events — append-only ledger for guardrail-verdict analytics.

Revision ID: b7c9e1a3f5d8
Revises: a1c5e7f9b3d6
Create Date: 2026-07-10

Creates the guardrail_verdict_events table (guardrail-analytics TASK.md §3, FROZEN @ v1):
  - id UUID PK (uuid7, generated application-side — mirrors request_logs/usage_records)
  - tenant_id UUID NOT NULL FK -> tenants.id ON DELETE RESTRICT
  - key_id UUID NOT NULL, no FK (append-only-ledger pattern, mirrors usage_records.key_id)
  - team_id UUID NULL, no FK (team deletion must not cascade, mirrors usage_records.team_id)
  - guardrail TEXT NOT NULL ("prompt_injection"|"pii_mask"|"ml_moderation"|"error")
  - action TEXT NOT NULL ("blocked"|"masked"|"audited"|"passed"|"error"|"unchecked"|
    "budget_exceeded")
  - policy_source TEXT NOT NULL ("key"|"tenant"|"none")
  - created_at TIMESTAMPTZ NOT NULL DEFAULT now()

Three indexes sized for the actual query shapes (window scan, per-guardrail breakdown,
per-key breakdown); the policy_source breakdown reuses the (tenant_id, created_at) index
(low cardinality — 3 values, no dedicated index needed).

NOT payload-bearing (no request/response body columns) — lower sensitivity than
request_logs; independent of payload-capture opt-in (guardrails evaluate on 100% of
governed traffic).

Downgrade: drops the three indexes then the table (safe — new table, no pre-migration
code references it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7c9e1a3f5d8"
down_revision: str | None = "b3d8e1f4a7c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create guardrail_verdict_events table with its three query-shape indexes."""
    op.create_table(
        "guardrail_verdict_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("guardrail", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("policy_source", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_guardrail_verdict_tenant_created",
        "guardrail_verdict_events",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_guardrail_verdict_tenant_guardrail_created",
        "guardrail_verdict_events",
        ["tenant_id", "guardrail", "created_at"],
    )
    op.create_index(
        "ix_guardrail_verdict_tenant_key_created",
        "guardrail_verdict_events",
        ["tenant_id", "key_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the three indexes and the guardrail_verdict_events table."""
    op.drop_index("ix_guardrail_verdict_tenant_key_created", table_name="guardrail_verdict_events")
    op.drop_index(
        "ix_guardrail_verdict_tenant_guardrail_created", table_name="guardrail_verdict_events"
    )
    op.drop_index("ix_guardrail_verdict_tenant_created", table_name="guardrail_verdict_events")
    op.drop_table("guardrail_verdict_events")
