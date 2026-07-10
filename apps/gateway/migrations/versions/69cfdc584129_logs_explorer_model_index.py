"""logs_explorer_model_index — additive index-only migration for GET /admin/logs.

Revision ID: 69cfdc584129
Revises: a55ddcebaac6
Create Date: 2026-07-10

logs-explorer-api TASK.md §3 (FROZEN @ v1) — pure index addition, no column/table-shape
change to the FROZEN request_logs contract (payload-capture-store TASK.md §3, extended by
request-log-metering-fields TASK.md §3). `model_id` is the single highest-value filter for
a "debug this model's calls" workflow (the milestone brief names it explicitly) — mitigates
a narrow filter combined with a wide/unbounded time range forcing the keyset scan to walk
many non-matching index-ordered rows before satisfying `limit`. The bounded asyncio.timeout
on the list query (ERR_LOGS_QUERY_TIMEOUT) is the backstop for every other filter
combination — an honest 504 under load, never an unbounded scan.

  CREATE INDEX ix_request_logs_tenant_model_created
    ON request_logs (tenant_id, model_id, created_at);
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "69cfdc584129"
down_revision: str | None = "b7c9e1a3f5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the single additive composite index this task's model_id filter relies on."""
    op.create_index(
        "ix_request_logs_tenant_model_created",
        "request_logs",
        ["tenant_id", "model_id", "created_at"],
    )


def downgrade() -> None:
    """Drop the additive index (symmetric, reversible; no column ever touched)."""
    op.drop_index("ix_request_logs_tenant_model_created", table_name="request_logs")
