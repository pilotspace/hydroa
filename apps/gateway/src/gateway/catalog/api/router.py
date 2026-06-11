"""Catalog API routers — sync (internal), list models (public/JWT-authed), admin model mgmt."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.api.deps import (
    get_admin_models_session,
    get_current_identity,
    get_list_use_case,
    get_sync_use_case,
    require_owner_or_admin,
)
from gateway.catalog.api.schemas import (
    AdminModelItem,
    AdminModelsListResponse,
    ModelItem,
    ModelsListResponse,
    PutModelRequest,
    SyncResponse,
)
from gateway.catalog.application.use_cases import (
    ListModelsForTenantUseCase,
    SyncCatalogUseCase,
)
from gateway.catalog.domain.errors import (
    CatalogEmptyError,
    CatalogSourceUnavailableError,
)
from gateway.catalog.infrastructure.orm import ModelRow, TenantModelOverrideRow
from gateway.core.error_catalog import (
    CATALOG_EMPTY,
    CATALOG_UPSTREAM_UNAVAILABLE,
    MODEL_NOT_FOUND,
)
from gateway.tenants.domain.entities import Identity

# Internal router — Envoy guards /internal/* at the edge (no auth in MVP).
internal_catalog_router = APIRouter(prefix="/internal/catalog", tags=["catalog-internal"])

# Public router — JWT-authenticated.
catalog_router = APIRouter(prefix="/v1", tags=["catalog"])

# Admin model management router — owner/admin JWT required (model-mgmt TASK.md §3).
admin_models_router = APIRouter(prefix="/admin/models", tags=["admin-models"])


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
        raise CATALOG_UPSTREAM_UNAVAILABLE.exc(detail=str(exc)) from exc
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
        raise CATALOG_EMPTY.exc() from None
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


# ---------------------------------------------------------------------------
# Admin model management endpoints (model-mgmt TASK.md §3)
# ---------------------------------------------------------------------------


@admin_models_router.get("", response_model=AdminModelsListResponse)
async def get_admin_models(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_admin_models_session)],
) -> AdminModelsListResponse:
    """GET /admin/models — list catalog models with per-tenant enabled flags.

    Returns all active catalog models joined with the caller's tenant overrides.
    No override row = enabled=true (open by default, M5).
    Requires owner or admin JWT; member role → 403 ERR_AUTH_FORBIDDEN.
    """
    stmt = (
        select(
            ModelRow.id,
            ModelRow.name,
            ModelRow.context_length,
            TenantModelOverrideRow.enabled,
        )
        .outerjoin(
            TenantModelOverrideRow,
            (TenantModelOverrideRow.model_id == ModelRow.id)
            & (TenantModelOverrideRow.tenant_id == identity.tenant_id),
        )
        .where(ModelRow.active.is_(True))
        .order_by(ModelRow.id)
    )
    rows = (await session.execute(stmt)).all()
    return AdminModelsListResponse(
        data=[
            AdminModelItem(
                id=row.id,
                name=row.name,
                context_length=row.context_length,
                # COALESCE(tmo.enabled, true): None means no override row → default enabled
                enabled=row.enabled if row.enabled is not None else True,
            )
            for row in rows
        ]
    )


@admin_models_router.put("/{model_id:path}", response_model=AdminModelItem)
async def put_admin_model(
    model_id: str,
    body: PutModelRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_admin_models_session)],
) -> AdminModelItem:
    """PUT /admin/models/{model_id} — upsert a tenant model override.

    model_id uses the :path converter because OpenRouter model ids contain "/".
    httpx ASGITransport delivers the decoded path, so the route param is already
    unencoded (e.g. "openai/gpt-4o", not "openai%2Fgpt-4o").

    Returns 404 ERR_MODEL_NOT_FOUND when model_id is not in the catalog.
    Returns 403 ERR_AUTH_FORBIDDEN for member role callers.
    Atomic upsert: INSERT ... ON CONFLICT (tenant_id, model_id) DO UPDATE SET enabled, updated_at.
    """
    # Verify model exists in catalog (any active state — the override can be set even
    # if the model is currently inactive; the §3 DDL allows FK to any models row).
    model_row = (
        await session.execute(
            select(ModelRow.id, ModelRow.name, ModelRow.context_length).where(
                ModelRow.id == model_id
            )
        )
    ).one_or_none()
    if model_row is None:
        raise MODEL_NOT_FOUND.exc(model_id=model_id)

    # Atomic upsert: single statement, no TOCTOU gap.
    stmt = (
        pg_insert(TenantModelOverrideRow)
        .values(
            tenant_id=identity.tenant_id,
            model_id=model_id,
            enabled=body.enabled,
            created_at=func.now(),
            updated_at=func.now(),
        )
        .on_conflict_do_update(
            index_elements=["tenant_id", "model_id"],
            set_={
                "enabled": body.enabled,
                "updated_at": func.now(),
            },
        )
    )
    await session.execute(stmt)
    await session.commit()

    return AdminModelItem(
        id=model_row.id,
        name=model_row.name,
        context_length=model_row.context_length,
        enabled=body.enabled,
    )
