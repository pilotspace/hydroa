"""credit_ledger + tenant_credit_balances — append-only prepaid credits ledger
(credits-ledger TASK.md §3, FROZEN @ v1).

Revision ID: d3f7a9c1b5e8
Revises: 69cfdc584129
Create Date: 2026-07-12

Creates:
  tenant_credit_balances (
    tenant_id    UUID          PRIMARY KEY REFERENCES tenants(id) ON DELETE RESTRICT
    balance_usd  NUMERIC(14,8) NOT NULL DEFAULT 0
    grace_usd    NUMERIC(14,8) NOT NULL DEFAULT 0
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT now()
  )
  -- Denormalized cache of SUM(credit_ledger.amount_usd) for the tenant (M7); every
  -- credit_ledger INSERT updates this row in the SAME transaction via SELECT ... FOR
  -- UPDATE — the row lock is what serializes concurrent admissions (M3).

  credit_ledger (
    id                  UUID          PRIMARY KEY
    tenant_id           UUID          NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
    entry_type          TEXT          NOT NULL   -- topup|hold|settle|release|correction
    amount_usd          NUMERIC(14,8) NOT NULL   -- signed delta
    balance_after_usd   NUMERIC(14,8) NOT NULL   -- snapshot, same locked txn as insert
    reference_type      TEXT          NULL       -- usage_record | correction_of | NULL
    reference_id        UUID          NULL
    request_id          UUID          NULL       -- correlates hold -> settle/release
    idempotency_key     TEXT          NULL       -- topup only
    actor_user_id       UUID          NULL
    note                TEXT          NULL
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT now()
  )

IMMUTABILITY (M8/R7 — mirrors audit_events, migration e3f5a7c9b1d2): two PostgreSQL
RULEs block UPDATE and DELETE at the DB engine level. Any application code (or an ad
hoc admin script) that issues either statement against credit_ledger is a silent no-op.

INDEXES:
  credit_ledger_tenant_created_idx (tenant_id, created_at DESC) — history reads (M10).
  credit_ledger_request_idx (tenant_id, request_id) WHERE request_id IS NOT NULL —
    hold -> settle/release correlation lookups (M4/M5) and the orphaned-hold sweep (M6).
  UNIQUE credit_ledger_tenant_idem_uq (tenant_id, idempotency_key) WHERE idempotency_key
    IS NOT NULL — top-up replay dedupe (M9).

Downgrade: drops the rules first (so DROP TABLE can proceed), then drops both tables
(credit_ledger before tenant_credit_balances — no FK between them, order is cosmetic).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d3f7a9c1b5e8"
down_revision: str | None = "69cfdc584129"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_credit_balances + credit_ledger with DB-enforced immutability."""
    op.create_table(
        "tenant_credit_balances",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "balance_usd",
            sa.Numeric(14, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "grace_usd",
            sa.Numeric(14, 8),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "credit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("balance_after_usd", sa.Numeric(14, 8), nullable=False),
        sa.Column("reference_type", sa.Text(), nullable=True),
        sa.Column("reference_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )

    op.create_index(
        "credit_ledger_tenant_created_idx",
        "credit_ledger",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "credit_ledger_request_idx",
        "credit_ledger",
        ["tenant_id", "request_id"],
        postgresql_where=sa.text("request_id IS NOT NULL"),
    )
    op.create_index(
        "credit_ledger_tenant_idem_uq",
        "credit_ledger",
        ["tenant_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    # DB-enforced immutability (M8/R7) — mirrors audit_events (migration e3f5a7c9b1d2).
    op.execute(
        "CREATE RULE credit_ledger_no_update AS ON UPDATE TO credit_ledger DO INSTEAD NOTHING"
    )
    op.execute(
        "CREATE RULE credit_ledger_no_delete AS ON DELETE TO credit_ledger DO INSTEAD NOTHING"
    )


def downgrade() -> None:
    """Drop immutability rules, indexes, and both tables."""
    op.execute("DROP RULE IF EXISTS credit_ledger_no_update ON credit_ledger")
    op.execute("DROP RULE IF EXISTS credit_ledger_no_delete ON credit_ledger")
    op.drop_index("credit_ledger_tenant_idem_uq", table_name="credit_ledger")
    op.drop_index("credit_ledger_request_idx", table_name="credit_ledger")
    op.drop_index("credit_ledger_tenant_created_idx", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_table("tenant_credit_balances")
