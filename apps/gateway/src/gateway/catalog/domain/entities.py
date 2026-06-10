"""Catalog domain entities — pure data, zero framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """Value object representing a model from the upstream catalog source."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float


@dataclass(frozen=True, slots=True)
class ModelRow:
    """Domain representation of a persisted model record."""

    id: str
    name: str
    context_length: int | None
    active: bool


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
