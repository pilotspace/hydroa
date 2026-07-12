"""invoice_generation — invoices/invoice_lines/invoice_corrections tables.

Revision ID: 0b5527920450
Revises: fddae7074590
Create Date: 2026-07-12

New tables (invoice-generation TASK.md §3 CONTRACT — FROZEN @ v1):

  invoices
    id, tenant_id (FK RESTRICT), period_start, period_end, status
    (draft|issued), currency, total_usd, raw_total_usd, tax_usd, issued_at,
    created_at. UNIQUE (tenant_id, period_start) — the idempotent-generation
    target for ON CONFLICT (tenant_id, period_start) DO NOTHING (M13).

  invoice_lines
    id, invoice_id (FK RESTRICT), model_id, team_id, key_id, tags (JSONB
    canonical tag-set grouping dimension), amount_usd (rounded, billed),
    raw_amount_usd (pre-rounding), prompt_tokens, completion_tokens,
    request_count, line_type ('usage' — seat-billing extension point, M14),
    created_at.

  invoice_corrections
    id, invoice_id (FK RESTRICT), delta_usd (signed), reason, created_by,
    created_at. Append-only — no repository update/delete method exists (M6).

IMMUTABILITY (M5): a BEFORE UPDATE OR DELETE trigger on all three tables always
RAISEs — every row this task writes is inserted already in its final form (no
draft->issued UPDATE transition in v1; auto-issue only runs after the
stabilization window has already elapsed). Mirrors audit_events' immutability
trigger (migration f2a4c6e8b0d3) — RAISE rather than a silent RULE no-op, so a
violation surfaces loudly instead of being swallowed.

downgrade() drops the triggers/functions then the three tables (children first).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0b5527920450"
down_revision: str | None = "d3f7a9c1b5e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "invoices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("period_start", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("currency", sa.Text(), nullable=False, server_default="USD"),
        sa.Column("total_usd", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("raw_total_usd", sa.Numeric(14, 8), nullable=False, server_default="0"),
        sa.Column("tax_usd", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("tenant_id", "period_start", name="uq_invoices_tenant_period"),
        sa.CheckConstraint("status IN ('draft', 'issued')", name="ck_invoices_status"),
    )
    op.create_index("ix_invoices_tenant_period_id", "invoices", ["tenant_id", "period_start", "id"])

    op.create_table(
        "invoice_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("amount_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("raw_amount_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("prompt_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("completion_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("line_type", sa.Text(), nullable=False, server_default="usage"),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_invoice_lines_invoice_id", "invoice_lines", ["invoice_id"])

    op.create_table(
        "invoice_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "invoice_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("invoices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("delta_usd", sa.Numeric(12, 2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_invoice_corrections_invoice_id", "invoice_corrections", ["invoice_id"])

    # Immutability triggers (M5) — always RAISE on UPDATE/DELETE, no bypass GUC
    # (unlike audit_events' retention-sweeper bypass: nothing ever legitimately
    # mutates or deletes an invoice/invoice_line/invoice_correction row).
    for table in ("invoices", "invoice_lines", "invoice_corrections"):
        op.execute(
            f"""
            CREATE OR REPLACE FUNCTION {table}_immutable_guard_fn() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION 'invoice_immutable_violation: {table} rows are immutable';
            END;
            $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table}_immutable_guard
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION {table}_immutable_guard_fn()
            """
        )


def downgrade() -> None:
    for table in ("invoices", "invoice_lines", "invoice_corrections"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_immutable_guard ON {table}")
        op.execute(f"DROP FUNCTION IF EXISTS {table}_immutable_guard_fn()")
    op.drop_index("ix_invoice_corrections_invoice_id", table_name="invoice_corrections")
    op.drop_table("invoice_corrections")
    op.drop_index("ix_invoice_lines_invoice_id", table_name="invoice_lines")
    op.drop_table("invoice_lines")
    op.drop_index("ix_invoices_tenant_period_id", table_name="invoices")
    op.drop_table("invoices")
