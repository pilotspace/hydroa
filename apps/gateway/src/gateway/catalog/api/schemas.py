"""Catalog API DTOs (Pydantic v2 schemas)."""

from __future__ import annotations

from pydantic import BaseModel


class SyncResponse(BaseModel):
    """Response for POST /internal/catalog/sync."""

    synced: int


class ModelItem(BaseModel):
    """Single model entry in the GET /v1/models response."""

    id: str
    name: str
    context_length: int | None
    prompt_per_token: float
    completion_per_token: float
    object: str = "model"


class ModelsListResponse(BaseModel):
    """Response for GET /v1/models (OpenAI-compatible list envelope)."""

    object: str = "list"
    data: list[ModelItem]
