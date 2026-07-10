"""scim-provisioning — deactivated_at, scim_tokens, audit_events.actor_scim_token_id.

Revision ID: 010e6f83a709
Revises: 511ad8a7b65e
Create Date: 2026-07-10

Additive-only migration (scim-provisioning TASK.md §3 CONTRACT — FROZEN @ v1), parented on
the current head (511ad8a7b65e):

  1. ALTER TABLE users ADD COLUMN deactivated_at TIMESTAMPTZ NULL
     — mirrors api_keys.revoked_at's nullable-timestamp soft pattern. NULL = active
     (default for every existing row — no backfill needed).

  2. CREATE TABLE scim_tokens (...)
     — a per-tenant bearer credential, SHA-256-hashed at rest (mirrors api_keys' own
     key_hash pattern). Shape mirrors api_keys minus the governance/budget columns that
     don't apply to a provisioning credential.

  3. ALTER TABLE audit_events ADD COLUMN actor_scim_token_id UUID NULL
     REFERENCES scim_tokens(id) ON DELETE SET NULL
     — additive, mirrors the existing actor_key_id precedent (migration 511ad8a7b65e)
     exactly. AuditEvent.__post_init__ is widened (application-layer change, this
     migration only adds the column) to accept actor_scim_token_id as a third valid
     actor alongside actor_user_id/actor_key_id. Every existing call site is unaffected
     — the field defaults to NULL/None.

Downgrade: drops all three additions in reverse order (safe — additive only, no other
column/constraint references any of them).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010e6f83a709"
down_revision: str | None = "511ad8a7b65e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. users.deactivated_at — nullable timestamp, NULL = active.
    op.add_column(
        "users",
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
    )

    # 2. scim_tokens — per-tenant SCIM bearer credential.
    op.create_table(
        "scim_tokens",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 200", name="scim_tokens_name_length_check"
        ),
    )

    # 3. audit_events.actor_scim_token_id — additive, mirrors actor_key_id exactly.
    op.add_column(
        "audit_events",
        sa.Column(
            "actor_scim_token_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("audit_events", "actor_scim_token_id")
    op.drop_table("scim_tokens")
    op.drop_column("users", "deactivated_at")
