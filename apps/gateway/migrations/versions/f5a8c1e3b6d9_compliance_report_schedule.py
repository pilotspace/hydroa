"""compliance_report_schedule — tenant_report_schedules/compliance_report_runs tables.

Revision ID: f5a8c1e3b6d9
Revises: a2b4c6d8e0f1
Create Date: 2026-07-14

New tables (compliance-report-center TASK.md §3 CONTRACT — FROZEN @ v1):

  tenant_report_schedules
    tenant_id PK (one row per tenant, mirrors the retention_policy single-row-per-
    tenant idiom), enabled, cadence ('monthly' fixed), day_of_month (1-28 CHECK),
    window_policy ('previous_calendar_month' fixed), delivery_target ('in_app'
    fixed — column present for a later webhook/email addition, unread by v1 code),
    created_by, created_at, updated_at, last_run_at, last_run_status
    (success|skipped_zdr|failed), next_run_at.

  compliance_report_runs
    id (uuid7), tenant_id, period_start, period_end, generated_at, object_key
    (s3-only — no inline BYTEA path), size_bytes, format_version, source
    ('scheduled' fixed — the on-demand path never persists). UNIQUE
    (tenant_id, period_start) — the idempotency target for
    ON CONFLICT (tenant_id, period_start) DO NOTHING (M16), mirrors
    InvoiceRow's own idempotent-insert idiom. INDEX (tenant_id, generated_at DESC)
    for the keyset list (M18).

NOTE (orchestrator re-parenting): this migration's down_revision is pinned to
a2b4c6d8e0f1 per the dispatch prompt's declared parent at branch time — the
orchestrator may need to re-parent this onto whichever head becomes canonical
if other wave-1 R1 tasks also migrate off the same base (TASK.md §0 Ground note).

downgrade() drops both tables (compliance_report_runs first — no FK between
the two tables, but dropped in creation-reverse order for symmetry).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f5a8c1e3b6d9"
down_revision: str | None = "a2b4c6d8e0f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_report_schedules",
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("cadence", sa.Text(), nullable=False, server_default="monthly"),
        sa.Column("day_of_month", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "window_policy", sa.Text(), nullable=False, server_default="previous_calendar_month"
        ),
        sa.Column("delivery_target", sa.Text(), nullable=False, server_default="in_app"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_run_status", sa.Text(), nullable=True),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint("day_of_month BETWEEN 1 AND 28", name="ck_report_schedules_dom"),
        sa.CheckConstraint("cadence = 'monthly'", name="ck_report_schedules_cadence"),
        sa.CheckConstraint(
            "window_policy = 'previous_calendar_month'", name="ck_report_schedules_window_policy"
        ),
        sa.CheckConstraint("delivery_target = 'in_app'", name="ck_report_schedules_delivery"),
        sa.CheckConstraint(
            "last_run_status IN ('success', 'skipped_zdr', 'failed') OR last_run_status IS NULL",
            name="ck_report_schedules_last_run_status",
        ),
    )

    op.create_table(
        "compliance_report_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("period_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "generated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("format_version", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="scheduled"),
        sa.UniqueConstraint(
            "tenant_id", "period_start", name="uq_compliance_report_runs_tenant_period"
        ),
        sa.CheckConstraint("source = 'scheduled'", name="ck_compliance_report_runs_source"),
    )
    op.create_index(
        "ix_compliance_report_runs_tenant_generated",
        "compliance_report_runs",
        ["tenant_id", sa.text("generated_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_compliance_report_runs_tenant_generated", table_name="compliance_report_runs")
    op.drop_table("compliance_report_runs")
    op.drop_table("tenant_report_schedules")
