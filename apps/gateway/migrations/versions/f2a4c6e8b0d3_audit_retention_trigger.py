"""audit_retention_trigger — replace RULE immutability with TRIGGER + created_at indexes.

Revision ID: f2a4c6e8b0d3
Revises: e3f5a7c9b1d2
Create Date: 2026-06-25

Replaces the two PostgreSQL RULEs on audit_events (audit_events_no_update,
audit_events_no_delete) with a BEFORE UPDATE OR DELETE TRIGGER that:
  - UPDATE: always RAISES 'audit_immutable_violation' (no bypass possible — audit rows
    are truly immutable, no legitimate UPDATE path exists).
  - DELETE: RAISES 'audit_immutable_violation' UNLESS the transaction-scoped GUC
    current_setting('app.audit_purge', true) = 'on'. This allows the RetentionSweeper
    to purge aged rows by setting SET LOCAL app.audit_purge = 'on' in its own
    transaction, while ordinary application code (which never sets this GUC) is still
    blocked.

RATIONALE for moving from RULE to TRIGGER:
  - RULEs are a PostgreSQL-specific extension that silently rewrite queries (DO INSTEAD
    NOTHING). They suppress the error, making immutability violations invisible — a row
    update attempt returns 200 to the caller with no indication of the block.
  - TRIGGERS fire BEFORE the DML and RAISE an exception, surfacing violations clearly.
    The exception propagates to the application layer, ensuring no silent mutations.
  - TRIGGERS also support the GUC-based bypass needed for the RetentionSweeper to
    execute legitimate audit purges without weakening the immutability guarantee for
    all other callers.

Also adds created_at indexes on usage_records and alert_events to support the
RetentionSweeper's DELETE ... WHERE created_at < :cutoff queries efficiently.

Downgrade restores the original RULE-based immutability (drops trigger + function,
recreates the two RULEs).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2a4c6e8b0d3"
down_revision: str | None = "e3f5a7c9b1d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# ---------------------------------------------------------------------------
# Trigger function body (extracted as a constant for readability + downgrade reuse)
# ---------------------------------------------------------------------------

_TRIGGER_FN_SQL = """\
CREATE OR REPLACE FUNCTION audit_events_immutable_guard_fn() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'audit_immutable_violation: audit_events rows are immutable';
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF current_setting('app.audit_purge', true) = 'on' THEN
      RETURN OLD;
    END IF;
    RAISE EXCEPTION 'audit_immutable_violation: audit_events rows cannot be '
                    'deleted without audit_purge bypass';
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_TRIGGER_SQL = """\
CREATE TRIGGER audit_events_immutable_guard
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_immutable_guard_fn()
"""


def upgrade() -> None:
    """Replace RULE-based immutability with TRIGGER + add retention indexes."""
    # ── 1. Drop the old RULE-based immutability guards ──────────────────────
    # Must drop before creating the trigger so there is no conflict / double-block.
    op.execute("DROP RULE IF EXISTS audit_events_no_update ON audit_events")
    op.execute("DROP RULE IF EXISTS audit_events_no_delete ON audit_events")

    # ── 2. Create the immutability trigger function ──────────────────────────
    op.execute(_TRIGGER_FN_SQL)

    # ── 3. Create the BEFORE UPDATE OR DELETE trigger ────────────────────────
    op.execute(_TRIGGER_SQL)

    # ── 4. Add created_at indexes for RetentionSweeper DELETE efficiency ─────
    # usage_records: the DELETE ... WHERE created_at < :cutoff LIMIT :batch query
    # runs a full-table scan without this index on a large ledger.
    # Using op.create_index (not raw SQL) so Alembic autogenerate tracks these
    # and the ORM __table_args__ entries stay in sync (zero autogenerate diff).
    op.create_index("ix_usage_records_created_at", "usage_records", ["created_at"])

    # alert_events: same pattern — purge by age.
    op.create_index("ix_alert_events_created_at", "alert_events", ["created_at"])

    # audit_events already has audit_events_tenant_created_idx (tenant_id, created_at)
    # which covers the RetentionSweeper's cutoff query (leading tenant_id=NULL rows
    # plus a full-scan-by-age). No additional index needed.


def downgrade() -> None:
    """Restore RULE-based immutability, drop trigger and retention indexes."""
    # ── 1. Drop the trigger first (depends on the function) ──────────────────
    op.execute("DROP TRIGGER IF EXISTS audit_events_immutable_guard ON audit_events")

    # ── 2. Drop the trigger function ─────────────────────────────────────────
    op.execute("DROP FUNCTION IF EXISTS audit_events_immutable_guard_fn()")

    # ── 3. Restore the original RULE-based immutability ──────────────────────
    op.execute("CREATE RULE audit_events_no_update AS ON UPDATE TO audit_events DO INSTEAD NOTHING")
    op.execute("CREATE RULE audit_events_no_delete AS ON DELETE TO audit_events DO INSTEAD NOTHING")

    # ── 4. Drop retention indexes ─────────────────────────────────────────────
    op.drop_index("ix_usage_records_created_at", table_name="usage_records")
    op.drop_index("ix_alert_events_created_at", table_name="alert_events")
