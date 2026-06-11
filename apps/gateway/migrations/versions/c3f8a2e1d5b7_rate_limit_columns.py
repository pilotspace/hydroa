"""rate_limit_columns — additive rpm_limit / tpm_limit columns on api_keys.

Revision ID: c3f8a2e1d5b7
Revises: b1e3f7c9d2a4
Create Date: 2026-06-11

Adds 2 new nullable INTEGER columns to api_keys (no backfill — existing rows keep NULL):
  rpm_limit  INTEGER  NULL  CHECK (rpm_limit > 0)
  tpm_limit  INTEGER  NULL  CHECK (tpm_limit > 0)

Downgrade: drops the 2 columns and their CHECK constraints.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3f8a2e1d5b7"
down_revision: str | None = "b1e3f7c9d2a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add rpm_limit and tpm_limit columns to api_keys (additive — no backfill)."""
    op.add_column(
        "api_keys",
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
    )
    op.add_column(
        "api_keys",
        sa.Column("tpm_limit", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "api_keys_rpm_limit_positive_check",
        "api_keys",
        "rpm_limit IS NULL OR rpm_limit > 0",
    )
    op.create_check_constraint(
        "api_keys_tpm_limit_positive_check",
        "api_keys",
        "tpm_limit IS NULL OR tpm_limit > 0",
    )


def downgrade() -> None:
    """Drop rpm_limit and tpm_limit columns from api_keys."""
    op.drop_constraint("api_keys_tpm_limit_positive_check", "api_keys", type_="check")
    op.drop_constraint("api_keys_rpm_limit_positive_check", "api_keys", type_="check")
    op.drop_column("api_keys", "tpm_limit")
    op.drop_column("api_keys", "rpm_limit")
