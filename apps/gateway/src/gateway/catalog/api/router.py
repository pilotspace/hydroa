"""Catalog API routers — sync (internal), list models (public/JWT-authed), admin model mgmt."""

from __future__ import annotations

import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.api.deps import (
    get_admin_models_session,
    get_current_identity,
    get_list_use_case,
    get_sync_use_case,
    get_token_service,
    require_catalog_sync,
    require_owner_or_admin,
)
from gateway.catalog.api.schemas import (
    AdminCatalogModelItem,
    AdminCatalogModelsListResponse,
    AdminModelItem,
    AdminModelsListResponse,
    CatalogSyncResponse,
    ModelItem,
    ModelsListResponse,
    PutModelRequest,
    SyncResponse,
)
from gateway.catalog.application.use_cases import (
    ListModelsForTenantUseCase,
    SyncCatalogUseCase,
)
from gateway.catalog.domain.entities import parse_input_modalities
from gateway.catalog.domain.errors import (
    CatalogEmptyError,
    CatalogSourceUnavailableError,
)
from gateway.catalog.infrastructure.orm import ModelRow, TenantModelOverrideRow
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    CATALOG_EMPTY,
    CATALOG_UPSTREAM_UNAVAILABLE,
    MODEL_NOT_FOUND,
)
from gateway.core.errors import ProblemError
from gateway.proxy.api.deps import get_completion_use_case, get_raw_key_ingress
from gateway.proxy.application.model_discovery import list_entitled_claude_models
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.ports import TokenService

# Internal router — Envoy guards /internal/* at the edge (no auth in MVP).
internal_catalog_router = APIRouter(prefix="/internal/catalog", tags=["catalog-internal"])

# Public router — JWT-authenticated.
catalog_router = APIRouter(prefix="/v1", tags=["catalog"])

# Admin model management router — owner/admin JWT required (model-mgmt TASK.md §3).
admin_models_router = APIRouter(prefix="/admin/models", tags=["admin-models"])

# Admin catalog router — owner/admin JWT required (catalog-sync-trigger TASK.md §3).
# Separate prefix from /admin/models to avoid the {model_id:path} converter collision with /sync.
admin_catalog_router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])


@internal_catalog_router.post("/sync", response_model=SyncResponse)
async def sync_catalog(
    request: Request,
    use_case: Annotated[SyncCatalogUseCase, Depends(get_sync_use_case)],
) -> SyncResponse:
    """Fetch current model list from CatalogSource and persist to catalog.

    Returns {"synced": N} where N is the count of models processed.
    Returns 502 ERR_UPSTREAM_UNAVAILABLE if the upstream source is unreachable.

    After a successful sync, refreshes the provider resolver cache (fail-safe —
    a refresh error never changes the sync response shape).
    """
    try:
        synced = await use_case.execute()
    except CatalogSourceUnavailableError as exc:
        raise CATALOG_UPSTREAM_UNAVAILABLE.exc(detail=str(exc)) from exc

    # Fail-safe provider resolver refresh — keep the model→provider map in sync
    # after a catalog sync. Never let a refresh error affect the sync response.
    try:
        _r = getattr(request.app.state, "provider_resolver", None)
        if _r is not None:
            await _r.refresh()
    except Exception:  # noqa: S110 — intentional; refresh is fail-safe, never alters sync response
        pass

    return SyncResponse(synced=synced)


