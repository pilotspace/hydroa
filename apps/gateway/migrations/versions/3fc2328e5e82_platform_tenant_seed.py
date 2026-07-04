"""platform_tenant_seed — add tenants.kind discriminator + seed the reserved platform tenant.

Revision ID: 3fc2328e5e82
Revises: 326b927cf8c2
Create Date: 2026-07-02

Additive migration: adds tenants.kind (TEXT NOT NULL DEFAULT 'customer', CHECK IN
('customer', 'platform')) so existing rows backfill to 'customer', a partial unique index
enforcing at most one kind='platform' row, and idempotently seeds that one reserved row
(id via uuid7(), name='Platform', no owner user). See TASK.md platform-tenant-seed §3.

Migration chain: 326b927cf8c2 (merge gpt-realtime/model-presets heads) -> 3fc2328e5e82
(platform-tenant-seed)
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from gateway.core.ids import uuid7

# revision identifiers, used by Alembic.
revision: str = "3fc2328e5e82"
down_revision: str | None = "326b927cf8c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add tenants.kind + partial unique index; seed the one platform-kind row."""
    op.add_column(
        "tenants",
        sa.Column("kind", sa.Text(), nullable=False, server_default="customer"),
    )
    op.create_check_constraint(
        "ck_tenants_kind",
        "tenants",
        "kind IN ('customer', 'platform')",
    )
    op.create_index(
        "tenants_platform_kind_uidx",
        "tenants",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'platform'"),
    )
    # ON CONFLICT's predicate must match the partial index's predicate verbatim, or
    # Postgres won't recognize it as the arbiter index and this INSERT would 23505 on replay.
    op.execute(
        sa.text(
            "INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform') "
            "ON CONFLICT (kind) WHERE kind = 'platform' DO NOTHING"
        ).bindparams(id=uuid7())
    )


def downgrade() -> None:
    """Reverse in dependency order: delete the seeded row while kind/index still exist."""
    op.execute("DELETE FROM tenants WHERE kind = 'platform'")
    op.drop_index("tenants_platform_kind_uidx", table_name="tenants")
    op.drop_column("tenants", "kind")
