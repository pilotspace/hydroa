"""recovery_sweep_index — partial index on usage_records (provider_generation_id,
usage_source) to support the periodic OpenRouter cost-recovery sweep (v30 t6.3).

Revision ID: d1e2f3a4b5c6
Revises: c9f2a4d7e1b8
Create Date: 2026-06-22

Additive index (openrouter-recovery-sweep TASK.md §3 DDL):

  CREATE INDEX ix_usage_records_gen_recovery
    ON usage_records (provider_generation_id, usage_source)
    WHERE provider_generation_id IS NOT NULL;

The sweep scans for client_disconnect rows carrying a generation id that have NO
openrouter_recovered sibling (a self NOT-EXISTS on provider_generation_id +
usage_source). The partial predicate keeps the index tiny — only rows that ever
carry a generation id (a small fraction of the append-only ledger) are indexed.

downgrade() drops the index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c9f2a4d7e1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the partial index supporting the recovery-sweep candidate scan."""
    op.create_index(
        "ix_usage_records_gen_recovery",
        "usage_records",
        ["provider_generation_id", "usage_source"],
        unique=False,
        postgresql_where="provider_generation_id IS NOT NULL",
    )


def downgrade() -> None:
    """Drop the additive index. Safe: additive-only, no data depends on it."""
    op.drop_index("ix_usage_records_gen_recovery", table_name="usage_records")
