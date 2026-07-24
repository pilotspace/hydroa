"""GET /v1/organization/usage/completions + GET /v1/organization/costs — OpenAI-wire,
API-key-authenticated, tenant-scoped usage/costs read API (tenant-usage-costs-api §3 FROZEN @ v1).

Rides the EXISTING Envoy /v1/ ext_authz route and re-authenticates in-app via AuthzUseCase (the
same seam /v1/chat|images|embeddings use) — ZERO new Envoy route, ZERO new table, ZERO migration.

Wire error envelope: the OpenAI SDK expects a bare ``{"error": <code>}`` body — NOT the gateway's
RFC-9457 problem+json shape — so every param/auth failure is returned as a JSONResponse here
rather than raised as a ProblemError (which the global handler would render as ``{"code": …}``).
Query params are all declared ``str | None`` and parsed manually in the use case, so FastAPI's own
422 (a different body shape) never fires; auth is resolved INSIDE the handler so a 401 body is the
OpenAI envelope too, and auth is checked BEFORE param validation (missing-key → 401, not 422).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.usage.application.openai_usage_query import (
    Endpoint,
    OpenAiUsageQueryUseCase,
    RawUsageParams,
)
from gateway.usage.domain.openai_usage import UsageQueryError
from gateway.usage.infrastructure.usage_aggregation_repository import (
    SqlAlchemyUsageAggregationRepository,
)

openai_usage_router = APIRouter(prefix="/v1/organization", tags=["usage"])

# Stateless singleton hasher — safe to share across requests (mirrors artifacts/images deps).
_hasher = Sha256SecretHasher()


def _raw_key(request: Request) -> str:
    """Extract the raw Bearer sk-… token, or "" (an empty key → AuthzUseCase raises → 401)."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _serve(
    request: Request, session: AsyncSession, endpoint: Endpoint, params: RawUsageParams
) -> JSONResponse:
    repo = SqlAlchemyApiKeyRepository(session)
    authz = AuthzUseCase(repo, _hasher)
    aggregation_repo = SqlAlchemyUsageAggregationRepository(session)
    use_case = OpenAiUsageQueryUseCase(authz, aggregation_repo)
    try:
        page = await use_case.execute(
            endpoint=endpoint, raw_key=_raw_key(request), params=params
        )
    except UsageQueryError as err:
        return JSONResponse(status_code=err.status, content={"error": err.code})
    return JSONResponse(status_code=200, content=page)


@openai_usage_router.get("/usage/completions")
async def get_organization_usage_completions(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    start_time: Annotated[str | None, Query()] = None,
    end_time: Annotated[str | None, Query()] = None,
    bucket_width: Annotated[str | None, Query()] = None,
    group_by: Annotated[str | None, Query()] = None,
    models: Annotated[str | None, Query()] = None,
    api_key_ids: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
    page: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    """OpenAI-wire time-bucketed token/request series over usage_records (tenant-scoped)."""
    return await _serve(
        request,
        session,
        "completions",
        RawUsageParams(
            start_time=start_time,
            end_time=end_time,
            bucket_width=bucket_width,
            group_by=group_by,
            models=models,
            api_key_ids=api_key_ids,
            limit=limit,
            page=page,
        ),
    )


@openai_usage_router.get("/costs")
async def get_organization_costs(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    start_time: Annotated[str | None, Query()] = None,
    end_time: Annotated[str | None, Query()] = None,
    bucket_width: Annotated[str | None, Query()] = None,
    group_by: Annotated[str | None, Query()] = None,
    api_key_ids: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
    page: Annotated[str | None, Query()] = None,
) -> JSONResponse:
    """OpenAI-wire time-bucketed billed-cost series over usage_records (tenant-scoped)."""
    return await _serve(
        request,
        session,
        "costs",
        RawUsageParams(
            start_time=start_time,
            end_time=end_time,
            bucket_width=bucket_width,
            group_by=group_by,
            models=None,
            api_key_ids=api_key_ids,
            limit=limit,
            page=page,
        ),
    )
