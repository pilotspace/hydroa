"""pending_personal_signups — the personal-tier deferred-creation signup mailbox-proof
table (scoped-self-serve-signup TASK.md §3 DDL — FROZEN @ v1, SECURITY).

Revision ID: b8e1c4f2a9d6
Revises: a4f2d9c17b3e
Create Date: 2026-07-20

Additive migration — ONE new table, no existing table altered:

  TABLE pending_personal_signups: a short-lived (~24h), single-use, mailbox-proof-BEFORE-
  creation row for a personal-tier self-serve signup. Only the SHA256 confirm_token_hash
  is stored (the 256-bit CSPRNG plaintext is emailed once, never persisted);
  password_hash is the already-argon2-hashed password computed at issuance, passed
  through unchanged at confirm-time (never re-hashed). UNIQUE(email) is the
  UPSERT/create-or-reissue target; UNIQUE(confirm_token_hash) backs the single-statement
  atomic consume.

FROZEN and UNTOUCHED by this migration: tenants, users, the S1 signup gate, and every
domain-capture / invite table.

Downgrade: DROP the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e1c4f2a9d6"
down_revision: str | None = "a4f2d9c17b3e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pending_personal_signups. Uniqueness on email and confirm_token_hash is
    declared as column-level UNIQUE constraints (not unique indexes) to match the ORM's
    ``mapped_column(Text, unique=True)`` exactly — otherwise `alembic check` autogenerate
    detects a remove-index/add-constraint diff (caught by the migration-parity suite)."""
    op.create_table(
        "pending_personal_signups",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("tenant_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("confirm_token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("email = lower(email)", name="ck_pending_personal_signups_email_lower"),
    )


def downgrade() -> None:
    """Drop pending_personal_signups (its inline unique constraints go with the table)."""
    op.drop_table("pending_personal_signups")
