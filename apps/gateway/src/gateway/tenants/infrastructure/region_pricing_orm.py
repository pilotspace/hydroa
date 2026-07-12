"""ORM model for tenant_region_multiplier_overrides (region-pricing TASK.md §3
CONTRACT — FROZEN @ v1).

Per-(tenant, region) pricing-multiplier override layered on the DECIDED seed
(eu=1.1x, us/ap/global=1.0x — gateway.usage.application.rate_card_resolver.
resolve_region_multiplier). An ABSENT row for a (tenant, region) pair means
"fall back to the seed" — this table carries NO server_default and NO
backfill; the resolve rule lives entirely in the resolver.

No FK/enum check on `region` (TASK.md §1 assumption #4, mirrors
tenant_rate_card_entries' no-FK-on-model_id precedent) — an unrecognized
region silently resolves to the safe 1.0 default at READ time; validation
happens at WRITE time in the admin PUT route instead (region_pricing_router.py),
a deliberate deviation from the no-FK precedent for a money knob.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Index, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class TenantRegionMultiplierOverride(Base):
    """Per-(tenant, region) pricing-multiplier override row.

    UNIQUE(tenant_id, region) is the UPSERT conflict target for the admin
    PUT /admin/region-pricing/{region} endpoint (idempotent upsert — TASK.md
    §2 "Duplicate region override PUT is an idempotent upsert").
    """

    __tablename__ = "tenant_region_multiplier_overrides"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "region", name="uq_tenant_region_multiplier_overrides_tenant_region"
        ),
        CheckConstraint(
            "multiplier > 0", name="ck_tenant_region_multiplier_overrides_multiplier_positive"
        ),
        Index("ix_tenant_region_multiplier_overrides_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    region: Mapped[str] = mapped_column(Text, nullable=False)
    multiplier: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
