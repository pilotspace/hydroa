"""files table + batch_jobs.input_file_id — OpenAI-wire /v1/files uploads.

Revision ID: f9d3b1a7c2e4
Revises: b8c1f4a2d6e9
Create Date: 2026-07-24

files-uploads-api PLAN.md §3 (FROZEN @ v1). Additive:

  - NEW TABLE files(id, tenant_id, key_id, filename, purpose, bytes, content_type,
    status, storage_backend, object_key, content, created_at, deleted_at) — tenant-scoped
    user-upload store with inline-BYTEA / s3 storage_backend dispatch + soft-delete,
    mirroring the artifacts table.
  - ALTER batch_jobs ADD input_file_id UUID NULL — additive, nullable; the files row a
    purpose='batch' upload drove a job from (NULL for every existing line_items job).
    NOT a hard cross-module FK (validated in the router against the caller-owned files
    row); NULL default keeps every existing batch_jobs row byte-identical.

Index (also declared in the ORM __table_args__ per the v30 lesson):
  ix_files_tenant_created on files(tenant_id, created_at DESC)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f9d3b1a7c2e4"
down_revision = "b1d05565455e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "files",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'processed'")),
        sa.Column("storage_backend", sa.Text(), nullable=False, server_default=sa.text("'inline'")),
        sa.Column("object_key", sa.Text(), nullable=True),
        sa.Column("content", sa.LargeBinary(), nullable=True),
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
        "ix_files_tenant_created",
        "files",
        ["tenant_id", sa.text("created_at DESC")],
    )

    op.add_column(
        "batch_jobs",
        sa.Column("input_file_id", sa.UUID(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("batch_jobs", "input_file_id")
    op.drop_index("ix_files_tenant_created", table_name="files")
    op.drop_table("files")
