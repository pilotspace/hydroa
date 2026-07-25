"""finetune_jobs + finetune_job_events tables — finetune-broker.

Revision ID: 6f2a9c1e3b7d
Revises: 55dc3f920a38
Create Date: 2026-07-24

finetune-broker PLAN.md §3 (FROZEN @ v1). Additive:

  - NEW TABLE finetune_jobs — tenant-scoped OpenAI-wire fine-tuning job (mirrors
    batch_jobs shape). NO credential-bearing column (T4 threat model).
  - NEW TABLE finetune_job_events — one row per lifecycle event, FK CASCADE from
    finetune_jobs.

Indexes (also declared in the ORM __table_args__ per the v30 lesson):
  ix_finetune_jobs_tenant_created        on finetune_jobs(tenant_id, created_at DESC)
  ix_finetune_job_events_job_created     on finetune_job_events(job_id, created_at)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "6f2a9c1e3b7d"
down_revision = "55dc3f920a38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "finetune_jobs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_job_id", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("training_file_id", sa.UUID(), nullable=False),
        sa.Column("validation_file_id", sa.UUID(), nullable=True),
        sa.Column("suffix", sa.Text(), nullable=True),
        sa.Column("hyperparameters", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'validating_files'")
        ),
        sa.Column("fine_tuned_model", sa.Text(), nullable=True),
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
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_finetune_jobs_tenant_created",
        "finetune_jobs",
        ["tenant_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "finetune_job_events",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("level", sa.Text(), nullable=False, server_default=sa.text("'info'")),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["finetune_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_finetune_job_events_job_created", "finetune_job_events", ["job_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_finetune_job_events_job_created", table_name="finetune_job_events")
    op.drop_table("finetune_job_events")
    op.drop_index("ix_finetune_jobs_tenant_created", table_name="finetune_jobs")
    op.drop_table("finetune_jobs")
