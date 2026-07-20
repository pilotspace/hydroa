"""member_verify_code — additive mailbox-confirmation code columns on tenant_domain_claims.

Revision ID: c2e5a9d1b7f4
Revises: 0f1648b174a2
Create Date: 2026-07-20

Additive migration (member-verified-recognition TASK.md §3 DDL — FROZEN @ v1, SECURITY):
  ALTER TABLE tenant_domain_claims ADD COLUMN member_verified_at         TIMESTAMPTZ NULL;
  ALTER TABLE tenant_domain_claims ADD COLUMN member_verify_code_hash    TEXT NULL;
  ALTER TABLE tenant_domain_claims ADD COLUMN member_verify_code_expires_at TIMESTAMPTZ NULL;
  ALTER TABLE tenant_domain_claims ADD COLUMN member_verify_attempt_count INT NOT NULL DEFAULT 0;

  No new table; the EXPECTED_TABLES manifest (tests/migrations/test_migrations.py) is
  unchanged — mirrors 0f1648b174a2 (domain-verify-notify) additive-columns precedent on
  this exact table.

  The 3 nullable columns default NULL (no in-flight code / never member-verified);
  member_verify_attempt_count defaults 0 — byte-identical state for every pre-existing
  row. FROZEN and UNTOUCHED by this migration: the ClaimStatus CheckConstraint
  (ck_tenant_domain_claims_status), uq_domain_claims_tenant_domain,
  uq_domain_claims_domain_verified.

Downgrade:
  DROP the 4 member-verify columns (safe — additive columns; no pre-migration code
  references them).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2e5a9d1b7f4"
down_revision: str | None = "0f1648b174a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the 4 member-verify code columns to tenant_domain_claims."""
    op.add_column(
        "tenant_domain_claims",
        sa.Column("member_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_domain_claims",
        sa.Column("member_verify_code_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "tenant_domain_claims",
        sa.Column("member_verify_code_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_domain_claims",
        sa.Column(
            "member_verify_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    """Drop the four member-verify columns from tenant_domain_claims."""
    op.drop_column("tenant_domain_claims", "member_verify_attempt_count")
    op.drop_column("tenant_domain_claims", "member_verify_code_expires_at")
    op.drop_column("tenant_domain_claims", "member_verify_code_hash")
    op.drop_column("tenant_domain_claims", "member_verified_at")
