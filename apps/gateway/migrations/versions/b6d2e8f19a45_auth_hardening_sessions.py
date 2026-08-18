"""auth-hardening — password_reset_tokens, revoked_auth_sessions, users.sessions_not_before.

Revision ID: b6d2e8f19a45
Revises: a3f9c7e21b84
Create Date: 2026-08-18

Additive-only migration (auth-hardening-login-sessions TASK.md §3 CONTRACT — FROZEN @ v1,
SECURITY), parented on the current head (a3f9c7e21b84):

  1. ALTER TABLE users ADD COLUMN sessions_not_before TIMESTAMPTZ NULL
     — the user-wide session watermark (M4): a session JWT whose iat predates it is
     refused at the identity seam. NULL = no user-wide revocation ever issued (default
     for every existing row — no backfill needed). Mirrors deactivated_at's
     nullable-TIMESTAMPTZ soft pattern (migration 010e6f83a709) exactly.

  2. CREATE TABLE password_reset_tokens (token_hash PK, user_id FK, expires_at, used_at,
     created_at) — hashed-at-rest, single-use, TTL-bounded reset tokens (M3). Mirrors
     pending_personal_signups' hashed-token convention: the raw 256-bit token exists only
     in the emailed link.

  3. CREATE TABLE revoked_auth_sessions (jti PK, user_id FK, expires_at, revoked_at)
     — the server-side jti denylist behind POST /admin/auth/logout (M5); expires_at (the
     token's own ceiling) bounds growth via opportunistic delete at insert time.

Both new tables land in BOTH table manifests (tests/migrations EXPECTED_TABLES + the
guardrails pg_tables NOT-IN inventory) per the four-manifests rule; ORM rows registered
on Base.metadata via gateway.tenants.infrastructure.orm (already imported by env.py).

Downgrade: drops all three additions in reverse order (safe — additive only).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "b6d2e8f19a45"
down_revision = "a3f9c7e21b84"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("sessions_not_before", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "password_reset_tokens",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "revoked_auth_sessions",
        sa.Column("jti", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("revoked_auth_sessions")
    op.drop_table("password_reset_tokens")
    op.drop_column("users", "sessions_not_before")
