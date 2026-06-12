"""SQLAlchemy ORM row for the usage_records append-only ledger.

Schema contract (FROZEN @ v1 — TASK.md §3):
  usage_records (
    id                  uuid        PRIMARY KEY,         -- Redis stream event id
    tenant_id           uuid        NOT NULL,
    key_id              uuid        NOT NULL,
    model_id            text        NOT NULL,
    prompt_tokens       int         NOT NULL DEFAULT 0,
    completion_tokens   int         NOT NULL DEFAULT 0,
    cost_usd            numeric(14,8) NOT NULL DEFAULT 0,
    status              int         NOT NULL,
    pricing_snapshot_id uuid        NULL,
    raw                 jsonb       NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    team_id             uuid        NULL          -- team-attribution (team-attribution TASK.md §3)
  )

APPEND-ONLY: no UPDATE or DELETE ever issued against this table.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class UsageRecordRow(Base):
    """Append-only usage ledger row.

    id = Redis stream event id (converted to UUID from the millisecond-timestamp
    format "1234567890123-0" by stripping the hyphen and padding to 128 bits).
    The exact conversion used by the flusher must remain consistent across
    restarts — see infrastructure/redis_stream.py stream_id_to_uuid().
    """

    __tablename__ = "usage_records"
    # Composite index for team-rollup queries: tenant_id first (all queries are
    # tenant-scoped), then team_id for efficient per-team aggregation.
    __table_args__ = (Index("ix_usage_records_tenant_team", "tenant_id", "team_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, server_default="0")
    status: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    raw: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    # team-attribution: nullable — no FK (append-only ledger; team deletion must not cascade)
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    # pricing-units (pricing-units TASK.md §3): discriminator + billed quantity
    pricing_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="per_token")
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
