"""impersonation session lifecycle

Creates the `impersonation_sessions` table: a time-boxed, revocable, superadmin-initiated
session-store record (impersonation-session-lifecycle TASK.md §3 Part D). Purely additive —
no existing table is altered.

Mirrors device_authorizations/agent_tokens' own nullable-revoked_at-timestamp shape — the
THIRD use of this pattern after api_keys and agent_tokens. actor_* is the REAL superadmin who
minted the session (snapshotted); target_* is the impersonated user (snapshotted, including
target_role AT MINT TIME — never re-derived live by this task). A partial unique index on
actor_user_id (WHERE revoked_at IS NULL) enforces "at most one active session per superadmin"
at the DB level (M7's race-safety backstop), not just application logic.

Revision ID: 1d563bf9b143
Revises: 1e66a2cb51a6
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "1d563bf9b143"
down_revision = "1e66a2cb51a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "impersonation_sessions",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "actor_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_email", sa.String(), nullable=False),
        sa.Column(
            "target_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_role", sa.String(), nullable=False),
        sa.Column("target_email", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(), nullable=True),
        sa.CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('explicit_end', 'expired_lazy_cleanup')",
            name="impersonation_sessions_revoked_reason_check",
        ),
    )
    op.create_index(
        "uq_impersonation_sessions_actor_active",
        "impersonation_sessions",
        ["actor_user_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_impersonation_sessions_actor_active", table_name="impersonation_sessions")
    op.drop_table("impersonation_sessions")
