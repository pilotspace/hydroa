"""oidc_tenant_config — per-tenant OIDC IdP configuration table + users.auth_method column.

Revision ID: a9b3c4d5e6f7
Revises: f1b2c3d4e5a6
Create Date: 2026-06-11

Additive migration (oidc-tenant-config TASK.md §3 DDL):

New table:
  CREATE TABLE oidc_provider_configs (
    tenant_id         UUID PRIMARY KEY NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    issuer            VARCHAR NOT NULL,
    client_id         VARCHAR NOT NULL,
    client_secret_enc BYTEA NOT NULL,
    authorize_url     VARCHAR NOT NULL DEFAULT '',
    token_url         VARCHAR NOT NULL,
    jwks_url          VARCHAR NOT NULL,
    email_domains     TEXT[] NOT NULL DEFAULT '{}',
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX ix_oidc_provider_configs_email_domains
    ON oidc_provider_configs USING GIN (email_domains);

New column (additive):
  ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_method VARCHAR(32) NOT NULL DEFAULT 'password';
  UPDATE users SET auth_method = 'oidc' WHERE password_hash = '!sso-no-password';

Downgrade:
  DROP TABLE IF EXISTS oidc_provider_configs;
  ALTER TABLE users DROP COLUMN IF EXISTS auth_method;

Migration chain: f1b2c3d4e5a6 (semantic-caching) → a9b3c4d5e6f7 (oidc-tenant-config)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, BYTEA

# revision identifiers, used by Alembic.
revision: str = "a9b3c4d5e6f7"
down_revision: str | None = "f1b2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create oidc_provider_configs table + GIN index; add users.auth_method column."""
    # Create oidc_provider_configs table
    op.create_table(
        "oidc_provider_configs",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("issuer", sa.VARCHAR(), nullable=False),
        sa.Column("client_id", sa.VARCHAR(), nullable=False),
        sa.Column("client_secret_enc", BYTEA(), nullable=False),
        sa.Column(
            "authorize_url",
            sa.VARCHAR(),
            nullable=False,
            server_default=sa.text("''"),
        ),
        sa.Column("token_url", sa.VARCHAR(), nullable=False),
        sa.Column("jwks_url", sa.VARCHAR(), nullable=False),
        sa.Column(
            "email_domains",
            ARRAY(sa.TEXT()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    # GIN index for email_domains @> containment queries
    op.create_index(
        "ix_oidc_provider_configs_email_domains",
        "oidc_provider_configs",
        ["email_domains"],
        postgresql_using="gin",
    )

    # Add auth_method column to users (additive, NOT NULL with DEFAULT 'password')
    op.add_column(
        "users",
        sa.Column(
            "auth_method",
            sa.VARCHAR(32),
            nullable=False,
            server_default=sa.text("'password'"),
        ),
    )

    # Backfill: SSO users (sentinel hash) → auth_method = 'oidc'
    op.execute(
        sa.text("UPDATE users SET auth_method = 'oidc' WHERE password_hash = '!sso-no-password'")
    )


def downgrade() -> None:
    """Drop oidc_provider_configs table and users.auth_method column."""
    op.drop_column("users", "auth_method")
    op.drop_index(
        "ix_oidc_provider_configs_email_domains",
        table_name="oidc_provider_configs",
    )
    op.drop_table("oidc_provider_configs")
