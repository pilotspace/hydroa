"""SQLAlchemy ORM row for the alert_events table.

Schema contract (FROZEN @ spend-windows — TASK.md §3):
  alert_events (
    id            UUID         PRIMARY KEY,
    tenant_id     UUID         NOT NULL FK(tenants.id) ON DELETE CASCADE,
    key_id        UUID         NULL,
    event_type    TEXT         NOT NULL,
    payload       JSONB        NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    delivered_at  TIMESTAMPTZ  NULL,
    dedupe_key    TEXT         NOT NULL UNIQUE
  )

CROSS-TASK FREEZE: dedupe_key UNIQUE + payload JSONB schema for soft_budget_exceeded.
Health-alerting extends this table with additive columns; this ORM is the minimal DDL owner.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, Text, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class AlertEventRow(Base):
    """Minimal alert event row — owned by spend-windows, extended by health-alerting."""

    __tablename__ = "alert_events"

    # Partial index for efficient undelivered query (health-alerting uses this).
    # Declared in __table_args__ so Alembic autogenerate includes it in metadata comparison.
    # postgresql_where must be a sqlalchemy text() expression (not the Text column type).
    __table_args__ = (
        Index(
            "alert_events_undelivered_idx",
            "created_at",
            postgresql_where=sa_text("delivered_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    key_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
