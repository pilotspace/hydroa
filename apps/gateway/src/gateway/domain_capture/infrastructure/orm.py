"""TenantDomainClaimRow — the `tenant_domain_claims` table (domain-capture TASK.md §3 M1 —
FROZEN @ v1).

Two indexes:
  (a) plain UNIQUE (tenant_id, domain) — one claim row per tenant per domain, ever.
  (b) PARTIAL UNIQUE INDEX on domain WHERE status='verified' — the STRUCTURAL cross-tenant
      collision guard, mirrors TenantRow's own `tenants_platform_kind_uidx` precedent
      (tenants/infrastructure/orm.py) — enforced by Postgres itself, never an app-level
      SELECT-then-INSERT race (the exact TOCTOU class saml-sso's own verify pass flagged as
      still-open residue).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class TenantDomainClaimRow(Base):
    __tablename__ = "tenant_domain_claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'verified')", name="ck_tenant_domain_claims_status"
        ),
        Index(
            "uq_domain_claims_tenant_domain",
            "tenant_id",
            "domain",
            unique=True,
        ),
        Index(
            "uq_domain_claims_domain_verified",
            "domain",
            unique=True,
            postgresql_where=text("status = 'verified'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    domain: Mapped[str]
    verification_token: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id")
    )
