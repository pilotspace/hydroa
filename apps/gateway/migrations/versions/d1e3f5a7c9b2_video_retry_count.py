"""Add retry_count column to video_generation_jobs.

Revision ID: d1e3f5a7c9b2
Revises: c1d4f7a9e2b5
Create Date: 2026-06-26

Required for the v48 durable-queue delta: the worker increments retry_count before
each attempt so it can enforce max_retries and avoid infinite re-enqueue of a
poison job. Server default 0 means existing rows are backfilled to 0 at-rest —
no data migration required. NOT NULL so the column is always present.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d1e3f5a7c9b2"
down_revision = "c1d4f7a9e2b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_generation_jobs",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_column("video_generation_jobs", "retry_count")
