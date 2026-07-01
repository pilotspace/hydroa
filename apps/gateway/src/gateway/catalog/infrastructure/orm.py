"""SQLAlchemy ORM rows for the catalog module."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class ModelRow(Base):
    """Persisted catalog model entry.

    id is the OpenRouter model id string (e.g. "anthropic/claude-opus-4").
    active is set to false when the model is absent from the upstream response.

    Additive columns (provider-seam TASK.md §3):
      modality — one of {chat, embedding, image, audio_stt, audio_tts}; default "chat"
      provider — upstream provider name (registry key); default "openrouter"
    Every pre-existing row gets modality="chat", provider="openrouter" via migration default.
    """

    __tablename__ = "models"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    context_length: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    modality: Mapped[str] = mapped_column(Text, nullable=False, server_default="chat")
    provider: Mapped[str] = mapped_column(Text, nullable=False, server_default="openrouter")
    # model-input-capabilities TASK.md §3: normalized CSV of accepted input types.
    # server_default 'text' gives every pre-existing row the conservative chat default.
    # The migration's UPDATE sets audio_stt rows to 'audio' after column addition.
    input_modalities: Mapped[str] = mapped_column(Text, nullable=False, server_default="text")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class PricingSnapshotRow(Base):
    """Append-only pricing ledger.

    NEVER UPDATE OR DELETE rows from this table.
    captured_at is set by the database server clock at insert time.

    Additive columns (pricing-units TASK.md §3):
      pricing_unit      — discriminator; one of per_token|per_image|per_second|per_character
                          NOT NULL DEFAULT 'per_token'; all existing rows carry per_token.
      unit_usd_per_unit — generic per-unit price for non-token rows; NULL for per_token rows.
    """

    __tablename__ = "pricing_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    model_id: Mapped[str] = mapped_column(
        Text, ForeignKey("models.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_usd_per_token: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    completion_usd_per_token: Mapped[float] = mapped_column(Numeric(20, 10), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    pricing_unit: Mapped[str] = mapped_column(Text, nullable=False, server_default="per_token")
    unit_usd_per_unit: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    # tiered-token-billing (TASK.md §3): per-tier prices; NULL → fall back to the base
    # prompt/completion price (the flat path), so existing snapshots bill byte-identically.
    cached_input_usd_per_token: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )
    reasoning_usd_per_token: Mapped[Decimal | None] = mapped_column(Numeric(20, 10), nullable=True)
    # prompt-cache-passthrough (TASK.md §3): Anthropic ~1.25x write premium for cache
    # creation; NULL -> falls back to prompt rate (no billing regression until feed populated).
    cache_creation_usd_per_token: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 10), nullable=True
    )


class TenantModelOverrideRow(Base):
    """Per-tenant model enable/disable override.

    Composite PK (tenant_id, model_id) — at most one row per (tenant, model) pair.
    No override row = default enabled=true ("open by default", model-mgmt TASK.md §1 M5).
    Both FKs are ON DELETE CASCADE: tenant deletion and catalog model hard-delete
    clean up override rows automatically.

    Added by migration: <new_rev>_tenant_model_overrides (model-mgmt TASK.md §3).
    """

    __tablename__ = "tenant_model_overrides"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    model_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("models.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
