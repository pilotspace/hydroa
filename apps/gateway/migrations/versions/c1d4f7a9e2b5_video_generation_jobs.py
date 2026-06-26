"""video_generation_jobs — async video generation job lifecycle.

Revision ID: c1d4f7a9e2b5
Revises: b3e5f9a7c1d4
Create Date: 2026-06-26

Adds one table:
  video_generation_jobs(id, tenant_id, key_id, status, model, prompt,
                        params, result_artifact_id, error, created_at, updated_at)

Indexes (also declared in ORM __table_args__ per v30 lesson):
  ix_video_jobs_tenant_created on video_generation_jobs(tenant_id, created_at DESC)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c1d4f7a9e2b5"
down_revision = "b3e5f9a7c1d4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "video_generation_jobs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'queued'")),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "result_artifact_id",
            sa.UUID(),
            nullable=True,
        ),
        sa.Column("error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_video_jobs_tenant_created",
        "video_generation_jobs",
        ["tenant_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_video_jobs_tenant_created", table_name="video_generation_jobs")
    op.drop_table("video_generation_jobs")
