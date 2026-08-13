"""eval baseline pin — baseline-and-verdict (R7 evals-regression-gate).

Revision ID: a3f9c7e21b84
Revises: f5b2d8c41a37
Create Date: 2026-08-13

baseline-and-verdict PLAN.md §3 (FROZEN @ sha256:b3b0c7c9). Additive, one tenant-scoped table
that extends the eval-run-executor substrate:

  - NEW TABLE eval_baselines — the ONE-baseline-per-set pin: which run is the reference a
    candidate run is verdicted against (M3). ``UNIQUE(eval_set_id)`` structurally enforces one
    baseline per set — a re-pin is an UPDATE, never a second row. ``pinned_at`` is the
    auditable moment the pin moved (SOC 2). FK -> eval_sets AND eval_runs, both CASCADE.
    The pin is DURABLE (M6): a verdict is reproducible across a redeploy.

Four-manifest rule (see [[gateway-new-table-four-manifests]]): this migration + EXPECTED_TABLES
(tests/migrations) + migrations/env.py's own ORM import + the guardrails NOT-IN allow-list.

Indexes (also declared in the ORM __table_args__ per the v30 two-manifest lesson):
  uq_eval_baselines_set          UNIQUE on eval_baselines(eval_set_id)
  ix_eval_baselines_tenant_set   on eval_baselines(tenant_id, eval_set_id)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a3f9c7e21b84"
down_revision = "f5b2d8c41a37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_baselines",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("eval_set_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column(
            "pinned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["eval_set_id"], ["eval_sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("eval_set_id", name="uq_eval_baselines_set"),
    )
    op.create_index(
        "ix_eval_baselines_tenant_set",
        "eval_baselines",
        ["tenant_id", "eval_set_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_baselines_tenant_set", table_name="eval_baselines")
    op.drop_table("eval_baselines")
