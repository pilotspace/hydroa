"""eval run + per-case result substrate — eval-run-executor (R7 evals-regression-gate).

Revision ID: f5b2d8c41a37
Revises: e4a1c9d27f60
Create Date: 2026-08-13

eval-run-executor PLAN.md §3 (FROZEN @ sha256:1353206d). Additive, two tenant-scoped tables
that extend the eval-set-store substrate:

  - NEW TABLE eval_runs — one row per launched run. Carries the launching key_id (A1 — a run
    bills that key, exactly as its live traffic) and the named model. ``status`` is DERIVED
    from the run's cases (M7): pending at launch, completed when every snapshot case is
    terminal, blocked iff a ZDR flip refused the run mid-flight (M5). FK -> eval_sets CASCADE.
    ⚠ The raw API key is NEVER persisted (auth-scoped resume, 2026-08-13): only key_id, so a
    cross-process resume must re-supply the raw key via a fresh authenticated request.
  - NEW TABLE eval_case_results — one row per case DRIVEN. ``response_text`` is the model's
    payload-at-rest (the ZDR-gated surface, M5); present only for a `completed` case.
    UNIQUE (eval_run_id, eval_case_id) makes a resumed drive idempotent — a terminal case is
    never re-dialed or re-billed (M7 / R:DOUBLE_BILL). FK -> eval_runs CASCADE.

Four-manifest rule (see [[gateway-new-table-four-manifests]]): this migration + EXPECTED_TABLES
(tests/migrations) + migrations/env.py's own ORM import + the guardrails NOT-IN allow-list.

Indexes (also declared in the ORM __table_args__ per the v30 two-manifest lesson):
  ix_eval_runs_tenant_set_created       on eval_runs(tenant_id, eval_set_id, created_at)
  ix_eval_case_results_run_created      on eval_case_results(tenant_id, eval_run_id, created_at)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f5b2d8c41a37"
down_revision = "e4a1c9d27f60"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("eval_set_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
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
        "ix_eval_runs_tenant_set_created",
        "eval_runs",
        ["tenant_id", "eval_set_id", "created_at"],
    )

    op.create_table(
        "eval_case_results",
        sa.Column("id", sa.UUID(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("eval_run_id", sa.UUID(), nullable=False),
        sa.Column("eval_case_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("usage_record_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["eval_run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "eval_run_id", "eval_case_id", name="uq_eval_case_results_run_case"
        ),
    )
    op.create_index(
        "ix_eval_case_results_run_created",
        "eval_case_results",
        ["tenant_id", "eval_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_eval_case_results_run_created", table_name="eval_case_results")
    op.drop_table("eval_case_results")
    op.drop_index("ix_eval_runs_tenant_set_created", table_name="eval_runs")
    op.drop_table("eval_runs")
