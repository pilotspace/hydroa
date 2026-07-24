"""API router for POST /v1/images/generations proxy endpoint.

Contract FROZEN @ images-endpoint (TASK.md §3).

Mirrors the style of proxy/api/embeddings_router.py without modifying it.
Routes exclusively via app.state.provider_registry — never calls
get_completion_upstream() or CompletionUseCase.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from gateway.proxy.api.deps import get_raw_api_key, get_usage_recorder
from gateway.proxy.api.images_deps import (
    get_image_edit_use_case,
    get_image_variation_use_case,
    get_images_use_case,
    get_provider_registry,
)
from gateway.proxy.application.images_use_case import (
    ImageEditUseCase,
    ImagesUseCase,
    ImageVariationUseCase,
)
from gateway.proxy.application.json_sanitize import sanitize_non_finite
from gateway.proxy.domain.ports import UsageRecorder
from gateway.proxy.infrastructure.provider_registry import ProviderRegistry

_log = logging.getLogger(__name__)

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

    # Sanitize non-finite floats (inf/-inf/nan) before render: Starlette serializes with
    # allow_nan=False, so an upstream non-finite anywhere would 500. Replace with null
    # (degrade, never fail) + WARN once. Response-only — billing already recorded.
    response_body, _nf = sanitize_non_finite(response_body)
    if _nf:
        _log.warning(
            "images_nonfinite_sanitized",
            extra={"model": body.get("model"), "count": _nf},
        )

    return JSONResponse(content=response_body, status_code=status)


@images_router.post("/v1/images/edits")
async def images_edits(
    request: Request,
    use_case: Annotated[ImageEditUseCase, Depends(get_image_edit_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/images/edits — per-image priced, multipart image-in/image-out.

    Contract FROZEN @ image-edits-variations (PLAN.md §3). Reads multipart form
    (requires python-multipart; already installed for audio), validates image +
    prompt + model fields, runs governance, forwards to the upstream provider via
    UpstreamProvider.post_multipart, fires a per_image usage record on the
    upstream-RETURNED image count, and returns the upstream response verbatim.
    """
    form = await request.form()
    status, response_body = await use_case.execute(
        raw_key=raw_key,
        form=form,
        registry=registry,
        usage_recorder=usage_recorder,
    )

    response_body, _nf = sanitize_non_finite(response_body)
    if _nf:
        _log.warning(
            "images_edits_nonfinite_sanitized",
            extra={"model": form.get("model"), "count": _nf},
        )

    return JSONResponse(content=response_body, status_code=status)


@images_router.post("/v1/images/variations")
async def images_variations(
    request: Request,
    use_case: Annotated[ImageVariationUseCase, Depends(get_image_variation_use_case)],
    registry: Annotated[ProviderRegistry, Depends(get_provider_registry)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/images/variations — per-image priced, multipart image-in/image-out.

    Contract FROZEN @ image-edits-variations (PLAN.md §3). Identical shape to
    images_edits except only `image` is required (no `prompt`, no `mask`), and the
    upstream path is /images/variations.
    """
    form = await request.form()
    status, response_body = await use_case.execute(
        raw_key=raw_key,
        form=form,
        registry=registry,
        usage_recorder=usage_recorder,
    )

    response_body, _nf = sanitize_non_finite(response_body)
    if _nf:
        _log.warning(
            "images_variations_nonfinite_sanitized",
            extra={"model": form.get("model"), "count": _nf},
        )

    return JSONResponse(content=response_body, status_code=status)
