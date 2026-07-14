"""usage_records_export_index — additive index-only migration for the Art. 12 bundle's
usage_lineage section.

Revision ID: 776ecf702f3f
Revises: 4583689a7b8b
Create Date: 2026-07-14

art12-record-keeping-preset TASK.md §3 (FROZEN @ v1) — pure index addition, no column/table-
shape change to usage_records. Backs the new UsageRepository.list_for_tenant_keyset's
keyset predicate over (tenant_id, created_at DESC, id DESC) — mirrors
ix_request_logs_tenant_created / the audit_events keyset precedent (both already scoped by
tenant_id first, then the ordering columns). Without this, the FIRST bounded/paginated read
over usage_records for an arbitrary period would keyset-walk the table using only the
existing ix_usage_records_created_at (no tenant_id) or ix_usage_records_tenant_team (no
created_at/id ordering) — neither backs this exact predicate.

  CREATE INDEX usage_records_tenant_created_id_idx
    ON usage_records (tenant_id, created_at DESC, id DESC);
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "776ecf702f3f"
down_revision: str | None = "9cb98362515f"  # re-parented onto identity head at R1 integration
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the single additive composite index the usage_lineage keyset query relies on."""
    op.create_index(
        "usage_records_tenant_created_id_idx",
        "usage_records",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
    )


def downgrade() -> None:
    """Drop the additive index (symmetric, reversible; no column ever touched)."""
    op.drop_index("usage_records_tenant_created_id_idx", table_name="usage_records")
