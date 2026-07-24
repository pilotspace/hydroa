"""ORM model for the responses-state-store domain (responses-state-store PLAN.md §3).

One table:
  stored_responses — tenant-scoped materialized-context rows for the OpenAI
  Responses wire (``store:true`` persistence + ``previous_response_id`` chaining).

Each row carries the FULL chat-shaped context it was served with, so chaining is
ONE tenant-scoped read (no multi-row chain walk). ``previous_response_id`` is a
BARE pointer (NO foreign key): a deleted parent leaves an honest dangling echo,
never mutates history.

Indexes declared in BOTH __table_args__ AND the migration (v30 lesson):
  ix_stored_responses_tenant_created (tenant_id, created_at DESC) — listing / window sweep
  ix_stored_responses_tenant_prev    (tenant_id, previous_response_id) — cascade resolution

The payload columns (context_messages / response_body / usage) are NOT NULL — the
metadata-only state class does not exist (a ZDR tenant's ``store:true`` is rejected
403 pre-dial, and the ZDR sweep row-deletes any flip-ZDR-on-later leftovers).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Integer, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class StoredResponseRow(Base):
    """ORM model for the ``stored_responses`` table."""

    __tablename__ = "stored_responses"

    # The wire ``resp_*`` id is the primary key (TEXT, not a server-generated UUID) —
    # GET/DELETE address the row by the exact id the POST returned on the wire.
    id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    # Bare pointer, NO FK: a deleted parent leaves an honest dangling echo.
    previous_response_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    chain_depth: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    context_messages: Mapped[Any] = mapped_column(JSONB, nullable=False)
    response_body: Mapped[Any] = mapped_column(JSONB, nullable=False)
    usage: Mapped[Any] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("ix_stored_responses_tenant_created", "tenant_id", text("created_at DESC")),
        Index("ix_stored_responses_tenant_prev", "tenant_id", "previous_response_id"),
    )
