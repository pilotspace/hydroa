"""models.tenant_id — tenant-owned catalog rows (finetune-model-registry PLAN.md §3).

Revision ID: b3d8f21ca9e6
Revises: 6f2a9c1e3b7d
Create Date: 2026-07-24

Additive migration (finetune-model-registry PLAN.md §3 Schema, FROZEN @ v1):
  ALTER TABLE models ADD COLUMN tenant_id UUID NULL REFERENCES tenants(id) ON DELETE CASCADE
    -- NULL = global row (every pre-existing row keeps this default; byte-identical).
  + partial index ix_models_tenant ON models(tenant_id) WHERE tenant_id IS NOT NULL.

No new table -> NO EXPECTED_TABLES / guardrails-manifest change (both are table-level).

Downgrade: drop the partial index then the column (safe — additive-only, no existing
row ever has tenant_id set before this migration).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3d8f21ca9e6"
down_revision: str | None = "6f2a9c1e3b7d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_models_tenant_id",
        "models",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_models_tenant",
        "models",
        ["tenant_id"],
        unique=False,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_models_tenant", table_name="models")
    op.drop_constraint("fk_models_tenant_id", "models", type_="foreignkey")
    op.drop_column("models", "tenant_id")
