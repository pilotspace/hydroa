"""batch_jobs + batch_job_items — async batch-discounted chat-completion job store.

Revision ID: e5a7c9b1d3f6
Revises: 326b927cf8c2
Create Date: 2026-07-02

Adds two tables:
  batch_jobs(id, tenant_id, key_id, status, item_count, retry_count, error,
             created_at, updated_at)
  batch_job_items(id, batch_job_id, tenant_id, custom_id, request_body, status,
                   result_body, error, created_at, updated_at)

Indexes (also declared in ORM __table_args__ per v30 lesson):
  ix_batch_jobs_tenant_created on batch_jobs(tenant_id, created_at DESC)
  ix_batch_job_items_job on batch_job_items(batch_job_id)
  uq_batch_job_items_job_custom_id UNIQUE on batch_job_items(batch_job_id, custom_id)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5a7c9b1d3f6"
down_revision = "326b927cf8c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "batch_jobs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'validating'")),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
        "ix_batch_jobs_tenant_created",
        "batch_jobs",
        ["tenant_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "batch_job_items",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("batch_job_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("custom_id", sa.Text(), nullable=False),
        sa.Column("request_body", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("result_body", sa.dialects.postgresql.JSONB(), nullable=True),
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
        sa.ForeignKeyConstraint(["batch_job_id"], ["batch_jobs.id"]),
        sa.UniqueConstraint("batch_job_id", "custom_id", name="uq_batch_job_items_job_custom_id"),
    )
    op.create_index(
        "ix_batch_job_items_job",
        "batch_job_items",
        ["batch_job_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_batch_job_items_job", table_name="batch_job_items")
    op.drop_table("batch_job_items")
    op.drop_index("ix_batch_jobs_tenant_created", table_name="batch_jobs")
    op.drop_table("batch_jobs")
