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
from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl

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

    # Resolve model_router from app.state per-request (fail-open: None if not wired).
    # This preserves the frozen-suite injection contract: tests that inject fakes into
    # app.state.completion_upstream are unaffected because the router receives the
    # per-request circuit-breaker-wrapped upstream via its `upstream` override kwarg.
    model_router = getattr(getattr(request.app, "state", None), "model_router", None)

    if stream_requested:
        gen = await use_case.stream(
            raw_key=raw_key,
            body=body,
            upstream=upstream,
            usage_recorder=usage_recorder,
            model_router=model_router,
        )
        return StreamingResponse(gen, media_type="text/event-stream")

    # Extract request headers for Cache-Control: no-cache detection
    req_headers = {k.lower(): v for k, v in request.headers.items()}
    # Resolve cache TTL and metrics registry from app state (fail-open defaults).
    # A per-request Cache-Control: max-age may lower/raise the TTL within the cap.
    cache_ttl = getattr(getattr(request.app, "state", None), "cache_ttl_seconds", 300)
    cache_max_ttl = getattr(getattr(request.app, "state", None), "cache_max_ttl_seconds", 86400)
    effective_ttl = resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)
    metrics_registry = getattr(getattr(request.app, "state", None), "metrics_registry", None)

    status, response_body, x_cache = await use_case.complete(
        raw_key=raw_key,
        body=body,
        upstream=upstream,
        usage_recorder=usage_recorder,
        cache_ttl_seconds=effective_ttl,
        metrics_registry=metrics_registry,
        request_headers=req_headers,
        model_router=model_router,
    )
    # Upstream 4xx: pass through verbatim — JSONResponse with upstream status
    resp = JSONResponse(content=response_body, status_code=status)
    if x_cache is not None:
        resp.headers["x-cache"] = x_cache
    return resp
