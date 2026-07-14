"""agent identity governance — named agent principals + kill switch

Creates the agent_principals table (a named, tenant-scoped identity grouping of
already-minted agent_tokens rows) and adds the additive, nullable
agent_tokens.principal_id FK.

Revision ID: 9cb98362515f
Revises: 4583689a7b8b
Create Date: 2026-07-14

Additive migration (agent-identity-governance TASK.md §3 DDL):
  CREATE TABLE agent_principals (...)
  ALTER TABLE agent_tokens ADD COLUMN principal_id UUID NULL

  Every existing v39 agent_tokens row stays principal_id=NULL (unattached,
  byte-identical behavior). No existing table is altered destructively.

Downgrade: DROP COLUMN agent_tokens.principal_id, DROP TABLE agent_principals
           (safe — additive; no pre-migration code references either).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

# revision identifiers, used by Alembic.
revision: str = "9cb98362515f"
down_revision: str | None = "4583689a7b8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_principals",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "tenant_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "owner_user_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("monthly_budget_usd", sa.Numeric(12, 2), nullable=True),
        sa.Column("rpm_limit", sa.Integer(), nullable=True),
        sa.Column("tpm_limit", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("killed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_agent_principals_tenant_name"),
    )

    op.add_column(
        "agent_tokens",
        sa.Column(
            "principal_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("agent_principals.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_agent_tokens_principal_id", "agent_tokens", ["principal_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_tokens_principal_id", table_name="agent_tokens")
    op.drop_column("agent_tokens", "principal_id")
    op.drop_table("agent_principals")
