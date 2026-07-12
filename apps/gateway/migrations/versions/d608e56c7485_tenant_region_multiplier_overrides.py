"""tenant_region_multiplier_overrides — per-(tenant, region) pricing-multiplier
override table.

Revision ID: d608e56c7485
Revises: c78ed31d0a4d
Create Date: 2026-07-12

Additive migration (region-pricing TASK.md §3 DDL — FROZEN @ v1):
  CREATE TABLE tenant_region_multiplier_overrides (
    id          UUID          PRIMARY KEY,
    tenant_id   UUID          NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    region      TEXT          NOT NULL,
    multiplier  NUMERIC(6,4)  NOT NULL,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, region),
    CHECK (multiplier > 0)
  );
  CREATE INDEX ix_tenant_region_multiplier_overrides_tenant_id
    ON tenant_region_multiplier_overrides (tenant_id);

No FK/enum check on `region` (TASK.md §1 assumption #4 — mirrors
tenant_rate_card_entries' no-FK-on-model_id precedent): an unrecognized region
silently resolves to the safe 1.0 default at RESOLVE time
(rate_card_resolver.resolve_region_multiplier); the admin PUT route validates
`region` against the frozen us|eu|ap|global Literal at WRITE time instead
(region_pricing_router.py) — a deliberate deviation from the no-FK precedent
because a silent no-op on a money knob is a foot-gun (TASK.md DECIDED #3).

No server_default / no backfill: an ABSENT row means "fall back to the
DECIDED seed" (eu=1.1x, us/ap/global=1.0x) — the resolve rule lives entirely
in gateway.usage.application.rate_card_resolver.resolve_region_multiplier.

Downgrade: DROP TABLE tenant_region_multiplier_overrides (safe — additive, no
existing rows reference it).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d608e56c7485"
down_revision: str | None = "c78ed31d0a4d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenant_region_multiplier_overrides table with UNIQUE(tenant_id, region)."""
    op.create_table(
        "tenant_region_multiplier_overrides",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("region", sa.Text(), nullable=False),
        sa.Column("multiplier", sa.Numeric(6, 4), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "region", name="uq_tenant_region_multiplier_overrides_tenant_region"
        ),
        sa.CheckConstraint(
            "multiplier > 0", name="ck_tenant_region_multiplier_overrides_multiplier_positive"
        ),
    )
    op.create_index(
        "ix_tenant_region_multiplier_overrides_tenant_id",
        "tenant_region_multiplier_overrides",
        ["tenant_id"],
    )


def downgrade() -> None:
    """Drop tenant_region_multiplier_overrides table (safe — additive, no existing
    rows reference it)."""
    op.drop_index(
        "ix_tenant_region_multiplier_overrides_tenant_id",
        table_name="tenant_region_multiplier_overrides",
    )
    op.drop_table("tenant_region_multiplier_overrides")
