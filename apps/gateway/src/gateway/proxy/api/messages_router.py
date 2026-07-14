"""API router for the Anthropic-wire ingress: POST /v1/messages (+count_tokens).

Contract FROZEN @ v1 (anthropic-messages-ingress TASK.md §3).

Mirrors the per-modality router-FILE convention (embeddings_router.py /
images_router.py / audio_router.py) rather than growing router.py.

Ingress-is-translation-only (milestone invariant): both endpoints reuse
CompletionUseCase's governance/router/recorder chokepoint completely
UNCHANGED — this file translates wire shapes at the boundary and nothing
else. All gateway-generated errors are the Anthropic error envelope
(`{"type":"error","error":{...}}`) — a documented, precedented exception to
the project-wide RFC 9457 problem+json convention (core/errors.py), same
class as the SCIM `/scim/v2/*` carve-out (scim/api/errors.py). No raw
problem+json body ever reaches an Anthropic-wire client.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.proxy.api.deps import (
    get_completion_upstream,
    get_completion_use_case,
    get_raw_key_ingress,
    get_usage_recorder,
)
from gateway.proxy.api.messages_deps import ANTHROPIC_PROVIDER, resolve_model_provider
from gateway.proxy.application.json_sanitize import sanitize_non_finite
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.ports import BatchDivertedStream, CompletionUpstream, UsageRecorder
from gateway.proxy.infrastructure.anthropic_ingress import (
    AnthropicIngressError,
    anthropic_error_body,
    anthropic_messages_request_to_openai,
    anthropic_response_from_openai,
    estimate_input_tokens,
    translate_openai_stream_to_anthropic,
)
from gateway.proxy.infrastructure.response_cache import resolve_cache_ttl

_log = logging.getLogger(__name__)

messages_router = APIRouter(tags=["proxy"])


def _anthropic_error_response(status: int, code: str, message: str) -> JSONResponse:
    body = anthropic_error_body(code, message, status=status)
    return JSONResponse(content=body, status_code=status)


def _problem_to_anthropic_response(exc: ProblemError) -> JSONResponse:
    """Translate a ProblemError (raised by the reused governance layer) into the
    Anthropic error envelope, SAME HTTP status, SAME headers (e.g. Retry-After).

    M9 boundary: every ERR_* ProblemError /v1/chat/completions already raises
    for auth/model/budget/rate-limit/guardrail/residency/tier rejections is
    caught HERE — never leaking a raw problem+json body to an Anthropic-wire
    client.
    """
    resp = JSONResponse(
        content=anthropic_error_body(exc.code, exc.title, status=exc.status),
        status_code=exc.status,
    )
    for key, val in exc.headers.items():
        resp.headers[key] = val
    return resp


async def _translate_request_body(
    request: Request, *, require_max_tokens: bool
) -> tuple[dict[str, Any] | None, bool, JSONResponse | None]:
    """Parse + translate the client body. Returns (internal_body, stream_requested,
    None) on success or (None, False, error_response) on any R1 malformed-body
    condition.

    Safety rule (TASK.md §5): this ALWAYS runs to completion (or fails)
    BEFORE any governance call — a malformed body never reaches, or
    partially consumes, an authn/budget/rate-limit/credit hold.
    """
    try:
        raw_body: Any = await request.json()
    except Exception:
        return (
            None,
            False,
            _anthropic_error_response(
                400, "ERR_PAYLOAD_INVALID", "Request body must be valid JSON"
            ),
        )
    stream_requested = bool(raw_body.get("stream", False)) if isinstance(raw_body, dict) else False
    try:
        internal_body = anthropic_messages_request_to_openai(
            raw_body, require_max_tokens=require_max_tokens
        )
    except AnthropicIngressError as exc:
        return None, False, _anthropic_error_response(400, "ERR_PAYLOAD_INVALID", exc.message)
    return internal_body, stream_requested, None


@messages_router.post("/v1/messages")
async def messages(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    upstream: Annotated[CompletionUpstream, Depends(get_completion_upstream)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_key_ingress)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """POST /v1/messages — Anthropic-wire ingress, streaming and non-streaming.

    Translates the client's Anthropic Messages body into the internal
    OpenAI-shape body, then calls CompletionUseCase.complete()/.stream()
    UNCHANGED (M2) — same authn, catalog/allowlist, budget, rate-limit,
    credit, tier, residency, guardrail, cache, and usage-recording path
    `/v1/chat/completions` uses. Response/SSE/error are translated back to
    Anthropic-wire at this boundary only.
    """
    internal_body, stream_requested, err = await _translate_request_body(
        request, require_max_tokens=True
    )
    if err is not None:
        return err
    assert internal_body is not None  # narrows for the type checker; err is None here

    # M7/M8: gate thinking/cache_control forwarding on the resolved provider.
    # Only strip on POSITIVE knowledge the candidate is non-Anthropic — a catalog
    # lookup MISS (e.g. a preset/alias selector `_resolve_preset` will resolve
    # LATER, inside complete()/stream(), not yet a literal catalog id here)
    # defaults to "forward" and is harmless: every non-Anthropic adapter already
    # ignores both fields (see anthropic_ingress.py docstring's disclosed gap).
    provider = await resolve_model_provider(session, internal_body["model"])
    if provider is not None and provider != ANTHROPIC_PROVIDER:
        internal_body.pop("thinking", None)
        _strip_cache_control(internal_body)

    req_headers = {k.lower(): v for k, v in request.headers.items()}
    model_router = getattr(getattr(request.app, "state", None), "model_router", None)

    if stream_requested:
        try:
            gen = await use_case.stream(
                raw_key=raw_key,
                body=internal_body,
                upstream=upstream,
                usage_recorder=usage_recorder,
                request_headers=req_headers,
                model_router=model_router,
            )
        except ProblemError as exc:
            return _problem_to_anthropic_response(exc)
        translated = translate_openai_stream_to_anthropic(gen)
        return StreamingResponse(translated, media_type="text/event-stream")

    cache_ttl = getattr(getattr(request.app, "state", None), "cache_ttl_seconds", 300)
    cache_max_ttl = getattr(getattr(request.app, "state", None), "cache_max_ttl_seconds", 86400)
    effective_ttl = resolve_cache_ttl(req_headers, cache_ttl, cache_max_ttl)
    metrics_registry = getattr(getattr(request.app, "state", None), "metrics_registry", None)

    try:
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
    except ProblemError as exc:
        return _problem_to_anthropic_response(exc)

    if isinstance(response_body, BatchDivertedStream):
        # batch-window-grouping is a chat/completions-only diversion surface;
        # undefined for a non-streaming Anthropic-wire request. Fail closed
        # rather than leak an OpenAI-shaped stream body.
        return _anthropic_error_response(502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream unavailable")

    response_body, _nf = sanitize_non_finite(response_body)
    if _nf:
        _log.warning(
            "anthropic_messages_nonfinite_sanitized",
            extra={"model": internal_body.get("model"), "count": _nf},
        )

    if status >= 400:
        # Upstream 4xx/5xx pass-through (CompletionUseCase.complete only RAISES
        # ProblemError for governance rejections — an upstream-returned 4xx is
        # data, not an exception). The internal body's own error message (when
        # OpenAI-shaped) is preserved; anything else degrades to a generic
        # message rather than leaking a non-Anthropic shape verbatim.
        message = "Upstream error"
        if isinstance(response_body, dict):
            err_obj = response_body.get("error")
            if isinstance(err_obj, dict) and isinstance(err_obj.get("message"), str):
                message = err_obj["message"]
            elif isinstance(err_obj, str):
                message = err_obj
        return _anthropic_error_response(status, "ERR_UPSTREAM_ERROR", message)

    anthropic_body = anthropic_response_from_openai(
        response_body if isinstance(response_body, dict) else {}
    )
    return JSONResponse(content=anthropic_body, status_code=200)


def _strip_cache_control(internal_body: dict[str, Any]) -> None:
    """Remove `cache_control` keys from every content part (M8: only carried
    through to the resolved Anthropic candidate)."""
    for msg in internal_body.get("messages", []):
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    part.pop("cache_control", None)


@messages_router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    raw_key: Annotated[str | None, Depends(get_raw_key_ingress)],
) -> Any:
    """POST /v1/messages/count_tokens — Anthropic-wire token estimate.

    Freeze decision (Tin 2026-07-14, TASK.md §3 M12): passes the SAME
    admission gates as /v1/messages (authn, model-check, budget, rate-limit,
    plan/credit gates) — an over-budget/over-limit/no-credit tenant gets the
    identical structured refusal a chat call would get. On SUCCESS ONLY:
    billed $0, writes NO usage_records row (no completion is ever produced) —
    the credit + tier-capacity holds _enforce_governance placed are released
    immediately (mirrors _settle_or_release_hold's "free" branch) so nothing
    is left stranded.
    """
    internal_body, _stream_requested, err = await _translate_request_body(
        request, require_max_tokens=False
    )
    if err is not None:
        return err
    assert internal_body is not None

    request_id = uuid.uuid4()
    model_router = getattr(getattr(request.app, "state", None), "model_router", None)
    model_groups = model_router.model_groups if model_router is not None else None

    try:
        # Reuses CompletionUseCase's OWN governance primitives, unchanged —
        # the exact admission sequence complete()/stream() run, minus the
        # upstream dial and the terminal usage_recorder.record() call
        # (TASK.md §5 Strategy item 4). use_cases.py is INVIOLABLE (frozen,
        # never modified for this task) — reaching into its private governance
        # methods (rather than adding a public seam) is the same accepted
        # house pattern as embeddings_use_case/audio-endpoints/images-endpoint
        # (precedent: `# pyright: ignore[reportPrivateUsage]` throughout).
        authz = await use_case._authenticate(raw_key)  # pyright: ignore[reportPrivateUsage]
        await use_case._resolve_preset(  # pyright: ignore[reportPrivateUsage]
            internal_body, authz.tenant_id
        )
        model_id, _messages, _ = await use_case._validate_payload(  # pyright: ignore[reportPrivateUsage]
            internal_body
        )
        await use_case._enforce_governance(  # pyright: ignore[reportPrivateUsage]
            authz,
            model_id,
            use_case._budget_guard,  # pyright: ignore[reportPrivateUsage]
            model_groups,
            request_id=request_id,
        )
    except ProblemError as exc:
        return _problem_to_anthropic_response(exc)

    await use_case._credit_guard.release(  # pyright: ignore[reportPrivateUsage]
        authz.tenant_id, request_id
    )
    await use_case._tier_capacity_guard.release(  # pyright: ignore[reportPrivateUsage]
        authz.tenant_id, request_id
    )

    return JSONResponse(
        content={"input_tokens": estimate_input_tokens(internal_body)}, status_code=200
    )
