"""audit_events — append-only admin audit event store (audit-log-store TASK.md §3).

Revision ID: e3f5a7c9b1d2
Revises: b2d4f6a8c0e1
Create Date: 2026-06-25

Creates the audit_events table (append-only; DB-enforced immutability via PostgreSQL rules):
  id            UUID         PRIMARY KEY
  tenant_id     UUID         NULL            (NULL for system-level events)
  actor_user_id UUID         NULL            (NULL only for system events)
  actor_email   TEXT         NULL
  action        TEXT         NOT NULL        (dotted verb: "routing.update", "key.create", ...)
  target_type   TEXT         NULL
  target_id     TEXT         NULL
  result        TEXT         NOT NULL        ("success" | "denied" | "error")
  metadata      JSONB        NOT NULL
  created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()

IMMUTABILITY (Decision A — DB-enforced, approved by Tin 2026-06-25):
  Two PostgreSQL RULEs block UPDATE and DELETE at the DB engine level:
    CREATE RULE audit_events_no_update AS ON UPDATE TO audit_events DO INSTEAD NOTHING;
    CREATE RULE audit_events_no_delete AS ON DELETE TO audit_events DO INSTEAD NOTHING;
  These rules fire INSTEAD of the actual UPDATE/DELETE, making them silent no-ops.
  Any code that attempts to mutate audit rows is silently ignored by the DB.

INDEX: audit_events_tenant_created_idx (tenant_id, created_at DESC) for newest-first reads.

Downgrade: drops the rules first (so the DROP TABLE can proceed), then drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e3f5a7c9b1d2"
down_revision: str | None = "b2d4f6a8c0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create audit_events table with DB-enforced immutability (rules + index)."""
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=True),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("result", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # Create index (tenant_id, created_at ASC) — ORM and migration must agree on direction
    # so autogenerate stays clean. Newest-first ORDER BY in queries still works with ASC.
    op.create_index(
        "audit_events_tenant_created_idx",
        "audit_events",
        ["tenant_id", "created_at"],
    )

    # DB-enforced immutability: RULEs that silently no-op any UPDATE or DELETE.
    # These fire INSTEAD of the actual DML, making any mutation attempt a silent no-op.
    # Verified by test_db_enforced_immutability.
    op.execute(
        "CREATE RULE audit_events_no_update AS "
        "ON UPDATE TO audit_events DO INSTEAD NOTHING"
    )
    op.execute(
        "CREATE RULE audit_events_no_delete AS "
        "ON DELETE TO audit_events DO INSTEAD NOTHING"
    )


def downgrade() -> None:
    """Drop immutability rules, index, and table."""
    # Rules must be dropped before the table (CASCADE would also drop them, but explicit is safer)
    op.execute("DROP RULE IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP RULE IF EXISTS audit_events_no_delete ON audit_events")
    op.drop_index("audit_events_tenant_created_idx", table_name="audit_events")
    op.drop_table("audit_events")
