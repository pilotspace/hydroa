"""key_governance_fields — additive per-key governance columns.

Revision ID: b1e3f7c9d2a4
Revises: ad14442336db
Create Date: 2026-06-11

Adds 5 new nullable columns to api_keys (no backfill — existing rows keep NULL):
  monthly_budget_usd  NUMERIC(12,2) NULL
  soft_budget_usd     NUMERIC(12,2) NULL  CHECK soft <= hard when both non-null
  expires_at          TIMESTAMPTZ   NULL
  model_allowlist     JSONB         NULL   (array of text, or null = all models)
  rotated_from_key_id UUID          NULL   FK self-ref → api_keys(id) ON DELETE SET NULL

Downgrade: drops the 5 columns and the partial CHECK constraint.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b1e3f7c9d2a4"
down_revision: str | None = "ad14442336db"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add governance columns to api_keys (additive — no backfill)."""
    op.add_column(
        "api_keys",
        sa.Column("monthly_budget_usd", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("soft_budget_usd", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "model_allowlist",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "rotated_from_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    # Self-referential FK: rotated_from_key_id → api_keys(id) ON DELETE SET NULL
    op.create_foreign_key(
        "api_keys_rotated_from_key_id_fkey",
        "api_keys",
        "api_keys",
        ["rotated_from_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # Partial check: soft_budget_usd <= monthly_budget_usd when both non-null
    op.create_check_constraint(
        "api_keys_soft_lte_hard_check",
        "api_keys",
        "soft_budget_usd IS NULL OR monthly_budget_usd IS NULL"
        " OR soft_budget_usd <= monthly_budget_usd",
    )


def downgrade() -> None:
    """Drop governance columns from api_keys (drops constraint + FK first)."""
    op.drop_constraint("api_keys_soft_lte_hard_check", "api_keys", type_="check")
    op.drop_constraint("api_keys_rotated_from_key_id_fkey", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "rotated_from_key_id")
    op.drop_column("api_keys", "model_allowlist")
    op.drop_column("api_keys", "expires_at")
    op.drop_column("api_keys", "soft_budget_usd")
    op.drop_column("api_keys", "monthly_budget_usd")
