"""SQLAlchemy ORM row for checkout_sessions (self-serve-checkout TASK.md §3, FROZEN @ v1).

The seam's OWN persisted state (I5): a create->confirm attempt must be persisted to be
confirmable, idempotent, and tenant-scoped-on-lookup. NO card / PAN / CVV / cardholder /
PII columns (M6) — only an opaque `provider_ref` (a Stripe hosted-checkout session id, or
NULL for dev). Every query filters WHERE tenant_id = identity.tenant_id; confirm takes a
SELECT ... FOR UPDATE on the row and flips pending->succeeded in ONE transaction (M3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class CheckoutSessionRow(Base):
    """One create->confirm checkout attempt. One-way status; no card/PII data."""

    __tablename__ = "checkout_sessions"

    __table_args__ = (
        # M4: idempotent create — a repeated (tenant_id, idempotency_key) returns the
        # EXISTING session, never a second one. DB-enforced, not just app logic.
        Index(
            "checkout_sessions_tenant_idem_uq",
            "tenant_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    actor_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # 'dev' | 'stripe'
    intent_type: Mapped[str] = mapped_column(Text, nullable=False)  # 'plan_upgrade'|'credit_topup'
    intent_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # pending|succeeded|failed|expired
    provider_ref: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    applied_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
