"""tenant_model_presets — per-tenant model-preset selector store.

Revision ID: b5f8a1d4c7e0
Revises: c2e4a6f8b0d3
Create Date: 2026-07-01

Additive migration (tenant-preset-store TASK.md §3 DDL):

New table:
  CREATE TABLE tenant_model_presets (
    tenant_id    UUID        NOT NULL REFERENCES tenants.id ON DELETE CASCADE,
    preset_name  TEXT        NOT NULL,
    alias_key    TEXT        NOT NULL,
    target_model TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, preset_name, alias_key)
  );

A tenant may map a stable (preset_name, alias_key) selector pair to a
concrete catalog model id (target_model). Re-upserting the same selector
repoints the target in place — no duplicate rows, one row per selector pair
per tenant (enforced by the composite primary key).

Migration chain: c2e4a6f8b0d3 (catalog-input-modalities) → b5f8a1d4c7e0
(tenant-model-presets). Single current head at the time this migration was
authored — confirmed via `alembic heads` before writing this file.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5f8a1d4c7e0"
down_revision: str | None = "c2e4a6f8b0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_model_presets table."""
    op.create_table(
        "tenant_model_presets",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("preset_name", sa.TEXT(), nullable=False),
        sa.Column("alias_key", sa.TEXT(), nullable=False),
        sa.Column("target_model", sa.TEXT(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "preset_name", "alias_key"),
    )


def downgrade() -> None:
    """Drop tenant_model_presets table.

    Safe: additive-only; no existing data depends on this table at migration time.
    """
    op.drop_table("tenant_model_presets")
