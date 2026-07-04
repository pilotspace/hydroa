"""member invite issuance

Creates the `invites` table: a tenant-scoped, single-use, hashed-at-rest, explicitly-
expiring offer for a NEW person to join a tenant as a specific role (member-invite-issuance
TASK.md §3). Purely additive — no existing table is altered (M11).

Mirrors the device_authorizations shape (b3d5f7a9c1e4): a partial unique index enforces
"at most one live pending row" at the DB level, not just application logic (M5); the
token is stored ONLY as a SHA-256 hash, never in plaintext (M3). The role CHECK constraint
lists exactly the 6 self-service Role values (owner/admin/operator/billing_admin/viewer/
member) — 'superadmin' is deliberately excluded (M4), stricter than users_role_check, which
must permit it for real platform-tenant UserRow rows (see entities.py Role.SUPERADMIN,
migration 5b34ca5e1c4b).

Revision ID: 1193bc6178f3
Revises: 5b34ca5e1c4b
Create Date: 2026-07-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "1193bc6178f3"
down_revision = "5b34ca5e1c4b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "invites",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "invited_by_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member')",
            name="invites_role_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked')", name="invites_status_check"
        ),
        sa.CheckConstraint("email = lower(email)", name="invites_email_lowercase_check"),
        sa.UniqueConstraint("token_hash", name="invites_token_hash_key"),
    )
    op.create_index(
        "uq_invites_tenant_email_pending",
        "invites",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_invites_tenant_email_pending", table_name="invites")
    op.drop_table("invites")
