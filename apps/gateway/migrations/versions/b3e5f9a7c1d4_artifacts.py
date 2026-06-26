"""artifacts — tenant-scoped file store.

Revision ID: b3e5f9a7c1d4
Revises: d8f0a2b4c6e8
Create Date: 2026-06-26

Adds one table:
  artifacts(id, tenant_id, key_id, name, content_type, size_bytes, content,
            created_at, deleted_at)

Indexes (also declared in ORM __table_args__ per v30 lesson):
  ix_artifacts_tenant_created on artifacts(tenant_id, created_at DESC)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b3e5f9a7c1d4"
down_revision = "d8f0a2b4c6e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_artifacts_tenant_created",
        "artifacts",
        ["tenant_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_artifacts_tenant_created", table_name="artifacts")
    op.drop_table("artifacts")
