"""Catalog domain entities — pure data, zero framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

# ---------------------------------------------------------------------------
# Modality type alias (provider-seam TASK.md §3)
# ---------------------------------------------------------------------------

#: Frozen set of valid modality values.  Stored as TEXT in the DB; validated at
#: the application boundary.  Using Literal + TEXT (not a PostgreSQL ENUM) avoids
#: ALTER TYPE migrations when new modalities are added in a future slice.
Modality = Literal["chat", "embedding", "image", "audio_stt", "audio_tts"]

VALID_MODALITIES: frozenset[str] = frozenset(
    {"chat", "embedding", "image", "audio_stt", "audio_tts"}
)


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """Value object representing a model from the upstream catalog source.

    Additive fields (provider-seam TASK.md §3):
      modality — defaults to "chat" for v6-compatibility
      provider — defaults to "openrouter" for v6-compatibility
    """

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    modality: str = field(default="chat")
    provider: str = field(default="openrouter")


@dataclass(frozen=True, slots=True)
class ModelRow:
    """Domain representation of a persisted model record.

    Additive fields (provider-seam TASK.md §3):
      modality — defaults to "chat" for v6-compatibility
      provider — defaults to "openrouter" for v6-compatibility
    """

    id: str
    name: str
    context_length: int | None
    active: bool
    modality: str = field(default="chat")
    provider: str = field(default="openrouter")


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """Append-only pricing record. Never updated or deleted once written."""

    id: uuid.UUID
    model_id: str
    prompt_usd_per_token: Decimal
    completion_usd_per_token: Decimal


@dataclass(frozen=True, slots=True)
class MarkedUpModel:
    """Model with tenant-specific markup applied — returned from list use case."""

    id: str
    name: str
    context_length: int | None
    prompt_per_token: float
    completion_per_token: float
