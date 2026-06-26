"""conversations — tenant-scoped conversation and message store.

Revision ID: c4d6e8f0a2b4
Revises: b3d5f7a9c1e4
Create Date: 2026-06-26

Adds two tables:
  conversations(id, tenant_id, key_id, title, created_at, updated_at, deleted_at)
  conversation_messages(id, conversation_id, role, content, created_at)

Indexes (also declared in ORM __table_args__ per v30 lesson):
  ix_conversations_tenant_updated   on conversations(tenant_id, updated_at DESC)
  ix_conversation_messages_conv_created on conversation_messages(conversation_id, created_at)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d6e8f0a2b4"
down_revision = "b3d5f7a9c1e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_tenant_updated",
        "conversations",
        ["tenant_id", sa.text("updated_at DESC")],
    )

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_messages_conv_created",
        "conversation_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_messages_conv_created", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversations_tenant_updated", table_name="conversations")
    op.drop_table("conversations")
