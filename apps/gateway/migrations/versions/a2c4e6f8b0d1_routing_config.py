"""routing_config — operator-wide singleton routing configuration (v32 routing-config-store).

Revision ID: a2c4e6f8b0d1
Revises: d1e2f3a4b5c6
Create Date: 2026-06-23

Creates the routing_config table (operator-wide SINGLETON — at most one row):
  - id          BOOLEAN PK DEFAULT true  CHECK (id IS TRUE)   # singleton: only one row possible
  - config      JSONB   NOT NULL                              # overridable routing fields doc
  - updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()

The config doc holds the overridable routing fields (model_groups, routing_strategy, cooldown_*,
upstream_*, loadbal_*). Applied OVER the env-derived Settings at gateway boot (DB-wins-when-present,
env fallback). Restart-to-apply — no live mutation of the running router.

Downgrade: drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a2c4e6f8b0d1"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the singleton routing_config table."""
    op.create_table(
        "routing_config",
        sa.Column(
            "id",
            sa.Boolean(),
            primary_key=True,
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("id IS TRUE", name="routing_config_singleton_check"),
    )


def downgrade() -> None:
    """Drop the routing_config table."""
    op.drop_table("routing_config")
