"""per_key_guardrail_policies — add guardrail_policy JSONB column to api_keys table.

Revision ID: d401ca5a7cde
Revises: 511ad8a7b65e
Create Date: 2026-07-10

Additive migration (per-key-guardrail-policies TASK.md §3 DDL):
  ALTER TABLE api_keys ADD COLUMN guardrail_policy JSONB NULL DEFAULT NULL;

  No new tables; EXPECTED_TABLES manifest unchanged.
  Column is NULLABLE with NO server default — NULL means "no override, inherit the
  tenant's guardrail_configs" (byte-identical default for every existing + new row).
  A non-NULL value (including an explicit {}) is a deliberate key-level override,
  wholesale, no field merge with the tenant — mirrors api_keys.model_allowlist's
  nullable-JSONB shape on this same table (NOT the tenants.guardrail_configs
  precedent, which is NOT NULL DEFAULT '{}'::jsonb — a deliberately DIFFERENT
  nullability because NULL vs {} is a meaningful distinction at the key level).

Downgrade:
  DROP COLUMN api_keys.guardrail_policy
  (safe — additive nullable column, no server default; no pre-migration code
  references it)

Migration chain: ... -> 511ad8a7b65e -> d401ca5a7cde (per-key-guardrail-policies)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d401ca5a7cde"
down_revision: str | None = "511ad8a7b65e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add guardrail_policy JSONB NULL DEFAULT NULL to api_keys."""
    op.add_column(
        "api_keys",
        sa.Column(
            "guardrail_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop guardrail_policy column from api_keys."""
    op.drop_column("api_keys", "guardrail_policy")
