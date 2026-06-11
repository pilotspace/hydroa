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


# ---------------------------------------------------------------------------
# Admin model management schemas (model-mgmt TASK.md §3)
# ---------------------------------------------------------------------------


class AdminModelItem(BaseModel):
    """Single model entry in GET /admin/models and PUT /admin/models/{model_id} responses.

    enabled reflects the tenant's override: no override row = true (open by default).
    """

    id: str
    name: str
    context_length: int | None
    enabled: bool


class AdminModelsListResponse(BaseModel):
    """Response for GET /admin/models."""

    object: str = "list"
    data: list[AdminModelItem]


class PutModelRequest(BaseModel):
    """Request body for PUT /admin/models/{model_id}."""

    enabled: bool
