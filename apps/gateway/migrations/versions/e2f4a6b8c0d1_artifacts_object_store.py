"""Add object-store backend columns to artifacts (v51 artifacts-s3-persistence).

Revision ID: e2f4a6b8c0d1
Revises: d1e3f5a7c9b2
Create Date: 2026-06-26

Additive: routes artifact bytes through the ObjectStore (S3/MinIO) per-row while
keeping back-compat with inline BYTEA storage.

  - storage_backend TEXT NOT NULL DEFAULT 'inline' — existing rows backfill to
    'inline' at-rest (no data migration); 's3' rows keep their bytes in the store.
  - object_key TEXT NULL — 'artifacts/{tenant_id}/{id}' when storage_backend='s3'.
  - content BYTEA → NULLABLE — NULL for 's3' rows (bytes live in the store).

Downgrade re-tightens content to NOT NULL; safe on an empty/inline-only DB.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e2f4a6b8c0d1"
down_revision = "d1e3f5a7c9b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column(
            "storage_backend",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'inline'"),
        ),
    )
    op.add_column(
        "artifacts",
        sa.Column("object_key", sa.Text(), nullable=True),
    )
    op.alter_column("artifacts", "content", existing_type=sa.LargeBinary(), nullable=True)


def downgrade() -> None:
    op.alter_column("artifacts", "content", existing_type=sa.LargeBinary(), nullable=False)
    op.drop_column("artifacts", "object_key")
    op.drop_column("artifacts", "storage_backend")
