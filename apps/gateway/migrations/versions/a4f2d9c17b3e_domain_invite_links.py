"""domain_invite_links + domain_invite_redemptions — the domain-restricted shareable
invite link (invite-by-domain TASK.md §3 DDL — FROZEN @ v1, SECURITY, task 6a).

Revision ID: a4f2d9c17b3e
Revises: c2e5a9d1b7f4
Create Date: 2026-07-20

Additive migration — two NEW tables, no existing table altered:

  TABLE domain_invite_links: the tenant-scoped, reusable, revocable, 30-day link. Only the
  SHA256 token_hash is stored (plaintext returned once at create, never persisted). A
  partial unique index WHERE status='active' guarantees at most ONE active link per
  (tenant_id, domain) — re-create atomically supersedes the old row.

  TABLE domain_invite_redemptions: the ephemeral per-(link, email) 6-digit-code challenge.
  code_hash is the Option-A keyed HMAC at rest (member_verify_code.py). UNIQUE (link_id,
  email) is the UPSERT / re-issue target. ON DELETE CASCADE off the parent link.

FROZEN and UNTOUCHED by this migration: resolve_verified_tenant, the ClaimStatus
CheckConstraint + tenant_domain_claims unique indexes, and the per-email invites tables.

Downgrade: DROP both tables (redemptions first — it FKs the links table).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4f2d9c17b3e"
down_revision: str | None = "c2e5a9d1b7f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the two domain-invite tables + their indexes."""
    op.create_table(
        "domain_invite_links",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("status IN ('active', 'revoked')", name="ck_domain_invite_links_status"),
        sa.CheckConstraint("domain = lower(domain)", name="ck_domain_invite_links_domain_lower"),
    )
    op.create_index(
        "uq_domain_invite_links_token_hash",
        "domain_invite_links",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "uq_domain_invite_links_active_domain",
        "domain_invite_links",
        ["tenant_id", "domain"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "domain_invite_redemptions",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "link_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domain_invite_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("code_hash", sa.Text(), nullable=False),
        sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "email = lower(email)", name="ck_domain_invite_redemptions_email_lower"
        ),
    )
    op.create_index(
        "uq_domain_invite_redemptions_link_email",
        "domain_invite_redemptions",
        ["link_id", "email"],
        unique=True,
    )


def downgrade() -> None:
    """Drop both tables (redemptions first — it FKs domain_invite_links)."""
    op.drop_index(
        "uq_domain_invite_redemptions_link_email", table_name="domain_invite_redemptions"
    )
    op.drop_table("domain_invite_redemptions")
    op.drop_index("uq_domain_invite_links_active_domain", table_name="domain_invite_links")
    op.drop_index("uq_domain_invite_links_token_hash", table_name="domain_invite_links")
    op.drop_table("domain_invite_links")
