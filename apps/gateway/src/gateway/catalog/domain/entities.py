"""Catalog domain entities — pure data, zero framework imports."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
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

# ---------------------------------------------------------------------------
# Input-modality type alias (model-input-capabilities TASK.md §3)
# ---------------------------------------------------------------------------

#: video is deliberately deferred from v55 — Tin 2026-06-30.
InputModality = Literal["text", "image", "audio"]

VALID_INPUT_MODALITIES: frozenset[str] = frozenset({"text", "image", "audio"})

#: Canonical sort order used by normalize_input_modalities.
#: Entries are sorted by position in this tuple so the output is always
#: deterministic regardless of the order values arrive in.
_INPUT_MODALITY_ORDER: tuple[str, ...] = ("text", "image", "audio")


def default_input_modalities(modality: str) -> frozenset[str]:
    """Derive a conservative default input-modality set from the coarse modality classifier.

    This is a PURE, TOTAL function — it never raises and always returns a non-empty
    frozenset.  Unknown classifier values fall back to {"text"} (safe fallback).

    Mapping (§3 CONTRACT):
      audio_stt                           -> {"audio"}
      chat | embedding | image | audio_tts
      | <any unknown value>               -> {"text"}
    """
    if modality == "audio_stt":
        return frozenset({"audio"})
    return frozenset({"text"})


def normalize_input_modalities(values: Iterable[str]) -> str:
    """Validate, deduplicate, sort, and join input-modality tokens into a canonical CSV string.

    Processing steps:
      1. Strip whitespace from each token.
      2. Drop blank tokens.
      3. Validate: any unknown token raises InvalidInputModalityError.
      4. Deduplicate (frozenset).
      5. Sort in canonical order (_INPUT_MODALITY_ORDER).
      6. Validate: empty result raises EmptyInputModalitiesError.
      7. Join with "," and return.

    Examples::

        normalize_input_modalities(["audio", "text", "text"])  -> "text,audio"
        normalize_input_modalities(["pdf"])                     -> raises InvalidInputModalityError
        normalize_input_modalities([])                          -> raises EmptyInputModalitiesError

    Args:
        values: Iterable of raw input-modality tokens (may contain duplicates/blanks).

    Returns:
        Normalized, canonical CSV string (e.g. "text,image,audio").

    Raises:
        InvalidInputModalityError: If any non-blank token is not in VALID_INPUT_MODALITIES.
        EmptyInputModalitiesError: If the result set is empty after stripping blanks.
    """
    from gateway.catalog.domain.errors import EmptyInputModalitiesError, InvalidInputModalityError

    seen: set[str] = set()
    for raw in values:
        token = raw.strip()
        if not token:
            continue
        if token not in VALID_INPUT_MODALITIES:
            raise InvalidInputModalityError(
                f"Unknown input modality {token!r}; valid values: {sorted(VALID_INPUT_MODALITIES)}"
            )
        seen.add(token)

    if not seen:
        raise EmptyInputModalitiesError(
            "input_modalities must be non-empty; provide at least one of: "
            + ", ".join(_INPUT_MODALITY_ORDER)
        )

    # Sort by canonical order so output is deterministic.
    ordered = [m for m in _INPUT_MODALITY_ORDER if m in seen]
    return ",".join(ordered)


def parse_input_modalities(text: str) -> frozenset[str]:
    """Parse a normalized CSV string back into a frozenset of input modality tokens.

    This is the inverse of normalize_input_modalities for reading stored values.
    No validation is performed — the DB stores only values that were already
    written through normalize_input_modalities or the migration default.

    Example::

        parse_input_modalities("text,image") -> frozenset({"text", "image"})

    Args:
        text: Comma-separated modality string as stored in the database.

    Returns:
        frozenset of modality token strings.
    """
    return frozenset(t.strip() for t in text.split(",") if t.strip())


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """Value object representing a model from the upstream catalog source.

    Additive fields (provider-seam TASK.md §3):
      modality — defaults to "chat" for v6-compatibility
      provider — defaults to "openrouter" for v6-compatibility

    Additive fields (model-input-capabilities TASK.md §3):
      input_modalities — normalized CSV; defaults to "text" (chat ⇒ {text}).
    """

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    modality: str = field(default="chat")
    provider: str = field(default="openrouter")
    input_modalities: str = field(default="text")


@dataclass(frozen=True, slots=True)
class ModelRow:
    """Domain representation of a persisted model record.

    Additive fields (provider-seam TASK.md §3):
      modality — defaults to "chat" for v6-compatibility
      provider — defaults to "openrouter" for v6-compatibility

    Additive fields (model-input-capabilities TASK.md §3):
      input_modalities — normalized CSV; defaults to "text" (chat ⇒ {text}).
    """

    id: str
    name: str
    context_length: int | None
    active: bool
    modality: str = field(default="chat")
    provider: str = field(default="openrouter")
    input_modalities: str = field(default="text")


@dataclass(frozen=True, slots=True)
class PricingSnapshot:
    """Append-only pricing record. Never updated or deleted once written."""

    id: uuid.UUID
    model_id: str
    prompt_usd_per_token: Decimal
    completion_usd_per_token: Decimal


@dataclass(frozen=True, slots=True)
class MarkedUpModel:
    """Model with tenant-specific markup applied — returned from list use case.

    Additive field (capabilities-admin-surface TASK.md §3):
      input_modalities — normalized CSV from the models row; defaults to "text".
      Read-only surface — never derived or computed here.
    """

    id: str
    name: str
    context_length: int | None
    prompt_per_token: float
    completion_per_token: float
    input_modalities: str = field(default="text")
