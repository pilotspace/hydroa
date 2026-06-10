"""baseline — initial schema for all six domain tables.

Revision ID: ad14442336db
Revises:
Create Date: 2026-06-10

WARNING — downgrade() is DESTRUCTIVE: it drops all six domain tables and all
data they contain.  Run downgrade only in controlled circumstances (CI teardown,
local dev reset).  Never run downgrade base in production without a full backup
and an approved runbook.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "ad14442336db"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create all six domain tables in dependency order.

    Dependency order (FK graph):
      tenants  (root — no FK deps)
      models   (root — no FK deps)
      users    -> tenants
      api_keys -> tenants
      pricing_snapshots -> models
      usage_records -> tenants
    """
    # ------------------------------------------------------------------
    # tenants
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "markup_pct",
            sa.Numeric(7, 4),
            server_default="20.0",
            nullable=False,
        ),
        sa.Column(
            "budget_usd_monthly",
            sa.Numeric(12, 2),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------
    # models
    # ------------------------------------------------------------------
    op.create_table(
        "models",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("context_length", sa.Integer(), nullable=True),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------
    # users  (FK -> tenants)
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column(
            "role",
            sa.String(),
            server_default=sa.text("'owner'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'admin', 'member')",
            name="users_role_check",
        ),
        sa.CheckConstraint(
            "email = lower(email)",
            name="users_email_lowercase_check",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    # ------------------------------------------------------------------
    # api_keys  (FK -> tenants)
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="api_keys_name_length_check",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------
    # pricing_snapshots  (FK -> models)
    # ------------------------------------------------------------------
    op.create_table(
        "pricing_snapshots",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column(
            "prompt_usd_per_token",
            sa.Numeric(20, 10),
            nullable=False,
        ),
        sa.Column(
            "completion_usd_per_token",
            sa.Numeric(20, 10),
            nullable=False,
        ),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["models.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------
    # usage_records  (FK -> tenants; no FK on key_id or model_id by design)
    # ------------------------------------------------------------------
    op.create_table(
        "usage_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "key_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "cost_usd",
            sa.Numeric(14, 8),
            server_default="0",
            nullable=False,
        ),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column(
            "pricing_snapshot_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "raw",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Drop all six domain tables in reverse dependency order.

    DATA-LOSS WARNING: This operation is irreversible.  All rows in
    usage_records, pricing_snapshots, api_keys, users, models, and
    tenants will be permanently deleted.  Only run this in CI teardown
    or local dev reset — never in production without a verified backup.
    """
    op.drop_table("usage_records")
    op.drop_table("pricing_snapshots")
    op.drop_table("api_keys")
    op.drop_table("users")
    op.drop_table("models")
    op.drop_table("tenants")
