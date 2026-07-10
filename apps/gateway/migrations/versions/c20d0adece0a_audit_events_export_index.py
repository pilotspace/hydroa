"""audit_events_export_index — composite keyset index + actor_email index.

Revision ID: c20d0adece0a
Revises: 511ad8a7b65e
Create Date: 2026-07-10

Additive-only migration (compliance-export-api TASK.md §3 — FROZEN @ v1, §3
FREEZE-QUESTION 4 "as drafted": two narrower indexes, confirmed at BUILD via
EXPLAIN ANALYZE rather than by inspection alone):

  CREATE INDEX audit_events_tenant_created_id_idx
    ON audit_events (tenant_id, created_at, id);
  CREATE INDEX audit_events_actor_email_idx
    ON audit_events (actor_email);

Supports GET /admin/audit/export's keyset page query:
  SELECT ... WHERE tenant_id = :tid [AND actor_email = lower(:actor_email)]
                    [AND created_at >= :since] [AND created_at <= :until]
                    [AND (created_at, id) < (:cursor_created_at, :cursor_id)]
  ORDER BY created_at DESC, id DESC
  LIMIT :limit + 1

BUILD-TIME DEVIATION from the §3 migration sketch (recorded, strictly-more-correct +
harmless per the fix-and-record rule — see TASK.md §5): the sketch used
`sa.text("created_at DESC")` / `sa.text("id DESC")` expression columns. This migration
instead uses plain ASCENDING columns (tenant_id, created_at, id):
  - Postgres scans an ascending btree index BACKWARD at the same cost as scanning a
    DESC-declared index forward — `ORDER BY created_at DESC, id DESC` gets an identical
    index-only backward scan plan either way (confirmed via EXPLAIN ANALYZE at build,
    see TASK.md §5 for the captured plan).
  - Plain columns keep this migration and `AuditEventRow.__table_args__` (audit_events_
    orm.py) byte-for-byte comparable by `alembic check` / autogenerate — expression
    indexes (`sa.text(...)`) are a known source of noisy or silently-skipped autogenerate
    diffs, which would either break `tests/migrations/test_migrations.py::
    test_autogenerate_empty_diff` or (worse) silently mask a real future drift.
  - No FK, no data change — safe additive DDL either way; only the column-direction
    encoding differs, not the query plan semantics or the index name/scope.

Downgrade: DROP both indexes (safe — additive-only, nothing else references them).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c20d0adece0a"
down_revision: str | None = "511ad8a7b65e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the export keyset composite index + the actor_email filter index (additive)."""
    op.create_index(
        "audit_events_tenant_created_id_idx",
        "audit_events",
        ["tenant_id", "created_at", "id"],
    )
    op.create_index(
        "audit_events_actor_email_idx",
        "audit_events",
        ["actor_email"],
    )


def downgrade() -> None:
    """Drop both indexes (safe — additive-only, no other object depends on them)."""
    op.drop_index("audit_events_actor_email_idx", table_name="audit_events")
    op.drop_index("audit_events_tenant_created_id_idx", table_name="audit_events")
