"""API router for POST /v1/embeddings proxy endpoint.

Contract FROZEN @ embeddings-endpoint (TASK.md §3).

Mirrors the style of proxy/api/router.py without modifying it.
Routes exclusively via app.state.provider_registry — never calls
get_completion_upstream() or CompletionUseCase.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from gateway.proxy.api.deps import get_raw_api_key, get_usage_recorder
from gateway.proxy.api.embeddings_deps import (
    get_embeddings_use_case,
    get_provider_registry,
    get_response_cache,
)
from gateway.proxy.application.embeddings_use_case import EmbeddingsUseCase
from gateway.proxy.domain.ports import ResponseCache, UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry
from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl

embeddings_router = APIRouter(tags=["proxy"])


@embeddings_router.post("/v1/embeddings")
async def embeddings(
    request: Request,
    use_case: Annotated[EmbeddingsUseCase, Depends(get_embeddings_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
    response_cache: Annotated[ResponseCache | None, Depends(get_response_cache)],
) -> Any:
    """POST /v1/embeddings — token-priced embeddings via the provider seam.

    Reads raw JSON body (dict), validates model + input fields, runs governance,
    consults the embedding response cache, forwards to the upstream provider on a
    miss, fires a usage record, and returns the upstream response verbatim.
    """
    body: dict[str, Any] = await request.json()

    # Cache-Control: no-cache detection + per-request max-age TTL override.
    req_headers = {k.lower(): v for k, v in request.headers.items()}
    cache_ttl = getattr(getattr(request.app, "state", None), "cache_ttl_seconds", 300)
    cache_max_ttl = getattr(getattr(request.app, "state", None), "cache_max_ttl_seconds", 86400)
    effective_ttl = resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)

    status, response_body, x_cache = await use_case.execute(
        raw_key=raw_key,
        body=body,
        registry=registry,
        usage_recorder=usage_recorder,
        response_cache=response_cache,
        cache_ttl_seconds=effective_ttl,
        request_headers=req_headers,
    )

    resp = JSONResponse(content=response_body, status_code=status)
    if x_cache is not None:
        resp.headers["x-cache"] = x_cache
    return resp
