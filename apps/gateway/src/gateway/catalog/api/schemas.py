"""Catalog API DTOs (Pydantic v2 schemas)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SyncResponse(BaseModel):
    """Response for POST /internal/catalog/sync."""

    synced: int


class CatalogSyncResponse(BaseModel):
    """Response for POST /admin/catalog/sync — adds the completion time the UI shows.

    Distinct from the internal SyncResponse so the internal endpoint stays byte-identical.
    synced_at is the gateway clock (ISO-8601 UTC) stamped when the sync completed.
    """

    synced: int
    synced_at: str


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


class AdminCatalogModelItem(BaseModel):
    """Single model entry in GET /admin/catalog/models (capabilities-admin-surface TASK.md §3).

    Same pricing/id fields as ModelItem (lean public shape) PLUS input_modalities —
    a sorted list of accepted input types for the dashboard capabilities surface.
    ModelItem itself STAYS unchanged so GET /v1/models remains byte-identical.
    """

    id: str
    name: str
    context_length: int | None
    prompt_per_token: float
    completion_per_token: float
    object: str = "model"
    input_modalities: list[str]


class AdminCatalogModelsListResponse(BaseModel):
    """Response for GET /admin/catalog/models."""

    object: str = "list"
    data: list[AdminCatalogModelItem]


class AdminModelItem(BaseModel):
    """Single model entry in GET /admin/models and PUT /admin/models/{model_id} responses.

    enabled reflects the tenant's override: no override row = true (open by default).

    Additive field (capabilities-admin-surface TASK.md §3):
      input_modalities — sorted list of accepted input types; defaults to ["text"].
    """

    id: str
    name: str
    context_length: int | None
    enabled: bool
    input_modalities: list[str] = Field(default_factory=lambda: ["text"])


class AdminModelsListResponse(BaseModel):
    """Response for GET /admin/models."""

    object: str = "list"
    data: list[AdminModelItem]


class PutModelRequest(BaseModel):
    """Request body for PUT /admin/models/{model_id}."""

    enabled: bool
