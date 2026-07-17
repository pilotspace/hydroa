"""self_serve_checkout — plans.self_serve/audience + checkout_sessions table.

Revision ID: b7e2c4a9f1d3
Revises: 9cdca76231c6
Create Date: 2026-07-17

self-serve-checkout TASK.md §3 (FROZEN @ v1). Additive + data-seed, single alembic head,
reversible:

  - ADD `plans.self_serve` (BOOLEAN NOT NULL DEFAULT false) — "enterprise = contact sales"
    is data-driven (I2/A3), NOT the ambiguous `base_price IS NULL` test.
  - ADD `plans.audience` (TEXT NULL) — the personal/business split (I3/A1) becomes
    data-driven instead of seed-convention-only.
  - id-preserving UPDATE seeds (never DELETE+reinsert — plan_id FK is ON DELETE RESTRICT):
      free/starter/pro   -> self_serve=true,  audience='personal'
      team               -> self_serve=true,  audience='business'
      enterprise         -> self_serve=false, audience='business'
  - CREATE `checkout_sessions` (the seam's own state, I5) with UNIQUE(tenant_id,
    idempotency_key) for idempotent create (M4). NO card/PAN/CVV/PII columns (M6).

Downgrade (reversible): drop checkout_sessions (+ its unique index), then drop the two
`plans` columns. The seed UPDATEs need no explicit reversal — the columns they wrote are
dropped wholesale.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7e2c4a9f1d3"
down_revision: str | None = "9cdca76231c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- additive plans columns -------------------------------------------
    op.add_column(
        "plans",
        sa.Column(
            "self_serve",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column("plans", sa.Column("audience", sa.Text(), nullable=True))

    # --- id-preserving seed of the shipped 5-tier catalog ------------------
    op.execute(
        sa.text(
            "UPDATE plans SET self_serve = true, audience = 'personal'"
            " WHERE name IN ('free', 'starter', 'pro')"
        )
    )
    op.execute(
        sa.text("UPDATE plans SET self_serve = true, audience = 'business' WHERE name = 'team'")
    )
    op.execute(
        sa.text(
            "UPDATE plans SET self_serve = false, audience = 'business' WHERE name = 'enterprise'"
        )
    )

    # --- checkout_sessions table ------------------------------------------
    op.create_table(
        "checkout_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("intent_type", sa.Text(), nullable=False),
        sa.Column("intent_payload", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("provider_ref", sa.Text(), nullable=True),
        sa.Column("applied_ref", postgresql.JSONB(), nullable=True),
        sa.Column("preview", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
    )
    op.create_index(
        "checkout_sessions_tenant_idem_uq",
        "checkout_sessions",
        ["tenant_id", "idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("checkout_sessions_tenant_idem_uq", table_name="checkout_sessions")
    op.drop_table("checkout_sessions")
    op.drop_column("plans", "audience")
    op.drop_column("plans", "self_serve")
