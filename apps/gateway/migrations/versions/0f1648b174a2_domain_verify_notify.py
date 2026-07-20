"""domain_verify_notify — opt-in + auto-verify-notify columns on tenant_domain_claims.

Revision ID: 0f1648b174a2
Revises: e6a1d0f47b29
Create Date: 2026-07-20

Additive migration (domain-verify-notify TASK.md §3 DDL):
  ALTER TABLE tenant_domain_claims ADD COLUMN notify_requested_at TIMESTAMPTZ NULL;
  ALTER TABLE tenant_domain_claims ADD COLUMN notified_at TIMESTAMPTZ NULL;

  No new table; EXPECTED_TABLES manifest (tests/migrations/test_migrations.py) is
  unchanged — mirrors tenant-retention-zdr's own additive-columns-on-an-existing-table
  precedent (migrations/versions/a7c2f0e1b4d9_tenant_retention_zdr.py).

  Both columns default NULL (no server_default) — NULL means "not opted in" /
  "not yet notified" respectively, byte-identical state for every pre-existing row.
  FROZEN and UNTOUCHED by this migration: the ClaimStatus CheckConstraint
  (ck_tenant_domain_claims_status), uq_domain_claims_tenant_domain,
  uq_domain_claims_domain_verified.

Downgrade:
  DROP COLUMN tenant_domain_claims.notified_at / notify_requested_at
  (safe — additive nullable columns; no pre-migration code references them)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0f1648b174a2"
down_revision: str | None = "e6a1d0f47b29"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add notify_requested_at / notified_at to tenant_domain_claims."""
    op.add_column(
        "tenant_domain_claims",
        sa.Column("notify_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tenant_domain_claims",
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop the two domain-verify-notify columns from tenant_domain_claims."""
    op.drop_column("tenant_domain_claims", "notified_at")
    op.drop_column("tenant_domain_claims", "notify_requested_at")
