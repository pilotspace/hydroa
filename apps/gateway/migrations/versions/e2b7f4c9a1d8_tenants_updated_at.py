"""tenants_updated_at — add updated_at column to tenants table.

Revision ID: e2b7f4c9a1d8
Revises: d8f3a1c9e5b2
Create Date: 2026-06-16

Additive migration: the tenants table was created in the baseline (ad14442336db)
without an updated_at column.  This migration adds it with server_default=now()
to backfill all existing rows and match the ORM declaration.

Migration chain: d8f3a1c9e5b2 (tenant-provider-keys) → e2b7f4c9a1d8 (tenants-updated-at)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e2b7f4c9a1d8"
down_revision: str | None = "d8f3a1c9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add updated_at to tenants with server_default=now(); backfills existing rows."""
    op.add_column(
        "tenants",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    """Drop updated_at from tenants.

    Safe: additive-only; no existing logic depends on this column at migration time.
    """
    op.drop_column("tenants", "updated_at")
