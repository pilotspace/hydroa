"""ORM model for tenant_priority_markup_overrides (service-tiers TASK.md §3
CONTRACT — FROZEN @ v1).

One row per tenant (UNIQUE tenant_id) — only the "priority" tier ever carries a
markup; "standard" is definitionally the zero-markup baseline (never overridable,
§3 M11). An ABSENT row means "fall back to the DECIDED seed" (+25%,
gateway.usage.application.rate_card_resolver.resolve_tier_multiplier) — mirrors
tenant_region_multiplier_overrides' own absent-row-means-seed convention.

markup_pct uses Numeric(7,4) — the PERCENTAGE-ADDITIVE convention (mirrors
tenants.markup_pct / tenant_rate_card_entries.markup_pct), deliberately NOT
region-pricing's raw-multiplier Numeric(6,4) convention (region-pricing TASK.md
§1 assumption #3's asymmetry, cited verbatim by this task's own §0 GROUND).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class TenantPriorityMarkupOverride(Base):
    """Per-tenant priority-tier markup override row.

    UNIQUE(tenant_id) is the UPSERT conflict target for
    PUT /admin/service-tiers/priority-markup (idempotent upsert — TASK.md §2
    "Duplicate priority-markup PUT is an idempotent upsert").
    """

    __tablename__ = "tenant_priority_markup_overrides"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_tenant_priority_markup_overrides_tenant"),
        CheckConstraint(
            "markup_pct >= 0", name="ck_tenant_priority_markup_overrides_markup_nonneg"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    markup_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
