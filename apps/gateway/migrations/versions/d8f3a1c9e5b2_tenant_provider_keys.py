"""tenant_provider_keys — per-tenant encrypted provider credential store.

Revision ID: d8f3a1c9e5b2
Revises: c9e2f4a8b1d6
Create Date: 2026-06-16

Additive migration (provider-credential-store TASK.md §3 DDL):

New table:
  CREATE TABLE tenant_provider_keys (
    tenant_id   UUID    NOT NULL REFERENCES tenants.id ON DELETE CASCADE,
    provider    TEXT    NOT NULL,
    secret_enc  BYTEA   NOT NULL,
    extra_enc   BYTEA   NULL,
    auth_mode   TEXT    NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider)
  );

SECURITY INVARIANTS:
- secret_enc / extra_enc are BYTEA (Fernet ciphertext) — NEVER the plaintext.
- auth_mode is the only plaintext column (non-secret discriminator).
- The table is NEVER serialized to JSON responses.

Migration chain: c9e2f4a8b1d6 (pricing-units) → d8f3a1c9e5b2 (tenant-provider-keys)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA

# revision identifiers, used by Alembic.
revision: str = "d8f3a1c9e5b2"
down_revision: str | None = "c9e2f4a8b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_provider_keys table."""
    op.create_table(
        "tenant_provider_keys",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("provider", sa.TEXT(), nullable=False),
        # BYTEA — Fernet ciphertext of the primary secret; NEVER the plaintext
        sa.Column("secret_enc", BYTEA(), nullable=False),
        # BYTEA — Fernet ciphertext of remaining fields JSON; NULL for Bearer
        sa.Column("extra_enc", BYTEA(), nullable=True),
        # Plaintext discriminator: "aad" | "api_key" for Azure; NULL for others
        sa.Column("auth_mode", sa.TEXT(), nullable=True),
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
        sa.PrimaryKeyConstraint("tenant_id", "provider"),
    )


def downgrade() -> None:
    """Drop tenant_provider_keys table.

    Safe: additive-only; no existing data depends on this table at migration time.
    """
    op.drop_table("tenant_provider_keys")
