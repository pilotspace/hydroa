"""AccessRequestRow — the `access_requests` table (signup-refusal-router TASK.md §3 —
FROZEN @ v1).

Deliberately minimal (§3 Schema, verbatim): id/email/domain/created_at only. No FK to
tenants/users/domain claims — mirrors TenantDomainClaimRow's own module docstring
discipline in spirit, but the ABSENCE of any tenant/owner FK here is itself the point
(R-sec-2): resolving-and-storing an owner at write time would become the enumeration
signal this task exists to avoid.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class AccessRequestRow(Base):
    __tablename__ = "access_requests"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    email: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
