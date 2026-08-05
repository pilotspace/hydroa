"""API router for the OpenAI Responses-wire ingress: POST /v1/responses.

Contract FROZEN @ v1 (responses-api-core PLAN.md §3 + responses-state-store PLAN.md §3).

Mirrors the per-modality router-FILE convention (messages_router.py /
embeddings_router.py / images_router.py) rather than growing router.py.

Ingress-is-translation-only (milestone invariant): this endpoint reuses
``CompletionUseCase.complete()/.stream()`` — the governance/router/recorder
chokepoint — COMPLETELY UNCHANGED. It translates the Responses wire shape at the
boundary and, when ``store``/``previous_response_id`` are present (the wave-2
state extension point owned by responses-state-store), orchestrates the ZDR gate,
chain resolution, depth/size caps, and persistence AROUND that unchanged dial. A
request using NEITHER field engages ZERO stored_responses reads or writes (M9
byte-identical default path).

Unlike the Anthropic ingress, /v1/responses stays in the OpenAI-dialect family:
every gateway-generated rejection (authn, model, budget, rate-limit, credit,
tier, residency, guardrail) is left to propagate as the SAME ProblemError the
chat seam raises, rendered by the shared ``on_problem`` handler as byte-identical
problem+json. Only the wire-local 400 rejects and the state-store rejects (404
not-found · 400 chain-too-deep · 413 context-too-large · 403 ZDR · 500 store-failed)
are raised here, each via its frozen catalog code.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    RESPONSE_NOT_FOUND,
    RESPONSES_BACKGROUND_UNSUPPORTED,
    RESPONSES_CHAIN_TOO_DEEP,
    RESPONSES_CONTEXT_TOO_LARGE,
    RESPONSES_PAYLOAD_INVALID,
    RESPONSES_STORE_FAILED,
    RESPONSES_STORE_UNSUPPORTED,
    RESPONSES_TOOL_UNSUPPORTED,
    UPSTREAM_UNAVAILABLE,
    ErrorSpec,
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
from gateway.responses_store.api.router import authenticate_bearer
from gateway.responses_store.application.chaining import (
    exceeds_depth_cap,
    exceeds_size_cap,
    materialize_context,
)
from gateway.responses_store.application.persistence import (
    persist_stored_response,
    wrap_streaming_persist,
)
from gateway.responses_store.infrastructure.repository import StoredResponseRepository
from gateway.tenants.application.retention_policy import raise_if_zdr

_log = logging.getLogger(__name__)

responses_router = APIRouter(tags=["proxy"])

# Map an ingress reject code onto its frozen catalog ErrorSpec (all 400).
_CODE_TO_SPEC: dict[str, ErrorSpec] = {
    RESPONSES_PAYLOAD_INVALID.code: RESPONSES_PAYLOAD_INVALID,
    RESPONSES_BACKGROUND_UNSUPPORTED.code: RESPONSES_BACKGROUND_UNSUPPORTED,
    RESPONSES_TOOL_UNSUPPORTED.code: RESPONSES_TOOL_UNSUPPORTED,
    RESPONSES_STORE_UNSUPPORTED.code: RESPONSES_STORE_UNSUPPORTED,
}


class _ChainPlan:
    """The pre-dial state decision for one POST (store gate + chain materialization)."""

    __slots__ = ("chain_depth", "key_id", "previous_response_id", "store", "tenant_id")

    def __init__(
        self,
        *,
        store: bool,
        previous_response_id: str | None,
        chain_depth: int,
        tenant_id: uuid.UUID | None,
        key_id: uuid.UUID | None,
    ) -> None:
        self.store = store
        self.previous_response_id = previous_response_id
        self.chain_depth = chain_depth
        self.tenant_id = tenant_id
        self.key_id = key_id


async def _prepare_state(
    *,
    raw_body: dict[str, Any],
    internal_body: dict[str, Any],
    session: AsyncSession,
    raw_key: str | None,
) -> _ChainPlan:
    """Run the pre-dial state gate: authn → ZDR (store path) → chain resolve + caps.

    Mutates ``internal_body['messages']`` in place to the materialized chain context
    when ``previous_response_id`` is present. Raises the frozen ProblemError for every
    reject (403 ZDR · 404 not-found · 400 chain-too-deep · 413 context-too-large) BEFORE
    any upstream dial — zero provider cost, zero usage rows on every reject.
    """
    store = raw_body.get("store") is True
    prev_raw = raw_body.get("previous_response_id")
    prev_id = prev_raw if isinstance(prev_raw, str) and prev_raw != "" else None

    if not store and prev_id is None:
        # Byte-identical default path (M9): no authn, no ZDR read, no chain SELECT.
        return _ChainPlan(
            store=False,
            previous_response_id=None,
            chain_depth=0,
            tenant_id=None,
            key_id=None,
        )

    authz = await authenticate_bearer(session, raw_key or "")

    # ZDR fail-closed is the FIRST gate on the store path (raise_if_zdr ordering rule):
    # BEFORE chain resolution and BEFORE the governance dial (Tin's recorded decision).
    if store:
        await raise_if_zdr(session, authz.tenant_id)

    chain_depth = 0
    if prev_id is not None:
        repo = StoredResponseRepository(session)
        prev = await repo.resolve_prev(tenant_id=authz.tenant_id, response_id=prev_id)
        if prev is None:
            # unknown | cross-tenant | deleted | malformed — the SINGLE 404. Because the
            # resolve is tenant-scoped, a foreign/unknown id can NEVER reach the depth/size
            # checks below (the evaluation-order oracle closure).
            raise RESPONSE_NOT_FOUND.exc()
        if exceeds_depth_cap(prev.chain_depth):
            raise RESPONSES_CHAIN_TOO_DEEP.exc()
        materialized = materialize_context(
            prev_context_messages=prev.context_messages,
            prev_response_body=prev.response_body,
            new_messages=internal_body["messages"],
        )
        if exceeds_size_cap(materialized):
            raise RESPONSES_CONTEXT_TOO_LARGE.exc()
        internal_body["messages"] = materialized
        chain_depth = prev.chain_depth + 1

    return _ChainPlan(
        store=store,
        previous_response_id=prev_id,
        chain_depth=chain_depth,
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
    )


@responses_router.post("/v1/responses")
async def responses(
    request: Request,
    use_case: Annotated[CompletionUseCase, Depends(get_completion_use_case)],
    upstream: Annotated[CompletionUpstream, Depends(get_completion_upstream)],
    usage_recorder: Annotated[UsageRecorder, Depends(get_usage_recorder)],
    raw_key: Annotated[str | None, Depends(get_raw_key_ingress)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Any:
    """POST /v1/responses — OpenAI Responses-wire ingress, streaming + non-streaming.

    Translates the Responses body into the internal chat body, then calls
    CompletionUseCase.complete()/.stream() UNCHANGED — same authn, catalog/
    allowlist, budget, rate-limit, credit, tier, residency, guardrail, cache, and
    usage-recording path /v1/chat/completions uses. When store/previous_response_id
    are present, the ZDR gate, chain resolution, caps, and persistence wrap that dial.
    """
    # Safety rule (PLAN.md §1 R:ERR_PAYLOAD_INVALID): the body is parsed, rejected,
    # and translated to completion BEFORE any governance call — a malformed body
    # never reaches, or partially consumes, an authn/budget/credit hold.
    try:
        raw_body: Any = await request.json()
    except Exception:
        # `from None` deliberately: the parser's own exception must not become the
        # __cause__ of a client-facing error, or its internals surface in traces.
        # The response body is unchanged.
        raise RESPONSES_PAYLOAD_INVALID.exc(detail="Request body must be valid JSON") from None

    try:
        validate_responses_request(raw_body)
        internal_body = responses_request_to_chat(raw_body)
    except ResponsesIngressError as exc:
        spec = _CODE_TO_SPEC.get(exc.code, RESPONSES_PAYLOAD_INVALID)
        raise spec.exc(detail=exc.message) from exc

    # Pre-dial state gate (ZDR · chain resolve · caps). Mutates internal_body['messages']
    # in place for a chained turn. Every reject here is pre-dial (no usage row).
    plan = await _prepare_state(
        raw_body=raw_body,
        internal_body=internal_body,
        session=session,
        raw_key=raw_key,
    )

    stream_requested = bool(raw_body.get("stream", False))
    served_model = str(internal_body.get("model") or "")
    req_headers = {k.lower(): v for k, v in request.headers.items()}
    model_router = getattr(getattr(request.app, "state", None), "model_router", None)

    if stream_requested:
        # ProblemError (governance rejection) propagates to the shared handler —
        # byte-identical problem+json to the chat seam.
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
        if plan.store and plan.tenant_id is not None and plan.key_id is not None:
            # Persist the row BEFORE the terminal response.completed frame (M2); a
            # persist failure emits response.failed(ERR_RESPONSES_STORE_FAILED) instead.
            # Uses the app session_factory — the request session is closed by stream end.
            translated = wrap_streaming_persist(
                translated,
                session_factory=request.app.state.sessionmaker,
                context_messages=internal_body["messages"],
                tenant_id=plan.tenant_id,
                key_id=plan.key_id,
                served_model=served_model,
                previous_response_id=plan.previous_response_id,
                chain_depth=plan.chain_depth,
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
        # error envelope. CompletionUseCase.complete only RAISES ProblemError for
        # governance rejections; an upstream 4xx is data. A failed round-trip is
        # never persisted (no stored row for a non-2xx).
        return JSONResponse(content=response_body, status_code=status)

    responses_body = chat_response_to_responses(
        response_body, served_model=served_model, raw_request=raw_body
    )
    # Echo the state truthfully (M1): store + previous_response_id.
    responses_body["store"] = plan.store
    responses_body["previous_response_id"] = plan.previous_response_id

    if plan.store and plan.tenant_id is not None and plan.key_id is not None:
        # Persist in-request BEFORE the 200 returns (M1). A persist failure is a loud 500
        # ERR_RESPONSES_STORE_FAILED — the one usage row already recorded stands (the
        # provider was served); nothing half-written (the insert is atomic).
        try:
            await persist_stored_response(
                request.app.state.sessionmaker,
                response_id=str(responses_body["id"]),
                tenant_id=plan.tenant_id,
                key_id=plan.key_id,
                model=served_model,
                status=str(responses_body.get("status") or "completed"),
                previous_response_id=plan.previous_response_id,
                chain_depth=plan.chain_depth,
                context_messages=internal_body["messages"],
                response_body=responses_body,
                usage=responses_body.get("usage"),
            )
        except Exception as exc:
            _log.warning("responses_store_persist_failed", extra={"model": served_model})
            raise RESPONSES_STORE_FAILED.exc() from exc

    return JSONResponse(content=responses_body, status_code=200)
