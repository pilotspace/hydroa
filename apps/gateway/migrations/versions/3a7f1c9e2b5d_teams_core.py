"""teams_core — teams, team_members tables and api_keys.team_id FK column.

Revision ID: 3a7f1c9e2b5d
Revises: e7f3b2a9c4d1
Create Date: 2026-06-11

Additive migration (teams-core TASK.md §3 DDL):
  CREATE TABLE teams (
    id         UUID        PRIMARY KEY,
    tenant_id  UUID        NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name       TEXT        NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, name)
  );

  CREATE TABLE team_members (
    team_id   UUID        NOT NULL REFERENCES teams(id)  ON DELETE CASCADE,
    user_id   UUID        NOT NULL REFERENCES users(id)  ON DELETE CASCADE,
    role      TEXT        NOT NULL CHECK(role IN ('lead', 'member')),
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, user_id)
  );

  ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS team_id UUID
      REFERENCES teams(id) ON DELETE SET NULL;

Downgrade: DROP COLUMN api_keys.team_id, DROP TABLE team_members, DROP TABLE teams
           (safe — all additive; no existing rows reference these objects pre-migration)

Forward-compat: no constraint blocks a future additive team_budget_usd column on teams.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3a7f1c9e2b5d"
down_revision: str | None = "e7f3b2a9c4d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create teams + team_members tables; add team_id FK column to api_keys."""
    # 1. Create teams table
    op.create_table(
        "teams",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="teams_name_length_check",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="teams_tenant_name_uq"),
    )

    # 2. Create team_members table
    op.create_table(
        "team_members",
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "added_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('lead', 'member')",
            name="team_members_role_check",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("team_id", "user_id"),
    )

    # 3. Add team_id nullable FK column to api_keys (ON DELETE SET NULL)
    op.add_column(
        "api_keys",
        sa.Column(
            "team_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "api_keys_team_id_fkey",
        "api_keys",
        "teams",
        ["team_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Drop team_id FK column from api_keys; drop team_members + teams tables."""
    # 1. Drop FK constraint and column from api_keys
    op.drop_constraint("api_keys_team_id_fkey", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "team_id")

    # 2. Drop team_members (references teams — must drop before teams)
    op.drop_table("team_members")

    # 3. Drop teams
    op.drop_table("teams")
