"""ORM model for tenant_rate_card_entries (tiered-rate-cards TASK.md §3 CONTRACT — FROZEN @ v1).

Per-(tenant, model) markup override layered on `tenants.markup_pct`. An ABSENT
row for a (tenant, model) pair means "fall back to the tenant's flat markup" —
this table carries NO server_default and NO backfill; the resolve rule lives
in `gateway.usage.application.rate_card_resolver.resolve_markup_pct`.

No FK to `models.id` — a markup override may target a model_id absent from
the catalog (it simply resolves once the model is billed/listed; TASK.md §1
assumption, deliberately NOT rejected as unknown_model).
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


class TenantRateCardEntry(Base):
    """Per-(tenant, model) markup override row.

    UNIQUE(tenant_id, model_id) is the UPSERT conflict target for the admin
    PUT /admin/rate-cards/{model_id} endpoint (idempotent upsert — TASK.md §2
    "Duplicate (tenant, model) create is an idempotent upsert").
    """

    __tablename__ = "tenant_rate_card_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "model_id", name="uq_tenant_rate_card_entries_tenant_model"),
        CheckConstraint("markup_pct >= 0", name="ck_tenant_rate_card_entries_markup_pct_nonneg"),
        Index("ix_tenant_rate_card_entries_tenant_id", "tenant_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    markup_pct: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
