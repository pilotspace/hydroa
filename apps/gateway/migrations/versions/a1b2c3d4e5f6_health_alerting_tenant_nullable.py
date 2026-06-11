"""health_alerting_tenant_nullable — alert_events.tenant_id DROP NOT NULL + DROP FK

Revision ID: a1b2c3d4e5f6
Revises: f4a9b3c7e8d2
Create Date: 2026-06-11

Additive migration (M15 from health-alerting TASK.md §1, amended at orchestrator review):
  ALTER TABLE alert_events ALTER COLUMN tenant_id DROP NOT NULL;
  ALTER TABLE alert_events DROP CONSTRAINT alert_events_tenant_id_fkey;

Rationale:
  System events (circuit_breaker_open, drain_timeout, upstream_health_*) have no tenant
  context — NULL, not a sentinel UUID. The tenant FK is dropped IN BOTH the migration
  and the ORM (orchestrator amendment): alert_events is an event LOG — rows must survive
  tenant deletion for audit and inserts must never fail on referential races. Keeping the
  FK only in migrations while hiding it from the ORM would break the v2 schema-parity
  guarantee (ORM metadata ≡ migrated schema, asserted in CI).

Downgrade caveat:
  The reverse re-adds the FK and NOT NULL. Only safe when no NULL-tenant rows and no
  rows referencing deleted tenants exist; the guard below fails loudly otherwise.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f4a9b3c7e8d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """DROP NOT NULL + DROP FK on alert_events.tenant_id (event-log semantics)."""
    op.alter_column("alert_events", "tenant_id", nullable=True)
    op.drop_constraint("alert_events_tenant_id_fkey", "alert_events", type_="foreignkey")


def downgrade() -> None:
    """Re-add the FK and NOT NULL on alert_events.tenant_id.

    WARNING: Only safe when no rows with tenant_id IS NULL exist and every
    non-NULL tenant_id still references a live tenant row.
    """
    # Fail explicitly if NULL rows exist rather than silently violating the constraint
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM alert_events WHERE tenant_id IS NULL) THEN "
            "RAISE EXCEPTION 'Cannot downgrade: alert_events has rows with tenant_id IS NULL. "
            "Delete or migrate system-event rows first.'; "
            "END IF; "
            "END $$"
        )
    )
    op.create_foreign_key(
        "alert_events_tenant_id_fkey",
        "alert_events",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    op.alter_column("alert_events", "tenant_id", nullable=False)
