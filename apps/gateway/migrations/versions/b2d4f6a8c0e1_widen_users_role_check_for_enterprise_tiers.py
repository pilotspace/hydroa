"""widen_users_role_check — add operator/billing_admin/viewer to users.role CHECK constraint.

Revision ID: b2d4f6a8c0e1
Revises: c3e5b7a9f1d2
Create Date: 2026-06-25

Context (rbac-roles TASK.md §3 CONTRACT — FROZEN @ v1):
  The baseline migration (ad14442336db) created users with:
    CHECK(role IN ('owner', 'admin', 'member'))
  Enterprise RBAC adds three new tiers: operator, billing_admin, viewer.
  This migration widens the constraint so new role values can be persisted.

Strategy:
  DROP the old constraint → ADD the widened constraint in one transaction.
  Both ops are metadata-only (no table rewrite on PostgreSQL).
  Old rows carry one of {owner, admin, member} — they satisfy the new constraint too.

downgrade() restores the original three-value constraint.
  Any rows with new role values must be removed / migrated before downgrading,
  otherwise the re-added constraint will fail validation.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2d4f6a8c0e1"
down_revision: str | None = "c3e5b7a9f1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = "'owner', 'admin', 'member'"
_NEW_VALUES = "'owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member'"
_CONSTRAINT_NAME = "users_role_check"
_TABLE = "users"
_COL = "role"


def upgrade() -> None:
    """Widen users.role CHECK to include the three enterprise tiers."""
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        f"{_COL} IN ({_NEW_VALUES})",
    )


def downgrade() -> None:
    """Restore the original three-value CHECK constraint.

    WARNING: rows with role IN ('operator', 'billing_admin', 'viewer') will violate
    the restored constraint.  Remove or update such rows before running downgrade.
    """
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        f"{_COL} IN ({_OLD_VALUES})",
    )
