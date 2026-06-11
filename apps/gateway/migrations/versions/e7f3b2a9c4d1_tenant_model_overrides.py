"""tenant_model_overrides — per-tenant model enable/disable override table.

Revision ID: e7f3b2a9c4d1
Revises: a1b2c3d4e5f6
Create Date: 2026-06-11

Additive migration (model-mgmt TASK.md §3 DDL):
  CREATE TABLE tenant_model_overrides (
    tenant_id   UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    model_id    TEXT        NOT NULL REFERENCES models(id)  ON DELETE CASCADE,
    enabled     BOOLEAN     NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, model_id)
  );

No extra index needed: PK (tenant_id, model_id) serves all queries.

Downgrade: DROP TABLE tenant_model_overrides (safe — additive, no existing rows reference it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f3b2a9c4d1"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_model_overrides table with composite PK and FK cascades."""
    op.create_table(
        "tenant_model_overrides",
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "model_id"),
    )


def downgrade() -> None:
    """Drop tenant_model_overrides table (safe — additive, no existing rows reference it)."""
    op.drop_table("tenant_model_overrides")
