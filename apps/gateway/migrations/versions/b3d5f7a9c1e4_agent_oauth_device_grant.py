"""agent oauth device grant store

Creates the OAuth 2.0 Device Authorization Grant (RFC 8628) store:
  - device_authorizations : pending/approved/denied/consumed requests (hashed codes)
  - agent_tokens          : issued opaque agent tokens (hashed access + refresh)

All secrets are stored ONLY as SHA-256 hashes (mirrors the api_keys invariant); all
timestamp columns are timestamptz to match the ORM (create_all) and avoid naive/aware drift.

Revision ID: b3d5f7a9c1e4
Revises: f2a4c6e8b0d3
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "b3d5f7a9c1e4"
down_revision = "f2a4c6e8b0d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_authorizations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("device_code_hash", sa.String(), nullable=False),
        sa.Column("user_code_hash", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("scope", sa.String(), nullable=False, server_default=sa.text("'proxy'")),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'consumed')",
            name="device_authorizations_status_check",
        ),
        sa.CheckConstraint(
            "interval_seconds > 0", name="device_authorizations_interval_positive_check"
        ),
        sa.UniqueConstraint("device_code_hash", name="device_authorizations_device_code_hash_key"),
    )
    op.create_index(
        "ix_device_authorizations_user_code_hash", "device_authorizations", ["user_code_hash"]
    )
    op.create_index(
        "uq_device_authorizations_user_code_pending",
        "device_authorizations",
        ["user_code_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "agent_tokens",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "authorization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("device_authorizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("access_token_hash", sa.String(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=True),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(), nullable=False, server_default=sa.text("'proxy'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("authorization_id", name="agent_tokens_authorization_id_key"),
        sa.UniqueConstraint("access_token_hash", name="agent_tokens_access_token_hash_key"),
        sa.UniqueConstraint("refresh_token_hash", name="agent_tokens_refresh_token_hash_key"),
    )
    op.create_index("ix_agent_tokens_access_token_hash", "agent_tokens", ["access_token_hash"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_access_token_hash", table_name="agent_tokens")
    op.drop_table("agent_tokens")
    op.drop_index("uq_device_authorizations_user_code_pending", table_name="device_authorizations")
    op.drop_index("ix_device_authorizations_user_code_hash", table_name="device_authorizations")
    op.drop_table("device_authorizations")
