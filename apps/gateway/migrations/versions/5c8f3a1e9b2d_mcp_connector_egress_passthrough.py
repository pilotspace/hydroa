"""mcp_connector_egress_passthrough — add MCP allow-list columns.

Revision ID: 5c8f3a1e9b2d
Revises: 4583689a7b8b
Create Date: 2026-07-14

Additive migration (mcp-connector-passthrough TASK.md §3 Schema):
  ALTER TABLE tenants ADD COLUMN mcp_allowed_servers JSONB NOT NULL DEFAULT '[]'::jsonb;
  ALTER TABLE tenants ADD COLUMN mcp_allowed_servers_updated_at TIMESTAMPTZ NULL DEFAULT NULL;
  ALTER TABLE api_keys ADD COLUMN mcp_allowed_servers_override JSONB NULL DEFAULT NULL;

  No new tables; EXPECTED_TABLES manifest unchanged.

  mcp_allowed_servers_updated_at is an implementation-detail column NOT named in §3's
  Schema block (which lists only the two domain-significant allow-list columns) — added
  to honestly satisfy the §3 response contract's `updated_at: str | null` shape for
  GET/PUT /admin/mcp-servers (null until the first PUT), mirroring
  tenants.residency_region_updated_at's own precedent exactly (b3e6a1d9f4c7). Purely
  additive, non-security-relevant bookkeeping — flagged here rather than silently added.

  tenants.mcp_allowed_servers: NOT NULL DEFAULT '[]'::jsonb — empty list is the secure
  default (deny-all) for every existing + new tenant row (byte-identical to pre-task
  behavior: no tenant could reach any MCP server before this task existed).

  api_keys.mcp_allowed_servers_override: NULLABLE, NO server default — NULL means "no
  override, inherit the tenant's mcp_allowed_servers" (byte-identical default for every
  existing + new key). A non-NULL value (including an explicit []) is a deliberate
  key-level override, wholesale, no field merge with the tenant — mirrors
  api_keys.guardrail_policy's own nullable-JSONB shape on this same table
  (d401ca5a7cde_per_key_guardrail_policies.py precedent).

Downgrade:
  DROP COLUMN api_keys.mcp_allowed_servers_override
  DROP COLUMN tenants.mcp_allowed_servers
  (safe — additive columns, no pre-migration code references them)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "5c8f3a1e9b2d"
down_revision: str | None = "4583689a7b8b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add mcp_allowed_servers to tenants and mcp_allowed_servers_override to api_keys."""
    op.add_column(
        "tenants",
        sa.Column(
            "mcp_allowed_servers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "tenants",
        sa.Column(
            "mcp_allowed_servers_updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "api_keys",
        sa.Column(
            "mcp_allowed_servers_override",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Drop the MCP allow-list columns."""
    op.drop_column("api_keys", "mcp_allowed_servers_override")
    op.drop_column("tenants", "mcp_allowed_servers_updated_at")
    op.drop_column("tenants", "mcp_allowed_servers")
