"""SQLAlchemy ORM row for the request_logs table.

Schema mirrors §3 CONTRACT (payload-capture-store TASK.md, FROZEN @ v1):
  id               UUID PK
  tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
  key_id           UUID NOT NULL, no FK (append-only-ledger pattern, mirrors usage_records)
  team_id          UUID NULL, no FK (team deletion must not cascade, mirrors usage_records)
  model_id         TEXT NOT NULL
  status_code      INTEGER NOT NULL
  stream           BOOLEAN NOT NULL DEFAULT false
  cached           BOOLEAN NOT NULL DEFAULT false
  request_body     JSONB NULL   -- null when metadata-only (scrub failure / oversize fallback)
  response_body    JSONB NULL   -- null when metadata-only, or the call never reached upstream
  guardrail_verdict JSONB NULL  -- {blocked, pii_masked, patterns_hit, ...} — reserved,
                                 -- unpopulated in v1
  scrub_status     TEXT NOT NULL DEFAULT 'scrubbed'
  truncated        BOOLEAN NOT NULL DEFAULT false
  cost_usd         NUMERIC(14,8) NULL  -- DENORMALIZED DISPLAY SNAPSHOT ONLY, never billing truth
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now()

The write path (logs/application/capture_writer.py) uses a raw text() INSERT (mirrors
usage/application/alert_writer.py's own precedent) rather than this ORM object directly
— this class exists so Base.metadata (tests) and Alembic autogenerate both see the table,
and so a future read-side consumer (the sibling logs-explorer-api task) has a typed row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class RequestLogRow(Base):
    """ORM row for the request_logs table (payload-capture-store TASK.md §3, FROZEN @ v1)."""

    __tablename__ = "request_logs"
    __table_args__ = (
        Index("ix_request_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_request_logs_created_at", "created_at"),
        Index("ix_request_logs_tenant_key", "tenant_id", "key_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # No FK — deliberate append-only-ledger pattern, mirrors usage_records.key_id.
    key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # No FK — team deletion must not cascade into captured rows, mirrors usage_records.team_id.
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True, default=None)
    guardrail_verdict: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    scrub_status: Mapped[str] = mapped_column(Text, nullable=False, default="scrubbed")
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
