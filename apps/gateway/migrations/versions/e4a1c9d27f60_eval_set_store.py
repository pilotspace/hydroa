"""eval sets + cases substrate — eval-set-store (R7 evals-regression-gate).

Revision ID: e4a1c9d27f60
Revises: c7f1a4e83b92
Create Date: 2026-08-12

eval-set-store PLAN.md §3 (FROZEN @ v1). Additive, two tenant-scoped tables:

  - NEW TABLE eval_sets — tenant-owned metadata (name + optional description). NO
    payload. UNIQUE (tenant_id, name) so a duplicate name is refused, never a silent
    second set. A ZDR tenant MAY create one (the set is payload-free; M7).
  - NEW TABLE eval_cases — the payload-bearing surface: request_body (the OpenAI-shaped
    request to replay) + a deterministic assertion, both JSONB. Append-only. FK ->
    eval_sets ON DELETE CASCADE. The ZDR gate (M3) lives in the write path, not the schema.

Indexes (also declared in the ORM __table_args__ per the v30 two-manifest lesson):
  ix_eval_sets_tenant_created   on eval_sets(tenant_id, created_at DESC)
  ix_eval_cases_set_created     on eval_cases(tenant_id, eval_set_id, created_at)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e4a1c9d27f60"
down_revision = "c7f1a4e83b92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_sets",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_eval_sets_tenant_name"),
    )
    op.create_index(
        "ix_eval_sets_tenant_created",
        "eval_sets",
        ["tenant_id", sa.text("created_at DESC")],
    )

    op.create_table(
        "eval_cases",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("eval_set_id", sa.UUID(), nullable=False),
        sa.Column("request_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("assertion", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["eval_set_id"], ["eval_sets.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_eval_cases_set_created",
        "eval_cases",
        ["tenant_id", "eval_set_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_cases_set_created", table_name="eval_cases")
    op.drop_table("eval_cases")
    op.drop_index("ix_eval_sets_tenant_created", table_name="eval_sets")
    op.drop_table("eval_sets")
