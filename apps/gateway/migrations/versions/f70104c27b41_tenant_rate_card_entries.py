"""tenant_rate_card_entries — per-(tenant, model) markup override table.

Revision ID: f70104c27b41
Revises: c2e4a6f8b0d3
Create Date: 2026-07-02

Additive migration (tiered-rate-cards TASK.md §3 DDL — FROZEN @ v1):
  CREATE TABLE tenant_rate_card_entries (
    id          UUID          PRIMARY KEY,
    tenant_id   UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    model_id    TEXT          NOT NULL,
    markup_pct  NUMERIC(7,4)  NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, model_id),
    CHECK (markup_pct >= 0)
  );
  CREATE INDEX ix_tenant_rate_card_entries_tenant_id ON tenant_rate_card_entries (tenant_id);

No FK to models(id) — a markup override may target a model_id absent from the
catalog (it simply resolves once the model is billed/listed; TASK.md §1
assumption, deliberately NOT rejected as unknown_model).

No server_default / no backfill: an ABSENT row means "fall back to
tenants.markup_pct" — the resolve rule lives in
gateway.usage.application.rate_card_resolver.resolve_markup_pct.

Downgrade: DROP TABLE tenant_rate_card_entries (safe — additive, no existing
rows reference it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f70104c27b41"
down_revision: str | None = "fef4716b6e33"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_rate_card_entries table with UNIQUE(tenant_id, model_id)."""
    op.create_table(
        "tenant_rate_card_entries",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("markup_pct", sa.Numeric(7, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "model_id", name="uq_tenant_rate_card_entries_tenant_model"
        ),
        sa.CheckConstraint("markup_pct >= 0", name="ck_tenant_rate_card_entries_markup_pct_nonneg"),
    )
    op.create_index(
        "ix_tenant_rate_card_entries_tenant_id",
        "tenant_rate_card_entries",
        ["tenant_id"],
    )


def downgrade() -> None:
    """Drop tenant_rate_card_entries table (safe — additive, no existing rows reference it)."""
    op.drop_index("ix_tenant_rate_card_entries_tenant_id", table_name="tenant_rate_card_entries")
    op.drop_table("tenant_rate_card_entries")
