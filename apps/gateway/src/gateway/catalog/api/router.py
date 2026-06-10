"""Catalog API routers — sync (internal) and list models (public/JWT-authed)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from gateway.catalog.api.deps import (
    get_current_identity,
    get_list_use_case,
    get_sync_use_case,
)
from gateway.catalog.api.schemas import ModelItem, ModelsListResponse, SyncResponse
from gateway.catalog.application.use_cases import ListModelsForTenantUseCase, SyncCatalogUseCase
from gateway.catalog.domain.errors import CatalogEmptyError, CatalogSourceUnavailableError
from gateway.core.errors import ProblemError
from gateway.tenants.domain.entities import Identity

# Internal router — Envoy guards /internal/* at the edge (no auth in MVP).
internal_catalog_router = APIRouter(prefix="/internal/catalog", tags=["catalog-internal"])

# Public router — JWT-authenticated.
catalog_router = APIRouter(prefix="/v1", tags=["catalog"])


@internal_catalog_router.post("/sync", response_model=SyncResponse)
async def sync_catalog(
    use_case: Annotated[SyncCatalogUseCase, Depends(get_sync_use_case)],
) -> SyncResponse:
    """Fetch current model list from CatalogSource and persist to catalog.

    Returns {"synced": N} where N is the count of models processed.
    Returns 502 ERR_UPSTREAM_UNAVAILABLE if the upstream source is unreachable.
    """
    try:
        synced = await use_case.execute()
    except CatalogSourceUnavailableError as exc:
        raise ProblemError(
            502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream catalog source unavailable", str(exc)
        ) from exc
    return SyncResponse(synced=synced)


@catalog_router.get("/models", response_model=ModelsListResponse)
async def list_models(
    identity: Annotated[Identity, Depends(get_current_identity)],
    use_case: Annotated[ListModelsForTenantUseCase, Depends(get_list_use_case)],
) -> ModelsListResponse:
    """Return active models with tenant-specific markup applied.

    Requires a valid Bearer JWT.  Returns 409 ERR_CATALOG_EMPTY when no
    active models have been synced yet.
    """
    try:
        models = await use_case.execute(tenant_id=identity.tenant_id)
    except CatalogEmptyError:
        raise ProblemError(
            409, "ERR_CATALOG_EMPTY", "No active models in catalog — run sync first"
        ) from None
    return ModelsListResponse(
        data=[
            ModelItem(
                id=m.id,
                name=m.name,
                context_length=m.context_length,
                prompt_per_token=m.prompt_per_token,
                completion_per_token=m.completion_per_token,
            )
            for m in models
        ]
    )
