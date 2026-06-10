"""SQLAlchemy ORM rows for the catalog module."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class ModelRow(Base):
    """Persisted catalog model entry.

    id is the OpenRouter model id string (e.g. "anthropic/claude-opus-4").
    active is set to false when the model is absent from the upstream response.
    """

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PricingSnapshotRow(Base):
    """Append-only pricing ledger.

    NEVER UPDATE OR DELETE rows from this table.
    captured_at is set by the database server clock at insert time.
    """

    __tablename__ = "pricing_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        Text, ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_usd_per_token: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    completion_usd_per_token: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
