"""API router for /v1/chat/completions proxy endpoint.

Contract FROZEN @ v1 (proxy-completions TASK.md §3).

All gateway-generated errors are RFC 9457 problem+json (gateway.core.errors).
Upstream 4xx responses are passed through verbatim — no wrapping.
Upstream 5xx / circuit open → 502 ERR_UPSTREAM_UNAVAILABLE.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.proxy.api.deps import (
    get_completion_upstream,
    get_completion_use_case,
    get_raw_api_key,
    get_usage_recorder,
)
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import CompletionUpstream, UsageRecorder

proxy_router = APIRouter(tags=["proxy"])


@proxy_router.post("/v1/chat/completions")
async def completions(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    upstream: Annotated[CompletionUpstream, Depends(get_completion_upstream)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_api_key)],
) -> Any:
    """POST /v1/chat/completions — streaming and non-streaming pass-through.

    Non-streaming: returns upstream JSON body verbatim (status + body).
    Streaming:     returns StreamingResponse with text/event-stream.
    """
    body: dict[str, Any] = await request.json()
    stream_requested = bool(body.get("stream", False))

    if stream_requested:
        gen = await use_case.stream(
            raw_key=raw_key,
            body=body,
            upstream=upstream,
            usage_recorder=usage_recorder,
        )
        return StreamingResponse(gen, media_type="text/event-stream")

    status, response_body = await use_case.complete(
        raw_key=raw_key,
        body=body,
        upstream=upstream,
        usage_recorder=usage_recorder,
    )
    # Upstream 4xx: pass through verbatim — JSONResponse with upstream status
    return JSONResponse(content=response_body, status_code=status)
