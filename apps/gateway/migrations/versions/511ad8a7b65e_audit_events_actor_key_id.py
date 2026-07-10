"""audit_events.actor_key_id — key-actor scoping for the audit invariant relaxation.

Revision ID: 511ad8a7b65e
Revises: f70104c27b41
Create Date: 2026-07-10

Additive nullable-column migration (realtime-relay-governance TASK.md §3 — FROZEN @ v1,
Option B, Tin's frozen choice at 2026-07-10):
  ALTER TABLE audit_events ADD COLUMN actor_key_id UUID NULL;

Sanctions a scoped change-request against the FROZEN audit-log-store contract: a
tenant-scoped AuditEvent (tenant_id is not None) is now valid if it carries EITHER
actor_user_id OR actor_key_id (relaxed audit_missing_actor invariant, see
gateway.audit.domain.audit_event.AuditEvent.__post_init__). Every existing user-actor
event is unaffected — actor_key_id stays NULL for those rows.

No FK to api_keys(id): mirrors the existing audit_events.tenant_id pattern (no FK to
tenants(id) either, per audit_events_orm.py's own note) so Base.metadata.create_all in
tests does not enforce a constraint requiring a matching api_keys row, and so a revoked/
deleted key's historical audit rows are never blocked by a FK.

Downgrade: DROP COLUMN actor_key_id (safe — additive, no other column/constraint
references it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "511ad8a7b65e"
down_revision: str | None = "f70104c27b41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable actor_key_id UUID column to audit_events (additive)."""
    op.add_column(
        "audit_events",
        sa.Column("actor_key_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Drop actor_key_id (safe — additive, no existing rows depend on it)."""
    op.drop_column("audit_events", "actor_key_id")
