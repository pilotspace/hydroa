"""SQLAlchemy ORM row for the routing_config table (v32 routing-config-store §3 FROZEN).

Operator-wide SINGLETON: at most one row. The single overridable routing configuration is
stored as a JSONB `config` document (model_groups, routing_strategy, cooldown_*, upstream_*,
loadbal_*). Applied OVER the env-derived Settings at boot (DB-wins-when-present, env fallback).

Singleton is enforced by a boolean primary key constrained to TRUE: a second insert collides on
the PK, and the repository upsert targets that fixed key (INSERT ... ON CONFLICT (id) DO UPDATE).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class RoutingConfigRow(Base):
    """The single operator-wide routing config row."""

    __tablename__ = "routing_config"
    __table_args__ = (
        CheckConstraint("id IS TRUE", name="routing_config_singleton_check"),
    )

    # Fixed-TRUE primary key → at most one row (the upsert always targets id=True).
    id: Mapped[bool] = mapped_column(
        Boolean, primary_key=True, server_default=sa_text("true")
    )
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