@catalog_router.get("/models", response_model=None)
async def list_models(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    list_use_case: Annotated[ListModelsForTenantUseCase, Depends(get_list_use_case)],
    tokens: Annotated[TokenService, Depends(get_token_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    """GET /v1/models — TWO distinct audiences share this ONE path (a real, pre-
    existing collision surfaced by claude-gateway-protocol-compat TASK.md §3 M1 — the
    external Claude gateway protocol hardcodes this exact path; it cannot be moved).

    Branch 1 (claude-gateway-protocol-compat TASK.md §3 M1, NEW): an sk-/agent-token
    credential (Authorization: Bearer <key> | x-api-key: <key> — the SAME
    CompositeKeyAuthenticator /v1/messages already uses) gets the Claude Code
    model-discovery shape: {"data":[{"id":"claude-...", "display_name"?}]}, scoped to
    catalog rows the tenant/key is entitled to that resolve to a claude-/anthropic--
    prefixed id. Never redirects.

    Branch 2 (PRE-EXISTING, byte-identical): a Bearer JWT (browser dashboard session)
    gets the ORIGINAL tenant-priced OpenAI-compatible list — UNCHANGED shape/behavior.
    409 ERR_CATALOG_EMPTY when no active models have been synced yet.

    Dispatch: try the API-key/agent-token path FIRST (fails cleanly — InvalidApiKeyError
    -> ProblemError — for a JWT, which is never sk--prefixed and never resolves via the
    agent-token store); only on that failure does the JWT branch run. A caller with NO
    credential at all fails the API-key branch and then genuinely fails the JWT branch
    too (AUTH_TOKEN_MISSING) — the SAME final 401 as before this change.
    """
    raw_key = get_raw_key_ingress(request)
    if raw_key is not None:
        try:
            authz = await use_case._authenticate(raw_key)  # pyright: ignore[reportPrivateUsage]
        except ProblemError:
            authz = None
        if authz is not None:
            model_router = getattr(getattr(request.app, "state", None), "model_router", None)
            model_groups: dict[str, list[str]] = (
                model_router.model_groups if model_router is not None else {}
            )
            entries = await list_entitled_claude_models(
                session,
                tenant_id=authz.tenant_id,
                model_groups=model_groups,
                key_model_allowlist=authz.model_allowlist,
                plan_model_allowlist=authz.plan_model_allowlist,
            )
            data = [
                {"id": e.id, **({"display_name": e.display_name} if e.display_name else {})}
                for e in entries
            ]
            return JSONResponse(content={"data": data}, status_code=200)

    # PRE-EXISTING JWT (dashboard) branch — UNCHANGED behavior/shape.
    identity: Identity = await get_current_identity(request, tokens, session)
    try:
        models = await list_use_case.execute(tenant_id=identity.tenant_id)
    except CatalogEmptyError:
        raise CATALOG_EMPTY.exc() from None
    body = ModelsListResponse(
        data=[
            ModelItem(
                id=m.id,
                name=m.name,
                context_length=m.context_length,
                prompt_per_token=m.prompt_per_token,
                completion_per_token=m.completion_per_token,
                prompt_usd_per_1m=m.prompt_per_token * 1_000_000,
                completion_usd_per_1m=m.completion_per_token * 1_000_000,
                cached_input_usd_per_1m=(
                    m.cached_input_per_token * 1_000_000
                    if m.cached_input_per_token is not None
                    else None
                ),
                audio_prompt_usd_per_1m=(
                    m.audio_prompt_per_token * 1_000_000
                    if m.audio_prompt_per_token is not None
                    else None
                ),
                audio_completion_usd_per_1m=(
                    m.audio_completion_per_token * 1_000_000
                    if m.audio_completion_per_token is not None
                    else None
                ),
                audio_cached_usd_per_1m=(
                    m.audio_cached_per_token * 1_000_000
                    if m.audio_cached_per_token is not None
                    else None
                ),
            )
            for m in models
        ]
    )
    return JSONResponse(content=body.model_dump(mode="json"), status_code=200)


@admin_catalog_router.get("/models", response_model=AdminCatalogModelsListResponse)
async def list_catalog_models(
    identity: Annotated[Identity, Depends(get_current_identity)],
    use_case: Annotated[ListModelsForTenantUseCase, Depends(get_list_use_case)],
) -> AdminCatalogModelsListResponse:
    """GET /admin/catalog/models — admin catalog surface with input_modalities.

    Returns the SAME tenant-priced active-model list as GET /v1/models (same
    ListModelsForTenantUseCase, identical markup arithmetic) but on the /admin/*
    (JWT/session) plane and extended with input_modalities — a sorted list of
    accepted input types for each model.

    Why two endpoints exist:
      /v1/models sits behind edge ext_authz (sk-/agent keys only); a browser
      SESSION JWT 401s through the edge and the BFF clears the cookie, logging
      the user out.  This admin twin is readable with a session JWT without
      widening the data plane.

    Any authenticated tenant role may read it (get_current_identity — no
    permission gate), parity with /v1/models.  Returns 409 ERR_CATALOG_EMPTY
    before the first catalog sync (identical to /v1/models behavior).

    GET /v1/models stays UNCHANGED (lean OpenAI shape, no input_modalities).
    """
    try:
        models = await use_case.execute(tenant_id=identity.tenant_id)
    except CatalogEmptyError:
        raise CATALOG_EMPTY.exc() from None
    return AdminCatalogModelsListResponse(
        data=[
            AdminCatalogModelItem(
                id=m.id,
                name=m.name,
                context_length=m.context_length,
                prompt_per_token=m.prompt_per_token,
                completion_per_token=m.completion_per_token,
                input_modalities=sorted(parse_input_modalities(m.input_modalities)),
                prompt_usd_per_1m=m.prompt_per_token * 1_000_000,
                completion_usd_per_1m=m.completion_per_token * 1_000_000,
                cached_input_usd_per_1m=(
                    m.cached_input_per_token * 1_000_000
                    if m.cached_input_per_token is not None
                    else None
                ),
                audio_prompt_usd_per_1m=(
                    m.audio_prompt_per_token * 1_000_000
                    if m.audio_prompt_per_token is not None
                    else None
                ),
                audio_completion_usd_per_1m=(
                    m.audio_completion_per_token * 1_000_000
                    if m.audio_completion_per_token is not None
                    else None
                ),
                audio_cached_usd_per_1m=(
                    m.audio_cached_per_token * 1_000_000
                    if m.audio_cached_per_token is not None
                    else None
                ),
                # region-catalog-dimension TASK.md §3: exposed here; NOT on /v1/models.
                region=m.region,
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
            ModelRow.input_modalities,
            ModelRow.region,
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
                # capabilities-admin-surface TASK.md §3: sorted list from stored CSV
                input_modalities=sorted(parse_input_modalities(row.input_modalities)),
                # region-catalog-dimension TASK.md §3: raw passthrough.
                region=row.region,
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
            select(
                ModelRow.id,
                ModelRow.name,
                ModelRow.context_length,
                ModelRow.input_modalities,
                ModelRow.region,
            ).where(ModelRow.id == model_id)
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
        # capabilities-admin-surface TASK.md §3: include in PUT response
        input_modalities=sorted(parse_input_modalities(model_row.input_modalities)),
        # region-catalog-dimension TASK.md §3 M6: PUT never writes region — the row's
        # existing value is simply echoed back (this endpoint only toggles `enabled`).
        region=model_row.region,
    )


# ---------------------------------------------------------------------------
# Admin catalog sync trigger (catalog-sync-trigger TASK.md §3)
# ---------------------------------------------------------------------------


@admin_catalog_router.post("/sync", response_model=CatalogSyncResponse)
async def admin_sync_catalog(
    request: Request,
    identity: Annotated[Identity, Depends(require_catalog_sync)],
    use_case: Annotated[SyncCatalogUseCase, Depends(get_sync_use_case)],
) -> CatalogSyncResponse:
    """POST /admin/catalog/sync — owner/admin-triggered model-catalog re-sync.

    A thin owner/admin wrapper over the same SyncCatalogUseCase the internal endpoint uses
    (member → 403 ERR_AUTH_FORBIDDEN via require_owner_or_admin). The catalog is a GLOBAL
    platform resource; this is an idempotent upsert. Returns {synced, synced_at} where
    synced_at is the gateway clock at completion (ISO-8601 UTC) — the dashboard shows it.

    502 ERR_UPSTREAM_UNAVAILABLE if the catalog source is unreachable (raised before any
    write — the catalog is unchanged on failure). After a successful sync, refreshes the
    provider resolver cache fail-safely (a refresh error never alters the response).
    """
    try:
        synced = await use_case.execute()
    except CatalogSourceUnavailableError as exc:
        raise CATALOG_UPSTREAM_UNAVAILABLE.exc(detail=str(exc)) from exc

    synced_at = datetime.datetime.now(datetime.UTC).isoformat()

    # Fail-safe provider resolver refresh — keep the model→provider map in sync after a
    # catalog sync. Never let a refresh error affect the response (mirrors the internal handler).
    try:
        _r = getattr(request.app.state, "provider_resolver", None)
        if _r is not None:
            await _r.refresh()
    except Exception:  # noqa: S110 — intentional; refresh is fail-safe, never alters sync response
        pass

    return CatalogSyncResponse(synced=synced, synced_at=synced_at)
