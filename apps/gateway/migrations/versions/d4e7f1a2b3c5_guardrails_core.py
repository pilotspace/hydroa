"""guardrails_core — add guardrail_configs JSONB column to tenants table.

Revision ID: d4e7f1a2b3c5
Revises: 1c4e7a9f3b2d
Create Date: 2026-06-11

Additive migration (guardrails-core TASK.md §3 DDL):
  ALTER TABLE tenants ADD COLUMN guardrail_configs JSONB NOT NULL DEFAULT '{}'::jsonb;

  No new tables; EXPECTED_TABLES manifest unchanged.
  Column has server_default '{}'::jsonb so all existing rows default to empty config
  (no guardrails enabled).

Downgrade:
  DROP COLUMN tenants.guardrail_configs
  (safe — additive column with server default; no existing code references it pre-migration)

Migration chain: ... → 1c4e7a9f3b2d (response-caching) → d4e7f1a2b3c5 (guardrails-core)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e7f1a2b3c5"
down_revision: str | None = "1c4e7a9f3b2d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add guardrail_configs JSONB NOT NULL DEFAULT '{}'::jsonb to tenants."""
    op.add_column(
        "tenants",
        sa.Column(
            "guardrail_configs",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    """Drop guardrail_configs column from tenants."""
    op.drop_column("tenants", "guardrail_configs")
