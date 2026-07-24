"""stored_responses table — OpenAI-wire Responses state + previous_response_id chaining.

Revision ID: c7e0a4b2d9f1
Revises: f9d3b1a7c2e4
Create Date: 2026-07-24

responses-state-store PLAN.md §3 (FROZEN @ v1). Additive:

  - NEW TABLE stored_responses(id, tenant_id, key_id, model, status,
    previous_response_id, chain_depth, context_messages, response_body, usage,
    created_at) — tenant-scoped materialized-context store for store:true persistence
    and previous_response_id chaining. id is the wire ``resp_*`` string (TEXT PK).
    previous_response_id is a BARE pointer (NO foreign key): a deleted parent leaves an
    honest dangling echo, never mutates history. Payload columns (context_messages /
    response_body / usage) are NOT NULL — no metadata-only state class exists (ZDR
    store:true is rejected 403 pre-dial, and the ZDR sweep row-deletes leftovers).

Indexes (also declared in the ORM __table_args__ per the v30 lesson):
  ix_stored_responses_tenant_created on stored_responses(tenant_id, created_at DESC)
  ix_stored_responses_tenant_prev    on stored_responses(tenant_id, previous_response_id)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c7e0a4b2d9f1"
down_revision = "f9d3b1a7c2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_responses",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("key_id", sa.UUID(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("previous_response_id", sa.Text(), nullable=True),
        sa.Column("chain_depth", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("context_messages", postgresql.JSONB(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=False),
        sa.Column("usage", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stored_responses_tenant_created",
        "stored_responses",
        ["tenant_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_stored_responses_tenant_prev",
        "stored_responses",
        ["tenant_id", "previous_response_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stored_responses_tenant_prev", table_name="stored_responses")
    op.drop_index("ix_stored_responses_tenant_created", table_name="stored_responses")
    op.drop_table("stored_responses")
