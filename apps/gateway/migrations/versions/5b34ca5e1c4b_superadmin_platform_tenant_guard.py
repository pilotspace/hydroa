"""superadmin_platform_tenant_guard — widen users.role CHECK to 7 values + add the
platform-tenant guard trigger for role='superadmin'.

Revision ID: 5b34ca5e1c4b
Revises: 3fc2328e5e82
Create Date: 2026-07-03

Context (superadmin-role TASK.md §3 CONTRACT — FROZEN @ v1):
  Adds Role.SUPERADMIN, a platform-tenant-only role whose authz check may target any
  tenant_id (see gateway.tenants.domain.authz.authorize_tenant_scope). This migration
  provides the two DB-level primitives the new role needs:

    1. Widen users_role_check (mirrors b2d4f6a8c0e1's drop/create strategy) so
       role='superadmin' can be persisted at all.
    2. Add a BEFORE INSERT OR UPDATE trigger (mirrors f2a4c6e8b0d3's function+trigger
       strategy) enforcing the cross-table invariant a CHECK constraint cannot express:
       a superadmin row's tenant_id must be the sole kind='platform' tenant (added by
       platform-tenant-seed, 3fc2328e5e82). This is the PRIMARY enforcement of the
       invariant — independent of any application code path (see TASK.md §1 framing);
       the AssignUserRoleUseCase guard is a second, application-layer backstop only.

  The trigger has no column restriction (FOR EACH ROW, not "OF role"), so an UPDATE
  that only touches tenant_id on an existing superadmin row is caught too, not just a
  role-column UPDATE.

Strategy:
  Both the CHECK-widen and the trigger creation are metadata/DDL-only (no table
  rewrite on PostgreSQL). Old rows carry one of the 6 pre-existing role values —
  they satisfy the widened constraint and never trip the trigger (which only
  inspects rows where NEW.role = 'superadmin').

downgrade() reverses in the safe dependency order: drop the trigger, then the
function (mirrors f2a4c6e8b0d3), then revert the CHECK to 6 values (mirrors
b2d4f6a8c0e1).

WARNING: any row with role='superadmin' will violate the restored 6-value CHECK.
Remove or reassign such rows before running downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5b34ca5e1c4b"
down_revision: str | None = "3fc2328e5e82"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_VALUES = "'owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member'"
_NEW_VALUES = "'owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member', 'superadmin'"
_CONSTRAINT_NAME = "users_role_check"
_TABLE = "users"
_COL = "role"

# ---------------------------------------------------------------------------
# Trigger function body (extracted as a constant for readability + downgrade reuse)
# ---------------------------------------------------------------------------

_TRIGGER_FN_SQL = """\
CREATE OR REPLACE FUNCTION users_superadmin_platform_tenant_guard_fn() RETURNS trigger AS $$
BEGIN
  IF NEW.role = 'superadmin' THEN
    IF NOT EXISTS (
      SELECT 1 FROM tenants WHERE id = NEW.tenant_id AND kind = 'platform'
    ) THEN
      RAISE EXCEPTION 'superadmin_requires_platform_tenant: role=superadmin is only '
                      'permitted for the platform tenant';
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

_TRIGGER_SQL = """\
CREATE TRIGGER users_superadmin_platform_tenant_guard
BEFORE INSERT OR UPDATE ON users
FOR EACH ROW EXECUTE FUNCTION users_superadmin_platform_tenant_guard_fn()
"""


def upgrade() -> None:
    """Widen users.role CHECK to 7 values + add the platform-tenant guard trigger."""
    # ── 1. Widen the CHECK constraint (mirrors b2d4f6a8c0e1) ──────────────────
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        f"{_COL} IN ({_NEW_VALUES})",
    )

    # ── 2. Create the platform-tenant guard trigger function ──────────────────
    op.execute(_TRIGGER_FN_SQL)

    # ── 3. Create the BEFORE INSERT OR UPDATE trigger ──────────────────────────
    op.execute(_TRIGGER_SQL)


def downgrade() -> None:
    """Reverse in the safe dependency order: trigger -> function -> CHECK.

    WARNING: rows with role='superadmin' will violate the restored 6-value
    constraint. Remove or reassign such rows before running downgrade.
    """
    # ── 1. Drop the trigger first (depends on the function) ───────────────────
    op.execute("DROP TRIGGER IF EXISTS users_superadmin_platform_tenant_guard ON users")

    # ── 2. Drop the trigger function ──────────────────────────────────────────
    op.execute("DROP FUNCTION IF EXISTS users_superadmin_platform_tenant_guard_fn()")

    # ── 3. Revert the CHECK constraint to 6 values ─────────────────────────────
    op.drop_constraint(_CONSTRAINT_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        _TABLE,
        f"{_COL} IN ({_OLD_VALUES})",
    )
