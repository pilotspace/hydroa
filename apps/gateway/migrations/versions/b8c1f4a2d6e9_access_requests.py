"""access_requests — the store-only request-access capture table
(signup-refusal-router TASK.md §3 DDL — FROZEN @ v1, SECURITY).

Revision ID: b8c1f4a2d6e9
Revises: a4f2d9c17b3e
Create Date: 2026-07-20

Additive migration — ONE new table, no existing table altered:

  TABLE access_requests: {id, email, domain, created_at} ONLY. Deliberately no
  tenant_id/owner FK (§3 Schema, verbatim) — resolving-and-storing an owner at write time
  would itself become the enumeration signal this task exists to avoid (R-sec-2). No
  status/handled_at column — owner-visible triage is an explicit OPEN follow-on, not
  silently assumed to exist.

FROZEN and UNTOUCHED by this migration: every existing table, resolve_verified_tenant,
the S1 public_signup_enabled gate.

Downgrade: DROP the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c1f4a2d6e9"
# Re-chained during wave-1 merge: both this (access_requests) and
# b8e1c4f2a9d6 (pending_personal_signups) were authored in parallel worktrees off the
# same parent a4f2d9c17b3e, producing two alembic heads. Linearized onto the pending-
# signups migration so `alembic heads` resolves to a single head. Both are purely
# additive (new tables), so ordering is immaterial to correctness.
down_revision: str | None = "b8e1c4f2a9d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the access_requests table."""
    op.create_table(
        "access_requests",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    """Drop the access_requests table."""
    op.drop_table("access_requests")
