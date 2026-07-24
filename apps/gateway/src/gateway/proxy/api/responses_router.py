"""API router for the OpenAI Responses-wire ingress: POST /v1/responses.

Contract FROZEN @ v1 (responses-api-core PLAN.md §3).

Mirrors the per-modality router-FILE convention (messages_router.py /
embeddings_router.py / images_router.py) rather than growing router.py.

Ingress-is-translation-only (milestone invariant): this endpoint reuses
``CompletionUseCase.complete()/.stream()`` — the governance/router/recorder
chokepoint — COMPLETELY UNCHANGED. It translates the Responses wire shape at the
boundary and nothing else, so a request not using /v1/responses engages ZERO new
plumbing (M8 byte-identical default path).

Unlike the Anthropic ingress, /v1/responses stays in the OpenAI-dialect family:
every gateway-generated rejection (authn, model, budget, rate-limit, credit,
tier, residency, guardrail) is left to propagate as the SAME ProblemError the
chat seam raises, rendered by the shared ``on_problem`` handler as byte-identical
problem+json (M7). Only the four wire-local 400 rejects (background · hosted
tool · store/previous_response_id · malformed input) are raised here, each via
its frozen catalog code.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from gateway.core.error_catalog import (
    ErrorSpec,
    RESPONSES_BACKGROUND_UNSUPPORTED,
    RESPONSES_PAYLOAD_INVALID,
    RESPONSES_STORE_UNSUPPORTED,
    RESPONSES_TOOL_UNSUPPORTED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.proxy.api.deps import (
    get_completion_upstream,
    get_completion_use_case,
    get_raw_key_ingress,
    get_usage_recorder,
)
from gateway.proxy.application.json_sanitize import sanitize_non_finite
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import BatchDivertedStream, CompletionUpstream, UsageRecorder
from gateway.proxy.infrastructure.openai_responses_ingress import (
    ResponsesIngressError,
    chat_response_to_responses,
    responses_request_to_chat,
    translate_chat_stream_to_responses,
    validate_responses_request,
)
from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl

_log = logging.getLogger(__name__)

responses_router = APIRouter(tags=["proxy"])

# Map an ingress reject code onto its frozen catalog ErrorSpec (all 400).
_CODE_TO_SPEC: dict[str, ErrorSpec] = {
    RESPONSES_PAYLOAD_INVALID.code: RESPONSES_PAYLOAD_INVALID,
    RESPONSES_BACKGROUND_UNSUPPORTED.code: RESPONSES_BACKGROUND_UNSUPPORTED,
    RESPONSES_TOOL_UNSUPPORTED.code: RESPONSES_TOOL_UNSUPPORTED,
    RESPONSES_STORE_UNSUPPORTED.code: RESPONSES_STORE_UNSUPPORTED,
}


@responses_router.post("/v1/responses")
async def responses(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    upstream: Annotated[CompletionUpstream, Depends(get_completion_upstream)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_key_ingress)],
) -> Any:
    """POST /v1/responses — OpenAI Responses-wire ingress, streaming + non-streaming.

    Translates the Responses body into the internal chat body, then calls
    CompletionUseCase.complete()/.stream() UNCHANGED — same authn, catalog/
    allowlist, budget, rate-limit, credit, tier, residency, guardrail, cache, and
    usage-recording path /v1/chat/completions uses. Response/SSE are translated
    back to the Responses wire at this boundary only.
    """
    # Safety rule (PLAN.md §1 R:ERR_PAYLOAD_INVALID): the body is parsed, rejected,
    # and translated to completion BEFORE any governance call — a malformed body
    # never reaches, or partially consumes, an authn/budget/credit hold.
    try:
        raw_body: Any = await request.json()
    except Exception:
        raise RESPONSES_PAYLOAD_INVALID.exc(detail="Request body must be valid JSON")

    try:
        validate_responses_request(raw_body)
        internal_body = responses_request_to_chat(raw_body)
    except ResponsesIngressError as exc:
        spec = _CODE_TO_SPEC.get(exc.code, RESPONSES_PAYLOAD_INVALID)
        raise spec.exc(detail=exc.message) from exc

    stream_requested = bool(raw_body.get("stream", False))
    served_model = str(internal_body.get("model") or "")
    req_headers = {k.lower(): v for k, v in request.headers.items()}
    model_router = getattr(getattr(request.app, "state", None), "model_router", None)

    if stream_requested:
        # ProblemError (governance rejection) propagates to the shared handler —
        # byte-identical problem+json to the chat seam (M7).
        gen = await use_case.stream(
            raw_key=raw_key,
            body=internal_body,
            upstream=upstream,
            usage_recorder=usage_recorder,
            request_headers=req_headers,
            model_router=model_router,
        )
        translated = translate_chat_stream_to_responses(
            gen, served_model=served_model, raw_request=raw_body
        )
        return StreamingResponse(translated, media_type="text/event-stream")

    cache_ttl = getattr(getattr(request.app, "state", None), "cache_ttl_seconds", 300)
    cache_max_ttl = getattr(getattr(request.app, "state", None), "cache_max_ttl_seconds", 86400)
    effective_ttl = resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)
    metrics_registry = getattr(getattr(request.app, "state", None), "metrics_registry", None)

    # batch_processor is NEVER passed (PLAN.md §3): a /v1/responses request never batch-diverts.
    status, response_body, _x_cache = await use_case.complete(
        raw_key=raw_key,
        body=internal_body,
        upstream=upstream,
        usage_recorder=usage_recorder,
        cache_ttl_seconds=effective_ttl,
        metrics_registry=metrics_registry,
        request_headers=req_headers,
        model_router=model_router,
    )

    if isinstance(response_body, BatchDivertedStream):
        # Defensive: /v1/responses never diverts (no batch_processor). Fail closed
        # rather than leak an internal chat SSE body onto the Responses wire.
        raise UPSTREAM_UNAVAILABLE.exc()

    response_body, _nf = sanitize_non_finite(response_body)
    if _nf:
        _log.warning(
            "responses_nonfinite_sanitized",
            extra={"model": served_model, "count": _nf},
        )

    if status >= 400:
        # Upstream 4xx/5xx pass-through verbatim — both dialects share the OpenAI
        # error envelope (PLAN.md §1 M7). CompletionUseCase.complete only RAISES
        # ProblemError for governance rejections; an upstream 4xx is data.
        return JSONResponse(content=response_body, status_code=status)

    responses_body = chat_response_to_responses(
        response_body, served_model=served_model, raw_request=raw_body
    )
    return JSONResponse(content=responses_body, status_code=200)
