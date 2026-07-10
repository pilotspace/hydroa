"""saml_provider_configs — per-tenant SAML 2.0 IdP configuration (saml-sso TASK.md §3 Part C).

Revision ID: c950c528d3d5
Revises: 511ad8a7b65e
Create Date: 2026-07-10

New table only — additive, mirrors oidc_provider_configs' shape (tenant_id PK/FK CASCADE,
GIN index on email_domains). idp_x509_cert is TEXT (PEM, public key material) — NOT Fernet-
encrypted, a deliberate divergence from oidc_provider_configs.client_secret_enc (M10:
the cert is not a secret).

sp_entity_id / acs_url are NOT columns — both are server-derived at read time from Settings
+ tenant_id (M2), never persisted, so a future base-URL change never needs a data backfill.

Downgrade: DROP the GIN index then the table (safe — no other table references it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c950c528d3d5"
down_revision: str | None = "a7c2f0e1b4d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "saml_provider_configs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("idp_entity_id", sa.VARCHAR(), nullable=False),
        sa.Column("idp_sso_url", sa.VARCHAR(), nullable=False),
        sa.Column("idp_x509_cert", sa.TEXT(), nullable=False),
        sa.Column(
            "email_domains",
            postgresql.ARRAY(sa.TEXT()),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "email_attribute_name",
            sa.VARCHAR(),
            nullable=False,
            server_default=sa.text(
                "'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress'"
            ),
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_saml_provider_configs_email_domains",
        "saml_provider_configs",
        ["email_domains"],
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_index("ix_saml_provider_configs_email_domains", table_name="saml_provider_configs")
    op.drop_table("saml_provider_configs")
