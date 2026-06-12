"""API router for POST /v1/images/generations proxy endpoint.

Contract FROZEN @ images-endpoint (TASK.md §3).

Mirrors the style of proxy/api/embeddings_router.py without modifying it.
Routes exclusively via app.state.provider_registry — never calls
get_completion_upstream() or CompletionUseCase.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from gateway.proxy.api.deps import get_raw_api_key, get_usage_recorder
from gateway.proxy.api.images_deps import get_images_use_case, get_provider_registry
from gateway.proxy.application.images_use_case import ImagesUseCase
from gateway.proxy.domain.ports import UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

images_router = APIRouter(tags=["proxy"])


@images_router.post("/v1/images/generations")
async def images_generations(
    request: Request,
    use_case: Annotated[ImagesUseCase, Depends(get_images_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/images/generations — per-image priced via the provider seam.

    Reads raw JSON body (dict), validates model + prompt fields, runs governance,
    forwards to the upstream provider, fires a usage record, and returns the
    upstream response verbatim.
    """
    body: dict[str, Any] = await request.json()

    status, response_body = await use_case.execute(
        raw_key=raw_key,
        body=body,
        registry=registry,
        usage_recorder=usage_recorder,
    )

    return JSONResponse(content=response_body, status_code=status)
