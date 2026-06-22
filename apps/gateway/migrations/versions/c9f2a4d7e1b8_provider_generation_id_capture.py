"""provider_generation_id_capture — store the provider's SSE generation id on a
client-disconnect usage_records row so disconnect cost-recovery (v30 t6) can look
up the authoritative cost later.

Revision ID: c9f2a4d7e1b8
Revises: b8e4f1a7c2d5
Create Date: 2026-06-22

Additive migration (provider-generation-id-capture TASK.md §3 DDL):

  usage_records:
    ADD COLUMN provider_generation_id TEXT NULL;

Nullable with NO default — NULL on every pre-existing row and every non-disconnect
caller; only a client-disconnect record (whose stream carried an `id`) populates it.
Adding a nullable column is instant on PostgreSQL (no table rewrite). Append-only
ledger preserved: the column is written at INSERT, never UPDATEd.

downgrade() drops the column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f2a4d7e1b8"
down_revision: str | None = "b8e4f1a7c2d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable provider_generation_id to usage_records (additive, instant)."""
    op.add_column(
        "usage_records",
        sa.Column("provider_generation_id", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Drop the additive column.

    Safe: additive-only; no existing data depends on it at migration time.
    """
    op.drop_column("usage_records", "provider_generation_id")
