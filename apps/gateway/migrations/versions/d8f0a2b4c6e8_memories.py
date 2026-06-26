"""memories — tenant-scoped semantic memory store.

Revision ID: d8f0a2b4c6e8
Revises: c4d6e8f0a2b4
Create Date: 2026-06-26

Adds one table:
  memories(id, tenant_id, key_id, content, embedding, metadata, created_at, deleted_at)

Indexes (also declared in ORM __table_args__ per v30 lesson):
  ix_memories_tenant_created on memories(tenant_id, created_at DESC)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8f0a2b4c6e8"
down_revision = "c4d6e8f0a2b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        # DOUBLE PRECISION[] — stores float8 array; NULL when embedding is unavailable
        sa.Column("embedding", postgresql.ARRAY(sa.Float(precision=53)), nullable=True),
        # "metadata" column stores arbitrary JSON per memory
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
        "ix_memories_tenant_created",
        "memories",
        ["tenant_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_memories_tenant_created", table_name="memories")
    op.drop_table("memories")
