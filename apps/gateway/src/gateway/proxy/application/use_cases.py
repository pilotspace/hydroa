"""Application use cases for the proxy module.

The CompletionUseCase is the single orchestrator:
  1. Authenticate key → tenant/key ids + governance fields (AuthzResult)
  2. Enforce governance: expiry → 401, model allowlist → 403, per-key budget → 402
  3. Validate payload (model, messages)
  4. Check model is active in catalog
  5. Delegate to CompletionUpstream (circuit-breaker-wrapped via BoundCircuitBreakerUpstream)
  6. Fire-and-forget UsageRecorder

The circuit breaker lives in the infrastructure layer (BoundCircuitBreakerUpstream),
so this layer only sees UpstreamUnavailableError or CircuitOpenError on failures.

Governance enforcement order (M8-M10, M12):
  expiry check → model allowlist check → per-key budget check → tenant budget check
  All governance fields come from AuthzResult (zero extra DB queries, M12).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Protocol

import structlog.contextvars

from gateway.budgets.domain.ports import BudgetGuard, PassthroughBudgetGuard
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    BANDWIDTH_EXHAUSTED,
    BUDGET_EXCEEDED,
    GUARDRAIL_BLOCKED,
    INVALID_JSON_SCHEMA,
    MODEL_DISABLED,
    MODEL_MODALITY_MISMATCH,
    MODEL_NOT_ALLOWED,
    MODEL_UNKNOWN,
    OUTPUT_SCHEMA_VALIDATION_FAILED,
    OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA,
    OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM,
    PAYLOAD_MESSAGES_REQUIRED,
    PAYLOAD_MODEL_REQUIRED,
    PRESET_NOT_FOUND,
    RATE_LIMITED,
    UPSTREAM_RATE_LIMITED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.logs.domain.sse_content_extractor import extract_content_from_sse
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import (
    AllDeploymentsSaturatedError,
    CircuitOpenError,
    UpstreamRateLimitedError,
    UpstreamUnavailableError,
)
from gateway.proxy.domain.guardrail_tenant_context import (
    reset_guardrail_tenant_id,
    set_guardrail_tenant_id,
)
from gateway.proxy.domain.model_presets import TenantModelPresetStore, parse_preset_selector
from gateway.proxy.domain.output_validation import (
    check_schema_well_formed,
    truncate_raw_output,
    validate_model_output,
)
from gateway.proxy.domain.ports import (
    BatchDiversionPort,
    BatchDivertedStream,
    ChatModalityLookup,
    CompletionUpstream,
    GuardrailEvaluator,
    InputModalityLookup,
    KeyAuthenticator,
    ModelAccess,
    ModelChecker,
    PayloadCapturePort,
    ProviderResolver,
    ResponseCache,
    TenantCredentialResolver,
    UsageRecorder,
    UsageRecordExtras,
    VectorCache,
)
from gateway.proxy.domain.provider_credentials import (
    BYOK_PROVIDERS,
    ProviderKeyMissing,
)
from gateway.proxy.domain.response_format_translation import extract_response_format
from gateway.rate_limits.application.passthrough import (
    PassthroughBandwidthBucket,
    PassthroughRateLimiter,
)
from gateway.rate_limits.domain.errors import (
    BandwidthExhaustedError,
    RateLimitExceededError,
)
from gateway.rate_limits.domain.ports import BandwidthBucket, BandwidthGrant, RateLimiter
from gateway.usage.domain.extractor import (
    extract_generation_id_from_sse,
    extract_usage_from_sse,
    stream_usage_is_complete,
)
from gateway.usage.domain.partial_usage import (
    partial_stream_usage,
    read_partial_usage,
)

if TYPE_CHECKING:
    from gateway.observability.otel import OtelSpanEmitter
    from gateway.proxy.application.fallback_router import FallbackModelRouter

_log = logging.getLogger(__name__)
_ZERO = Decimal("0")


def _sse_error_frame(code: str, message: str) -> bytes:
    """Return a single SSE data line carrying an OpenAI-shaped error object.

    Format mirrors the adapter convention in anthropic_upstream.py:
        b"data: " + json.dumps(payload).encode() + b"\\n\\n"
    """
    payload = {"error": {"message": message, "type": "upstream_error", "code": code}}
    return b"data: " + json.dumps(payload).encode() + b"\n\n"


# ---------------------------------------------------------------------------
# Span emission helper — fire-and-forget, never raises into request path
# ---------------------------------------------------------------------------


def _emit_span_fire_forget(
    span_emitter: OtelSpanEmitter,
    authz: AuthzResult,
    model_id: str,
    status_code: int,
    stream: bool,
    cached: bool,
    guardrail_blocked: bool,
    start_ns: int,
    error_code: str | None = None,
    fallback: bool = False,
) -> None:
    """Build an OtelSpan and schedule fire-and-forget emission.

    Must never raise — all errors are swallowed so observability never
    affects the request path.
    """
    try:
        from gateway.observability.otel import OtelSpan

        end_ns = time.time_ns()
        trace_id = os.urandom(16).hex()
        span_id = os.urandom(8).hex()
        team_id_str = str(authz.team_id) if authz.team_id is not None else None
        span = OtelSpan(
            trace_id=trace_id,
            span_id=span_id,
            name="proxy.completion",
            start_time_ns=start_ns,
            end_time_ns=end_ns,
            tenant_id=str(authz.tenant_id),
            key_id=str(authz.key_id),
            team_id=team_id_str,
            model=model_id,
            status_code=status_code,
            stream=stream,
            cached=cached,
            guardrail_blocked=guardrail_blocked,
            error_code=error_code,
            fallback=fallback,
        )
        task = asyncio.ensure_future(span_emitter.emit(span))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
    except Exception:  # noqa: S110
        pass


def _fire_record_tpm(
    rate_limiter: RateLimiter,
    *,
    key_id: uuid.UUID,
    tokens: int,
) -> None:
    """Schedule a fire-and-forget TPM token record.

    Swallows all errors — post-stream accounting must never fail a request.
    """
    task = asyncio.ensure_future(rate_limiter.record_tpm(key_id, tokens))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _fire_bandwidth_reconcile(
    bucket: BandwidthBucket,
    *,
    key_id: uuid.UUID,
    estimate: int,
    real_tokens: int,
) -> None:
    """Schedule a fire-and-forget estimate->real bandwidth reconcile (bandwidth-usage-reconcile).

    Applies the signed delta (estimate minus real_tokens) to the per-key bucket so its level
    reflects the REAL usage, not the pacing estimate. NO-OP unless both estimate>0 and real_tokens>0
    (no truth to correct toward). Mirrors _fire_record_tpm: never blocks the response, swallows the
    task exception (the bucket also swallows Redis errors). Passthrough.reconcile is itself a no-op.
    """
    if estimate <= 0 or real_tokens <= 0:
        return
    grant = BandwidthGrant(key_id=key_id, consumed=estimate, waited_s=0.0)
    task = asyncio.ensure_future(bucket.reconcile(key_id, grant, real_tokens))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _dispatch_record(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    status: int,
    extras: UsageRecordExtras | None = None,
) -> None:
    """Schedule a fire-and-forget usage record, forwarding only declared extras.

    Stores the Task reference to satisfy RUF006 (avoids garbage-collected task).
    Failures in the recorder are intentionally ignored — recording must never
    affect the caller's response.

    Typed capability seam (UsageRecordExtras): extras are filtered against the
    recorder's `supported_extras` class attribute. v1-Protocol test fakes lack
    the attribute (→ empty set) and receive only the base kwargs — same
    backward-compat guarantee as the former inspect.signature introspection,
    now an explicit declaration instead of runtime reflection.
    """
    supported: frozenset[str] = getattr(usage_recorder, "supported_extras", frozenset())
    kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "key_id": key_id,
        "model": model,
        "usage": usage,
        "status": status,
    }
    if extras:
        kwargs.update({k: v for k, v in extras.items() if k in supported})
    task = asyncio.ensure_future(usage_recorder.record(**kwargs))
    # Suppress unhandled-exception noise if recorder raises unexpectedly.
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _fire_record(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    status: int,
    team_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
) -> None:
    """Fire-and-forget usage record; forwards team_id when set (team-governance seam)."""
    extras: UsageRecordExtras = {}
    if team_id is not None:
        extras["team_id"] = team_id
    if request_id is not None:
        extras["request_id"] = request_id
    _dispatch_record(
        usage_recorder,
        tenant_id=tenant_id,
        key_id=key_id,
        model=model,
        usage=usage,
        status=status,
        extras=extras,
    )


def _fire_record_cached(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    team_id: uuid.UUID | None = None,
    request_id: uuid.UUID | None = None,
) -> None:
    """Fire-and-forget usage record for a cache hit (cached=true, cost_usd=0).

    Cost is 0 because the recorder's INCRBYFLOAT guard only runs when
    cost_usd > 0 — no spend counter increment.
    """
    extras: UsageRecordExtras = {"cached": True}
    if team_id is not None:
        extras["team_id"] = team_id
    if request_id is not None:
        extras["request_id"] = request_id
    _dispatch_record(
        usage_recorder,
        tenant_id=tenant_id,
        key_id=key_id,
        model=model,
        usage=usage,
        status=200,
        extras=extras,
    )


def _fire_cache_set(
    cache: Any,
    cache_key: str,
    body: dict[str, Any],
    ttl_seconds: int,
) -> None:
    """Schedule a fire-and-forget cache SET. Errors logged + swallowed in RedisResponseCache."""
    task = asyncio.ensure_future(cache.set(cache_key, body, ttl_seconds))
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


# cache-alias-billing (B6): the served CATALOG candidate is stamped onto the cached VALUE at write
# time so a later cache HIT bills the candidate that produced the body — never the alias (an alias
# has no pricing snapshot -> $0). Reserved key; popped on every hit path so the client can't see it.
_SERVED_STAMP = "__hydroa_served_model__"


def _stamp_served(body: dict[str, Any], served_model_id: str) -> dict[str, Any]:
    """Return a SHALLOW COPY of a cache value carrying the served-candidate stamp.

    The original ``body`` (returned to the client on a MISS) is never mutated.
    """
    return {**body, _SERVED_STAMP: served_model_id}


def _read_served_from_cache(cached: dict[str, Any], model_id: str) -> str:
    """Pop the served-candidate stamp off a fetched cache value; return the billing model id.

    Popping strips the reserved key so it never reaches the client. Falls back to the cached
    provider ``model`` (legacy pre-stamp entries — never the alias), then the request ``model_id``.
    MUST be called BEFORE any post-call guardrail masking (which may return a fresh dict).
    """
    stamped = cached.pop(_SERVED_STAMP, None)
    if isinstance(stamped, str) and stamped:
        return stamped
    fallback = cached.get("model")
    return fallback if isinstance(fallback, str) and fallback else model_id


def _make_error_event(guardrail_configs: dict[str, Any]) -> Any:
    """Build a GuardrailEvent with action='error' for the first active guardrail.

    Used when evaluate_pre() raises unexpectedly — provides a metric event for
    the fail-CLOSED/fail-OPEN code path.
    """
    from gateway.proxy.domain.entities import GuardrailEvent

    first_name = next(iter(guardrail_configs), "unknown")
    return GuardrailEvent(guardrail=first_name, action="error", detail="evaluator raised")


def _fire_guardrail_metrics(
    metrics_registry: Any,
    events: list[Any],
    guardrail_configs: dict[str, Any],
) -> None:
    """Increment guardrail_events_total counter for each event.

    Uses the guardrail_configs to look up the mode for the label.
    Swallows all errors — metrics must never fail a request.
    """
    if metrics_registry is None:
        return
    counter = getattr(metrics_registry, "guardrail_events_total", None)
    if counter is None:
        return
    for event in events:
        try:
            guardrail_name = event.guardrail
            action = event.action
            # Determine the mode from configs; fall back to "unknown" gracefully
            cfg = guardrail_configs.get(guardrail_name, {})
            mode = cfg.get("mode", "unknown") if isinstance(cfg, dict) else "unknown"
            counter.labels(guardrail=guardrail_name, mode=mode, action=action).inc()
        except Exception:  # noqa: S110
            pass


def _fire_record_with_raw(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    status: int,
    team_id: uuid.UUID | None = None,
    guardrail_blocked: bool = False,
    blocked_by: str | None = None,
    pii_masked: bool = False,
    pricing_unit: str | None = None,
    quantity: Decimal | None = None,
    usage_source: str | None = None,
    provider_generation_id: str | None = None,
    disconnect_estimate: bool = False,
    request_id: uuid.UUID | None = None,
) -> None:
    """Fire-and-forget usage record with optional guardrail raw markers.

    guardrail_blocked/blocked_by/pii_masked are forwarded via the typed
    UsageRecordExtras seam (declared-capability filtering in _dispatch_record).

    Additive extension (pricing-units TASK.md §3):
      pricing_unit / quantity are forwarded via UsageRecordExtras when set.
      Chat/embeddings callers pass nothing new — defaults None → per_token path.

    request_id (request-log-metering-fields TASK.md §3): correlation key forwarded
    via the SAME typed extras seam when set.
    """
    extras: UsageRecordExtras = {}
    if team_id is not None:
        extras["team_id"] = team_id
    if guardrail_blocked:
        extras["guardrail_blocked"] = True
    if blocked_by is not None:
        extras["blocked_by"] = blocked_by
    if pii_masked:
        extras["pii_masked"] = True
    if pricing_unit is not None:
        extras["pricing_unit"] = pricing_unit
    if quantity is not None:
        extras["quantity"] = quantity
    if usage_source is not None:
        extras["usage_source"] = usage_source
    if provider_generation_id is not None:
        extras["provider_generation_id"] = provider_generation_id
    if disconnect_estimate:
        extras["disconnect_estimate"] = True
    if request_id is not None:
        extras["request_id"] = request_id
    _dispatch_record(
        usage_recorder,
        tenant_id=tenant_id,
        key_id=key_id,
        model=model,
        usage=usage,
        status=status,
        extras=extras,
    )


async def _run_output_validation_retry(
    *,
    authz: AuthzResult,
    body: dict[str, Any],
    upstream: CompletionUpstream,
    usage_recorder: UsageRecorder,
    model_router: FallbackModelRouter | None,
    model_id: str,
    schema: dict[str, Any],
    status: int,
    response_body: dict[str, Any],
    served_model_id: str,
) -> tuple[int, dict[str, Any], str, str | None]:
    """M5-M8 bounded-retry loop, entered only when attempt 1 (already 200) failed
    schema validation. Extracted as its own function (not inlined in complete())
    so CompletionUseCase.complete() stays within pyright's control-flow complexity
    limit — the mega-method already carries ~15 branches of its own.

    Returns (status, response_body, served_model_id, usage_source_final) for the
    caller to resume its existing flow with — usage_source_final is None (default
    "frame") on a validated success, else "validation_retry" for the caller's own
    final billing call.

    Raises ProblemError exactly like complete()'s own upstream-call handling
    (429/502) for a transport failure on the retry leg, or 422
    ERR_OUTPUT_SCHEMA_VALIDATION_FAILED when both attempts fail validation (M5/M8
    exhausted) — carrying attempt 2's raw output + bounded validation_errors (M12).
    """
    # M8: attempt 1 was a REAL, paid upstream call — bill it before the retry fires
    # (never a free/undisclosed first attempt).
    usage1_raw = response_body.get("usage")
    usage1 = usage1_raw if isinstance(usage1_raw, dict) else None
    _fire_record_with_raw(
        usage_recorder,
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=served_model_id,
        usage=usage1,
        status=status,
        team_id=authz.team_id,
        usage_source="validation_retry",
    )
    # M5/M6/M7: exactly ONE bounded retry, the IDENTICAL routed call, same
    # unmodified body — no re-run of governance/budget/rate-limit (M6); a transport
    # failure raises the SAME path attempt 1 would (M7 — the breaker is never
    # bypassed "because it's just a retry").
    try:
        if model_router is not None:
            retry_status, retry_body, retry_served = await model_router.complete(
                body, upstream=upstream
            )
        else:
            retry_status, retry_body = await upstream.complete(body)
            retry_served = model_id
    except AllDeploymentsSaturatedError as exc:
        raise RATE_LIMITED.exc(
            detail=f"all deployments for '{exc.alias}' are rate-limited",
            headers={"Retry-After": "60"},
        ) from exc
    except UpstreamRateLimitedError as exc:
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=429,
            team_id=authz.team_id,
            usage_source="validation_retry",
        )
        if exc.retry_after is not None:
            raise UPSTREAM_RATE_LIMITED.exc(
                headers={"Retry-After": str(int(exc.retry_after))}
            ) from None
        raise UPSTREAM_RATE_LIMITED.exc() from None
    except (UpstreamUnavailableError, CircuitOpenError):
        # Circuit-breaker proxy has already counted the failure.
        raise UPSTREAM_UNAVAILABLE.exc() from None

    if retry_status != 200:
        # A genuine upstream pass-through on the retry leg (not a validation
        # failure) — never a fabricated 422 for a response the provider itself
        # didn't say was invalid. Billed under validation_retry (it's still the
        # terminal attempt of this bounded-retry loop, M8); caller's existing
        # status==200-gated cache-store/evaluate_post steps no-op naturally.
        return retry_status, retry_body, retry_served, "validation_retry"

    retry_outcome = validate_model_output(schema, retry_body)
    if retry_outcome["valid"]:
        # Attempt 2 succeeded — keeps the DEFAULT "frame" usage_source (M8); caller
        # falls through the rest of complete() (cache-store skip, masking, billing).
        return retry_status, retry_body, retry_served, None

    # M5/M8 exhausted: both attempts failed validation. Bill attempt 2 (also
    # real/paid) then raise the terminal 422 — never a silent free retry, never
    # partial content returned.
    usage2_raw = retry_body.get("usage")
    usage2 = usage2_raw if isinstance(usage2_raw, dict) else None
    _fire_record_with_raw(
        usage_recorder,
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=retry_served,
        usage=usage2,
        status=retry_status,
        team_id=authz.team_id,
        usage_source="validation_retry",
    )
    raise OUTPUT_SCHEMA_VALIDATION_FAILED.exc(
        extra={
            "raw_output": truncate_raw_output(retry_body),
            "validation_errors": retry_outcome["errors"],
        }
    )
def _capture_response_body(collected: list[bytes]) -> dict[str, Any] | None:
    """Build the capture-hook response_body for a streaming exit branch.

    Wraps the assembled assistant text (extract_content_from_sse) in the same
    {"content": "<text>"} shape logs/application/capture_writer.py already recognizes
    for streaming rows — None when no content fragments were found (e.g. an empty or
    pre-first-byte-failed stream), which capture_writer treats as "no response
    content", never a crash.
    """
    content = extract_content_from_sse(collected)
    return {"content": content} if content is not None else None


def _dispatch_capture(
    payload_capture: PayloadCapturePort | None,
    *,
    enabled: bool,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    request_body: dict[str, Any],
    response_body: dict[str, Any] | None,
    status: int,
    stream: bool,
    cached: bool,
    guardrail_configs: dict[str, Any],
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    request_id: uuid.UUID | None = None,
) -> None:
    """Schedule a fire-and-forget payload-capture write (payload-capture-store §3).

    No-op when the port is unwired (None, the default — every existing frozen test
    fake that does not inject one stays byte-identical) or capture is not effectively
    enabled for this tenant/key. All scrub/truncate/ZDR/timeout/concurrency-shed logic
    lives INSIDE the port implementation (SqlAlchemyPayloadCapture) — this call site's
    only job is the enabled-gate + fire-and-forget dispatch, mirroring _dispatch_record's
    idiom exactly (task reference kept to satisfy RUF006; exception suppressed so a
    capture-store failure can never surface as "Task exception was never retrieved").

    usage/latency_ms/request_id (request-log-metering-fields TASK.md §3, additive):
    forwarded verbatim into payload_capture.capture(...) — never recomputed here.
    """
    if payload_capture is None or not enabled:
        return
    task = asyncio.ensure_future(
        payload_capture.capture(
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            request_body=request_body,
            response_body=response_body,
            status=status,
            stream=stream,
            cached=cached,
            guardrail_configs=guardrail_configs,
            usage=usage,
            latency_ms=latency_ms,
            request_id=request_id,
        )
    )
    task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


def _check_expiry(authz: AuthzResult) -> None:
    """Raise ProblemError 401 ERR_AUTH_KEY_EXPIRED if the key has expired (M8).

    Only called after hash-match succeeds (post-identification).
    expires_at <= now() UTC means expired (A7 semantics).
    """
    if authz.expires_at is None:
        return
    now_utc = datetime.datetime.now(datetime.UTC)
    # Ensure expires_at is timezone-aware for comparison
    exp = authz.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=datetime.UTC)
    if exp <= now_utc:
        raise AUTH_KEY_EXPIRED.exc()


def _check_model_allowlist(authz: AuthzResult, model_id: str) -> None:
    """Raise ProblemError 403 ERR_MODEL_NOT_ALLOWED if model is not in allowlist (M9).

    null allowlist = all models allowed.
    empty [] = no models allowed (security-strict, A3).
    """
    if authz.model_allowlist is None:
        # null = unlimited — all models allowed
        return
    if model_id not in authz.model_allowlist:
        raise MODEL_NOT_ALLOWED.exc()


def _parse_spend(raw: bytes | str | None) -> Decimal:
    """Parse Redis spend counter value; returns 0 on any failure (fail-open)."""
    if raw is None:
        return _ZERO
    try:
        return Decimal(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (InvalidOperation, AttributeError):
        return _ZERO


async def resolve_provider_credential(
    resolver: TenantCredentialResolver | None,
    tenant_id: Any,
    provider: str,
) -> object | None:
    """Resolve a tenant credential for ``provider`` and set the request contextvar.

    The single source of truth for the credential-resolution-seam §3 gate, shared by
    the chat use-case (``CompletionUseCase._resolve_credential``) and the non-chat
    use-cases (embeddings / images / audio). Returns the contextvar reset Token — the
    caller MUST call ``reset_provider_credential(token)`` in a ``finally`` — or ``None``
    when resolution is SKIPPED (no resolver wired, or a still-env-bound provider).

    Resolves for ALL six providers in ``BYOK_PROVIDERS`` (task-3 dynamic-auth-byok
    extends the task-2 Bearer-only set to include bedrock and azure). Returns ``None``
    only when the resolver is not wired (no credential seam configured).

    Raises:
        ProblemError(402, ERR_PROVIDER_KEY_MISSING): the tenant has no enabled credential
            for the requested provider (absent/disabled/None/resolver-timeout). Fail-closed.
    """
    if resolver is None or provider not in BYOK_PROVIDERS:
        return None
    try:
        cred = await resolver.resolve(tenant_id, provider)
    except ProviderKeyMissing as pkm:
        # §3 CONTRACT: ERR_PROVIDER_KEY_MISSING → HTTP 402 (no secret in the chain).
        raise ProblemError(402, pkm.code, "Provider key not configured for this tenant") from None
    return set_provider_credential(cred)


class _InlineCostRecovery(Protocol):
    """Structural type for the optional inline cost-recovery service (v30 t6.2c).

    Kept structural so the proxy use-case never imports the usage-layer concrete
    OpenRouterCostRecoveryService (no layering dependency). recover() never raises.
    """

    async def recover(
        self,
        *,
        tenant_id: Any,
        key_id: Any,
        model: str,
        provider_generation_id: str,
    ) -> Any: ...


class CompletionUseCase:
    """Orchestrate a single /v1/chat/completions request."""

    def __init__(
        self,
        authenticator: KeyAuthenticator,
        model_checker: ModelChecker,
        budget_guard: BudgetGuard = PassthroughBudgetGuard(),  # noqa: B008
        rate_limiter: RateLimiter | None = None,
        response_cache: ResponseCache | None = None,
        guardrail_evaluator: GuardrailEvaluator | None = None,
        span_emitter: OtelSpanEmitter | None = None,
        stream_resilience_enabled: bool = False,
        vector_cache: VectorCache | None = None,
        tenant_credential_resolver: TenantCredentialResolver | None = None,
        provider_resolver: ProviderResolver | None = None,
        cost_recovery: object | None = None,
        bandwidth_bucket: BandwidthBucket | None = None,
        bandwidth_max_wait_s: float = 0.0,
        web_search_enabled: bool = False,
        input_modality_lookup: InputModalityLookup | None = None,
        input_modality_guard_enabled: bool = False,
        tenant_model_preset_store: TenantModelPresetStore | None = None,
        chat_modality_lookup: ChatModalityLookup | None = None,
        batch_diversion: BatchDiversionPort | None = None,
        output_validation_enabled: bool = False,
        payload_capture: PayloadCapturePort | None = None,
    ) -> None:
        self._authenticator = authenticator
        self._model_checker = model_checker
        self._budget_guard = budget_guard
        self._rate_limiter: RateLimiter = (
            rate_limiter if rate_limiter is not None else PassthroughRateLimiter()
        )
        self._response_cache = response_cache
        self._guardrail_evaluator = guardrail_evaluator
        self._span_emitter = span_emitter
        # Pre-first-byte streaming resilience (streaming-resilience v19). False (default) ⇒
        # the old sync stream() path (byte-identical). True ⇒ peek the first chunk via
        # model_router.stream_resilient so a pre-first-byte failure falls over to the next
        # candidate before StreamingResponse commits.
        self._stream_resilience_enabled = stream_resilience_enabled
        # Embedding-similarity "vector" cache (semantic-cache v19) — the THIRD lookup layer.
        # None (default) ⇒ feature OFF ⇒ complete() is byte-identical (no vector lookup, no embed).
        self._vector_cache = vector_cache
        # Credential resolution seam (credential-resolution-seam §3).
        # None = resolver not wired (frozen test suites / backward compat).
        # When wired, credential is resolved per-request from (tenant_id, provider) and
        # placed in the request-scoped contextvar before the upstream call.
        self._tenant_credential_resolver = tenant_credential_resolver
        self._provider_resolver = provider_resolver
        # openrouter-cost-recovery-wiring (v30 t6.2c): optional inline recovery service.
        # None ⇒ feature off ⇒ byte-identical. Structurally typed (_InlineCostRecovery)
        # so the use-case never depends on the concrete usage-layer service.
        self._cost_recovery: _InlineCostRecovery | None = cost_recovery  # type: ignore[assignment]
        # Per-key bandwidth pacing (stream-bandwidth-pacing, v36). None ⇒ Passthrough ⇒
        # acquire is an immediate no-op grant ⇒ the stream/complete paths are byte-identical
        # to today (zero Redis, zero pacing). max_wait_s comes from settings.
        self._bandwidth_bucket: BandwidthBucket = (
            bandwidth_bucket if bandwidth_bucket is not None else PassthroughBandwidthBucket()
        )
        self._bandwidth_max_wait_s = bandwidth_max_wait_s
        # web-search-grounding (web-search task). False (default) = CENTRAL KNOB-KILL:
        # _strip_web_search_flag() removes the raw web_search flag from the payload before
        # dispatch so the outgoing upstream body is byte-identical to today. True = adapters
        # receive the flag and translate it into provider-native grounding tools.
        self._web_search_enabled = web_search_enabled
        # unsupported-input-guard (unsupported-input-guard task). None + False (defaults) =
        # feature OFF: no catalog lookup, no rejection, byte-identical to today.
        # When enabled, _check_input_modalities() runs after _enforce_governance and BEFORE
        # bandwidth acquire / upstream / usage — a refused request is never billed.
        self._input_modality_lookup: InputModalityLookup | None = input_modality_lookup
        self._input_modality_guard_enabled: bool = input_modality_guard_enabled
        # preset-resolution-ingress (v56 §3): None (default) ⇒ feature off ⇒ complete()/
        # stream() are byte-identical (no resolve() call, no rewrite). When wired, a
        # `<preset>:<alias>` selector in body["model"] is resolved to the tenant's target
        # model BEFORE _validate_payload/governance/catalog/budget/upstream — see complete()
        # and stream() for the single insertion point each.
        self._tenant_model_preset_store: TenantModelPresetStore | None = tenant_model_preset_store
        # chat-modality-guard (v56 §3): None (default) ⇒ feature off ⇒ complete()/stream()
        # are byte-identical (no lookup call, no rejection). When wired (main.py always wires
        # the SAME provider_resolver singleton as this lookup — zero new per-request I/O), a
        # resolved model_id whose CACHED modality is known and != "chat" is rejected via
        # _check_chat_modality() — see complete()/stream() for the single insertion point each.
        self._chat_modality_lookup: ChatModalityLookup | None = chat_modality_lookup
        # batch-auto-grouping (v57 §3): None (default) ⇒ feature off ⇒ complete() is
        # byte-identical (no try_divert call). When wired, an opted-in tenant's eligible
        # (non-streaming, past-validation, cache-miss/bypass) request may be diverted
        # into the batch-job-store pipeline — see complete() for the single insertion
        # point, immediately before the upstream call, after every cache tier resolves.
        # stream() is NEVER touched (M2 — streaming always synchronous).
        self._batch_diversion: BatchDiversionPort | None = batch_diversion
        # output-schema-validation (§3): False (default) ⇒ CENTRAL KNOB-KILL —
        # _check_output_validation() pops validate_output from the body before
        # dispatch unconditionally (M1/M2) so complete()/stream() are byte-identical
        # to the v11 translate-don't-enforce path. True ⇒ a request that ALSO opts
        # in (validate_output:true) engages the pre-flight checks + bounded retry —
        # see complete() for the single insertion point.
        self._output_validation_enabled: bool = output_validation_enabled
        # payload-capture-store (§3): None (default) => feature off => complete()/
        # stream() are byte-identical (no capture dispatch at any hook site). When
        # wired, an opted-in, non-ZDR tenant's proxied call fires a scrubbed
        # request_logs row via _dispatch_capture at each hook site — see complete()/
        # stream()/_fire_record_cached call sites for the insertion points.
        self._payload_capture: PayloadCapturePort | None = payload_capture

    async def _authenticate(self, raw_key: str | None) -> AuthzResult:
        """Extract bearer key and return AuthzResult with governance fields.

        Raises ProblemError 401 on any authentication failure.
        Returns the full AuthzResult so callers can enforce governance fields.
        """
        if not raw_key:
            raise AUTH_KEY_INVALID.exc()
        try:
            result = await self._authenticator.authenticate(raw_key)
        except InvalidApiKeyError:
            raise AUTH_KEY_INVALID.exc() from None
        # Bind tenant_id to the structlog context so the access log line (emitted
        # by RequestIdMiddleware after the response) carries it for authenticated paths.
        # On pre-auth 401 exits above, this line is never reached — field stays absent.
        structlog.contextvars.bind_contextvars(tenant_id=str(result.tenant_id))
        return result

    async def _resolve_preset(self, body: dict[str, Any], tenant_id: uuid.UUID) -> None:
        """Resolve a `<preset>:<alias>` selector in body["model"], in place.

        preset-resolution-ingress (v56 §3 CONTRACT). Called between _authenticate and
        _validate_payload in both complete() and stream() — BEFORE any per-model
        authorization/catalog/budget/billing logic or upstream call.

        No-ops (byte-identical) when: the store is unwired (None), the model field is
        not a colon selector (bare id), or the model field is present but not a string
        (defers to _validate_payload's existing type/emptiness check, never crashes here).
        Raises PRESET_NOT_FOUND (400) when the selector's colon is present but resolve()
        finds no matching row for the CALLING tenant only (never cross-tenant).
        """
        if self._tenant_model_preset_store is None:
            return
        raw_model = body.get("model", "")
        if not isinstance(raw_model, str):
            return
        selector = parse_preset_selector(raw_model)
        if selector is None:
            return
        preset_name, alias_key = selector
        target = await self._tenant_model_preset_store.resolve(tenant_id, preset_name, alias_key)
        if target is None:
            raise PRESET_NOT_FOUND.exc() from None
        body["model"] = target

    def _strip_web_search_flag(self, body: dict[str, Any]) -> None:
        """Central knob-kill for the web_search flag (web-search-grounding task).

        When web_search_enabled=False (default), removes "web_search" from the
        request body BEFORE dispatch so the outgoing upstream payload is byte-identical
        to today — no adapter, retry leg, or downstream code ever sees the flag.

        When web_search_enabled=True, leaves the flag in place so adapters can
        translate it into provider-native grounding tools.

        Mutates body in-place (same pattern as any pre-dispatch normalization).
        """
        if not self._web_search_enabled:
            body.pop("web_search", None)

    async def _check_input_modalities(
        self,
        body: dict[str, Any],
        model_id: str,
        model_groups: dict[str, list[str]] | None,
    ) -> None:
        """Enforce the capability-aware input-modality guard (unsupported-input-guard §3).

        Runs AFTER _enforce_governance and BEFORE bandwidth acquire / upstream / usage.
        A refused request is never billed (raises ProblemError 400 before any side-effect).

        Guard is a no-op when:
          - _input_modality_guard_enabled is False (default; byte-identical)
          - _input_modality_lookup is None (not wired — legacy / test compat)
          - lookup returns None (unknown model / no active row) → fail-open by design

        Does NOT touch video_url parts (video modality deferred from v55).
        """
        if not self._input_modality_guard_enabled or self._input_modality_lookup is None:
            return

        from gateway.proxy.application.modality_guard import (
            enforce,
            required_input_modalities_for_chat,
            resolve_allowed,
        )

        messages_raw = body.get("messages", [])
        if not isinstance(messages_raw, list):
            return  # malformed messages — adapter / payload validation will handle it
        messages: list[dict[str, Any]] = messages_raw

        required = required_input_modalities_for_chat(messages)
        if not required:
            # Nothing to enforce (e.g. only video_url parts, deferred)
            return

        allowed = await resolve_allowed(model_id, self._input_modality_lookup, model_groups)
        enforce(required, allowed, model_id=model_id)

    async def _check_chat_modality(self, model_id: str) -> None:
        """Enforce the coarse operation-type guard (chat-modality-guard TASK.md §3).

        Runs AFTER _check_input_modalities and BEFORE credential resolution / upstream /
        usage. A refused request is never billed (raises ProblemError 400 before any
        side-effect) — mirrors the images/embeddings/TTS coarse guard precedent.

        Reads from the SAME cached provider-resolver map provider_for() already reads —
        zero new per-request I/O. Guard is a no-op (fail-open) when:
          - _chat_modality_lookup is None (not wired — legacy / test compat)
          - lookup returns None (unknown/uncached model_id) → fail-open by design; the
            model's existence/active-state was already checked earlier by ModelChecker
        """
        if self._chat_modality_lookup is None:
            return
        modality = await self._chat_modality_lookup.modality_for(model_id)
        if modality is not None and modality != "chat":
            raise MODEL_MODALITY_MISMATCH.exc(
                model_id=model_id,
                detail=f"model '{model_id}' has modality '{modality}', endpoint requires 'chat'",
            )

    def _check_output_validation(self, body: dict[str, Any]) -> bool:
        """Central knob-kill + pre-flight for output-schema validation (§3 M1-M4, M11).

        Pops ``validate_output`` from body UNCONDITIONALLY (M2 — never forwarded
        upstream, regardless of engagement). Returns True when the feature engages
        for THIS request (operator kill-switch AND the request opted in); False
        means the byte-identical v11 path (M1) — no schema parse, no rejection.

        When engaged, runs the pre-flight checks BEFORE any upstream call (zero
        calls billed on a reject):
          - M11: stream:true + engaged ⇒ 400 ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM
          - Reject: response_format is not json_schema ⇒ 400
            ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA
          - M3: the schema itself fails JSON-Schema meta-validation ⇒ 400
            ERR_INVALID_JSON_SCHEMA (REUSES the v11 response_format_translation.py
            error CODE STRING; this task does not edit that frozen module)

        Mutates body in-place (same pattern as _strip_web_search_flag).
        """
        requested = bool(body.pop("validate_output", False))
        engaged = self._output_validation_enabled and requested
        if not engaged:
            return False
        if body.get("stream"):
            raise OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM.exc()
        try:
            rf = extract_response_format(body)
        except ValueError as exc:
            if str(exc) == "ERR_INVALID_JSON_SCHEMA":
                raise INVALID_JSON_SCHEMA.exc() from None
            raise OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA.exc() from None
        if rf is None or rf["type"] != "json_schema":
            raise OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA.exc()
        json_schema_spec = rf.get("json_schema")
        if json_schema_spec is None:
            raise OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA.exc()
        schema = json_schema_spec["schema"]
        reason = check_schema_well_formed(schema)
        if reason is not None:
            raise INVALID_JSON_SCHEMA.exc(detail=reason)
        return True

    async def _validate_payload(
        self,
        body: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]], bool]:
        """Validate model and messages fields (format only — no catalog check here).

        Returns (model_id, messages, output_validation_engaged).
        Raises ProblemError 422 on validation failure (400 for the output-validation
        pre-flight rejects — see _check_output_validation).

        NOTE: The catalog active check (ERR_MODEL_UNKNOWN) and tenant-disabled check
        (ERR_MODEL_DISABLED) are performed in _enforce_governance, AFTER the key-level
        allowlist check — enforcing §3 M7 order:
          1. model_id format validation  [here]
          2. key-level allowlist check   [_enforce_governance step 1]
          3. catalog+tenant check        [_enforce_governance step 2]
        """
        # web-search-grounding: strip (or keep) the raw web_search flag centrally
        # BEFORE any field validation — the flag is not a gateway-owned field and must
        # never reach upstream verbatim. Knob-off ⇒ pop; knob-on ⇒ adapters handle it.
        self._strip_web_search_flag(body)

        # output-schema-validation: pop validate_output centrally (M1/M2) + run the
        # M3/M11/Reject pre-flight checks BEFORE any field/governance/upstream work —
        # shared by complete() AND stream() so a stream:true + engaged request is
        # rejected regardless of which entry point routed it here.
        output_validation_engaged = self._check_output_validation(body)

        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        messages = body.get("messages")
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            raise PAYLOAD_MESSAGES_REQUIRED.exc()

        return model_id, messages, output_validation_engaged

    async def _check_model_catalog(
        self,
        model_id: str,
        tenant_id: uuid.UUID | None = None,
        model_groups: dict[str, list[str]] | None = None,
    ) -> None:
        """Check catalog active state and per-tenant override.

        Enforcement order (§3 M7): called from _enforce_governance AFTER the
        key-level allowlist check so allowlist always fires first.

        Alias-aware (model-fallbacks §3 CATALOG INTERACTION A4):
          When model_id is an alias key in model_groups, ALL candidates in the
          group are validated via check_for_tenant + is_active. Any candidate
          failing => MODEL_UNKNOWN for the whole request (conservative: never
          route to or bill a model the tenant cannot access; fallback may serve
          ANY candidate, so all must be authorized up front).
          Cost: <=5 catalog lookups per alias request (bounded by Settings validator #3).
          Plain model ids keep the existing single check, byte-identical.

        Frozen-fake compatibility seam (model-mgmt TASK.md §3):
          Frozen proxy-completions test fakes only implement is_active — they do not
          have check_for_tenant. We use hasattr to detect capability:
          - If the injected checker has check_for_tenant AND tenant_id is provided,
            call check_for_tenant and interpret the tri-state ModelAccess enum.
          - Otherwise fall back to is_active (frozen fakes satisfy this — no edits needed).
          This is the standard soft-budget seam precedent used in this repo: additive
          capability detection via hasattr, never forcing frozen fakes to implement new methods.
        """
        checker = self._model_checker

        # Alias-aware: when model_id is an alias, validate every candidate.
        if model_groups and model_id in model_groups:
            candidates = model_groups[model_id]
            for candidate_id in candidates:
                await self._check_single_model(checker, candidate_id, tenant_id)
            return

        # Plain model id — existing single check (byte-identical for non-alias requests).
        await self._check_single_model(checker, model_id, tenant_id)

    async def _check_single_model(
        self,
        checker: ModelChecker,
        model_id: str,
        tenant_id: uuid.UUID | None,
    ) -> None:
        """Check a single model id against the catalog (extracted helper).

        Raises MODEL_UNKNOWN or MODEL_DISABLED as appropriate.
        """
        if tenant_id is not None and hasattr(checker, "check_for_tenant"):
            access = await checker.check_for_tenant(model_id, tenant_id)
            if access is ModelAccess.UNKNOWN:
                raise MODEL_UNKNOWN.exc(model_id=model_id)
            if access is ModelAccess.TENANT_DISABLED:
                raise MODEL_DISABLED.exc()
            # ModelAccess.ACTIVE — proceed
        else:
            # Fallback path: frozen fakes / no tenant_id (preserves existing behavior)
            is_active = await checker.is_active(model_id)
            if not is_active:
                raise MODEL_UNKNOWN.exc(model_id=model_id)

    async def _enforce_rate_limits(self, authz: AuthzResult) -> None:
        """Enforce RPM and TPM rate limits (M5-M7, M10).

        Order: RPM first, then TPM. If either fires, 429 ERR_RATE_LIMITED.
        Fail-open on Redis error (RateLimitExceededError is not swallowed —
        it converts to a 429 ProblemError; Redis errors are swallowed in the limiter).

        Called AFTER governance checks (expiry/allowlist/budget) per §3 M10.
        """
        limiter = self._rate_limiter

        # M5: RPM check (atomic ZSET sliding window)
        if authz.rpm_limit is not None:
            try:
                await limiter.check_rpm(authz.key_id, authz.rpm_limit)
            except RateLimitExceededError as exc:
                raise RATE_LIMITED.exc(
                    detail=f"RPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

        # M6: TPM pre-flight check
        if authz.tpm_limit is not None:
            try:
                await limiter.check_tpm(authz.key_id, authz.tpm_limit)
            except RateLimitExceededError as exc:
                raise RATE_LIMITED.exc(
                    detail=f"TPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

    async def _check_team_budget(self, authz: AuthzResult) -> None:
        """Check per-team Redis spend counter against team's team_budget_usd.

        Only called when authz.team_id and authz.team_budget_usd are both set.
        Fail-open: Redis unavailable → allow (advisory counter pattern, same as per-key).
        Counter key: usage:spend:team:{team_id}:{YYYYMM}
        """
        budget = authz.team_budget_usd
        team_id = authz.team_id
        if budget is None or team_id is None:
            return

        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:team:{team_id}:{yyyymm}"

        try:
            redis = self._get_redis()
            if redis is None:
                return  # No Redis wired — fail open
            raw = await redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "team_budget_check: Redis GET failed (fail open)",
                exc_info=exc,
                extra={"team_id": str(team_id), "spend_key": spend_key},
            )
            return

        spent = _parse_spend(raw)

        if spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=f"Team spend {spent} >= budget {budget} for team {team_id}"
            )

    async def _enforce_governance(
        self,
        authz: AuthzResult,
        model_id: str,
        budget_guard: BudgetGuard,
        model_groups: dict[str, list[str]] | None = None,
    ) -> None:
        """Enforce all governance rules in priority order (M8-M10, M12).

        Order: expiry → model allowlist → catalog+tenant check → per-key budget
               → team budget → tenant budget (fallback) → RPM check → TPM check.
        All governance data comes from AuthzResult — zero extra DB queries.

        Per-key budget: fail-open on Redis failure (advisory counter, A2/M13).
        Team budget: fail-open on Redis failure; runs regardless of key budget presence.
        Soft budget: no blocking — the seam is exposed via _compute_soft_exceeded().
        Rate limits: fail-open on Redis failure (M14); RPM before TPM (M7).

        Enforcement order per §3 M7:
          key-level allowlist (step 2) BEFORE tenant-disabled check (step 3).
          Both use ERR_MODEL_NOT_ALLOWED and ERR_MODEL_DISABLED respectively.

        model_groups: when provided, the catalog check is alias-aware (§3 A4):
          alias keys validate all candidates; plain ids use the existing single check.
        """
        # M8: Expiry check (fail-closed, DB-sourced — no infra failure risk)
        _check_expiry(authz)

        # M9: Model allowlist check (fail-closed, DB-sourced — no infra failure risk)
        # MUST run BEFORE the tenant-disabled check (§3 M7 enforcement order).
        _check_model_allowlist(authz, model_id)

        # Catalog active + per-tenant override check (§3 step 3 — after allowlist).
        # Alias-aware when model_groups provided (§3 A4): validates all candidates.
        await self._check_model_catalog(model_id, authz.tenant_id, model_groups)

        # M10: Most-specific-wins budget enforcement.
        # A soft budget alone is a SIGNAL, not a limit — it must never exempt
        # the key from tenant-budget enforcement. So: hard per-key budget wins
        # outright; otherwise the soft seam runs (alert only, cannot 402) AND
        # the tenant budget still enforces.
        if authz.monthly_budget_usd is not None:
            await self._check_per_key_budget(authz)
            # Team budget check (§3 step 5) — most-specific-wins continues upward:
            # a key under its own cap can still be stopped by its team's cap.
            await self._check_team_budget(authz)
        else:
            if authz.soft_budget_usd is not None:
                # Soft-alert seam only (budget None → no per-key 402 possible)
                await self._check_per_key_budget(authz)
            # Team budget check (§3 step 5) — before the tenant guard (step 6);
            # fail-open on Redis errors (same guarantee as per-key budget check).
            await self._check_team_budget(authz)
            # No hard per-key budget — tenant budget enforces (RedisBudgetGuard)
            await budget_guard.check(authz.tenant_id)

        # M10 (rate limits): RPM check → TPM check — after governance, before upstream
        await self._enforce_rate_limits(authz)

    async def _check_per_key_budget(self, authz: AuthzResult) -> None:
        """Check per-key Redis spend counter against key's monthly_budget_usd.

        Also fires the soft-budget alert event if soft_budget_usd is set (M11).
        Fail-open: Redis unavailable → allow (advisory counter pattern, M13).
        Counter key: usage:spend:key:{key_id}:{YYYYMM}
        """
        budget = authz.monthly_budget_usd
        # Return early only if neither hard nor soft budget needs a Redis read.
        if budget is None and authz.soft_budget_usd is None:
            return

        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:key:{authz.key_id}:{yyyymm}"

        try:
            redis = self._get_redis()
            if redis is None:
                return  # No Redis wired — fail open
            raw = await redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "per_key_budget_check: Redis GET failed (fail open)",
                exc_info=exc,
                extra={"key_id": str(authz.key_id), "spend_key": spend_key},
            )
            return

        spent = _parse_spend(raw)

        # Soft budget seam (M11): fire-and-forget alert event when crossing detected.
        # Scheduled here (pre-flight), never awaited — hot-path latency unaffected.
        # ON CONFLICT (dedupe_key) DO NOTHING → idempotent across repeated crossings.
        if authz.soft_budget_usd is not None and spent >= authz.soft_budget_usd:
            session_factory = self._get_session_factory()
            if session_factory is not None:
                from gateway.usage.application.alert_writer import (
                    persist_soft_budget_alert,
                )

                _task = asyncio.ensure_future(
                    persist_soft_budget_alert(
                        session_factory,
                        authz.tenant_id,
                        authz.key_id,
                        authz.soft_budget_usd,
                        spent,
                    )
                )
                # Keep reference to prevent GC; swallow exceptions in callback.
                _task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

        # Hard budget enforcement — only when monthly_budget_usd is set (M10).
        if budget is not None and spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=f"Per-key spend {spent} >= budget {budget} for key {authz.key_id}"
            )

    def _get_redis(self) -> Any:
        """Return the Redis client if available via the budget guard.

        Attempts to extract redis from RedisBudgetGuard; returns None if unavailable.
        This avoids adding a direct Redis dependency to CompletionUseCase.
        """
        guard = self._budget_guard
        return getattr(guard, "_redis", None)

    def _get_session_factory(self) -> Any:
        """Return the session_factory if available via the budget guard.

        Attempts to extract _session_factory from RedisBudgetGuard; returns None if unavailable.
        This avoids adding a direct DB dependency to CompletionUseCase.
        """
        guard = self._budget_guard
        return getattr(guard, "_session_factory", None)

    async def _resolve_credential(self, tenant_id: Any, model_id: str) -> object | None:
        """Resolve the per-tenant provider credential and set it in the request contextvar.

        Returns the contextvar reset Token when resolution succeeds, or ``None`` when it is
        SKIPPED (resolvers not wired / a still-env-bound provider). Caller MUST call
        ``reset_provider_credential(token)`` in a ``finally`` block when non-None.

        The chat entry point: it resolves the provider from ``model_id`` via the provider
        resolver, then delegates the STAGED gate + resolve + set + ProviderKeyMissing→402
        mapping to the module-level ``resolve_provider_credential`` (shared with the
        non-chat use-cases — the single source of truth for the §3 seam).

        Raises:
            ProblemError(402): when the tenant has no configured key for a CONVERTED
                provider. Mapped inside ``resolve_provider_credential`` so
                ``complete()``/``stream()`` need no extra except clause (complexity).
        """
        if self._tenant_credential_resolver is None or self._provider_resolver is None:
            return None
        _provider = await self._provider_resolver.provider_for(model_id)
        return await resolve_provider_credential(
            self._tenant_credential_resolver, tenant_id, _provider
        )

    async def _run_diverted_fallback(
        self,
        *,
        authz: AuthzResult,
        body: dict[str, Any],
        model_id: str,
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
        model_router: FallbackModelRouter | None,
    ) -> dict[str, Any]:
        """G10 fallback: run the billing-correct equivalent of the existing
        synchronous upstream call for ONE accumulated item, entirely independent of
        the original complete() call's lifetime (batch-window-grouping TASK.md §3).

        Invoked by the SSE generator (BatchDiversionAdapter._lifecycle), potentially
        seconds after the original complete() call already returned — that call's own
        credential-resolution contextvar token was already reset by its own `finally`
        clause. Credential resolution is therefore redone FRESH here, exactly as a
        brand-new request would, never assumed still in effect.

        Never raises: this is a deferred, best-effort completion for an item whose
        HTTP response (200 text/event-stream, already committed) cannot change
        status. Any upstream failure degrades to an error-shaped body rather than an
        unhandled exception reaching the SSE generator.

        Deliberately does NOT re-apply post-call guardrail masking or cache-store — a
        conscious, bounded residue for this rare fallback path (this task's own test
        plan only requires "a real body, never a 5xx" here, not guardrail/cache
        parity; documented per batch-auto-grouping's own non-blocking-residue
        convention).
        """
        _cred_token: object | None = None
        try:
            _cred_token = await self._resolve_credential(authz.tenant_id, model_id)
            try:
                if model_router is not None:
                    status, response_body, served_model_id = await model_router.complete(
                        body, upstream=upstream
                    )
                else:
                    status, response_body = await upstream.complete(body)
                    served_model_id = model_id
            except Exception as exc:
                _log.warning(
                    "batch window fallback upstream call failed for tenant %s",
                    authz.tenant_id,
                    exc_info=exc,
                )
                _fire_record(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=model_id,
                    usage=None,
                    status=502,
                    team_id=authz.team_id,
                )
                return {
                    "error": {
                        "message": "batch window fallback failed",
                        "type": "internal_error",
                        "code": "ERR_UPSTREAM_UNAVAILABLE",
                    }
                }

            # Mirrors the established idiom at complete()'s own success path above:
            # response_body's static type is dict[str, Any] | dict[str, object]
            # (model_router.complete() vs upstream.complete() declare different value
            # types), so isinstance(response_body, dict) is always true (both arms are
            # already dicts) — narrow the EXTRACTED usage value instead.
            usage_raw = response_body.get("usage")
            usage: dict[str, Any] | None = usage_raw if isinstance(usage_raw, dict) else None
            _fire_record(
                usage_recorder,
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=served_model_id,
                usage=usage,
                status=status,
                team_id=authz.team_id,
            )
            return response_body
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

    async def _try_cache_lookup(
        self,
        *,
        cache: ResponseCache,
        authz: AuthzResult,
        body: dict[str, Any],
        model_id: str,
        guardrail_evaluator: Any,
        guardrail_configs: dict[str, Any],
        metrics_registry: Any,
        usage_recorder: UsageRecorder,
        request_headers: dict[str, str] | None,
        start_ns: int,
        request_id: uuid.UUID,
    ) -> tuple[tuple[int, dict[str, Any], str | None] | None, str | None]:
        """Step 4.5a/4.5b/4.5c cache-lookup region (exact → semantic → vector),
        extracted out of complete() (which already carries ~15 branches of its own,
        same reason _run_output_validation_retry above was extracted) so
        CompletionUseCase.complete() stays within pyright's control-flow
        complexity limit.

        Called only once the caller has already confirmed cache is not None,
        authz.cache_enabled, and not _output_validation_engaged (M9) — those three
        gate conditions stay in complete() itself, unchanged.

        Returns (hit, x_cache):
          - hit is (200, response_body, x_cache) on an exact/semantic/vector HIT —
            the caller returns this triple immediately, exactly as complete() did
            inline before this extraction (same short-circuit, before batch
            diversion / upstream call / anything else runs).
          - hit is None on MISS or BYPASS — x_cache is "miss" or "bypass" for the
            caller to thread into its own x_cache local and continue.
        """
        from gateway.proxy.infrastructure.response_cache import (
            build_cache_key,
            build_semantic_cache_key,
        )

        no_cache = (request_headers or {}).get("cache-control", "").lower() == "no-cache"
        cache_key = build_cache_key(str(authz.tenant_id), body)

        if not no_cache:
            # Step 4.5a: Try exact-match cache lookup
            cached_body = await cache.get(cache_key)
            if cached_body is not None:
                # EXACT HIT: apply post-call guardrails, then return cached body
                x_cache = "hit"
                # cache-alias-billing (B6): read+pop the served candidate BEFORE
                # evaluate_post masking (which may return a fresh dict), so billing keys
                # on the served catalog id, not the alias, and the stamp never reaches
                # the client.
                _served_cached = _read_served_from_cache(cached_body, model_id)
                if metrics_registry is not None:
                    try:
                        metrics_registry.cache_events_total.labels(result="hit").inc()
                    except Exception:  # noqa: S110
                        pass
                # Step 5.5 on cache HIT: apply post-call PII mask if configured
                # evaluate_post is always fail-OPEN (post-call is MASK/AUDIT only).
                if guardrail_evaluator is not None and guardrail_configs:
                    if hasattr(guardrail_evaluator, "evaluate_post"):
                        try:
                            cached_body = await guardrail_evaluator.evaluate_post(
                                cached_body, guardrail_configs
                            )
                        except Exception as _exc:
                            _log.warning(
                                "guardrail evaluate_post raised on cache HIT (fail-OPEN)",
                                exc_info=_exc,
                            )
                cached_usage_raw = cached_body.get("usage")
                cached_usage: dict[str, Any] | None = (
                    cached_usage_raw if isinstance(cached_usage_raw, dict) else None
                )
                _fire_record_cached(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=_served_cached,
                    usage=cached_usage,
                    team_id=authz.team_id,
                    request_id=request_id,
                )
                # payload-capture-store §3: exact-cache-HIT capture hook —
                # cached_body is the SAME post-mask body actually served to the
                # client (never the raw unmasked Redis-internal representation).
                # request-log-metering-fields §3: usage=cached_usage (the SAME cached
                # dict, never re-derived); latency_ms from the SAME start_ns the caller
                # threaded in — never a second clock.
                _dispatch_capture(
                    self._payload_capture,
                    enabled=authz.payload_capture_enabled,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=_served_cached,
                    request_body=body,
                    response_body=cached_body,
                    status=200,
                    stream=False,
                    cached=True,
                    guardrail_configs=guardrail_configs,
                    usage=cached_usage,
                    latency_ms=round((time.time_ns() - start_ns) / 1_000_000),
                    request_id=request_id,
                )
                # TPM post-accounting uses cached token counts
                if authz.tpm_limit is not None and cached_usage is not None:
                    total_tokens = cached_usage.get("total_tokens")
                    if isinstance(total_tokens, int) and total_tokens > 0:
                        _fire_record_tpm(
                            self._rate_limiter, key_id=authz.key_id, tokens=total_tokens
                        )
                return (200, cached_body, x_cache), x_cache
            else:
                # Step 4.5b: Exact MISS — try semantic lookup if enabled
                semantic_cache_enabled = getattr(authz, "semantic_cache_enabled", False)
                if semantic_cache_enabled and hasattr(cache, "get_pointer"):
                    sem_key = build_semantic_cache_key(str(authz.tenant_id), body)
                    exact_key_str = await cache.get_pointer(sem_key)  # pyright: ignore[reportAttributeAccessIssue]  — guarded by hasattr; concrete ResponseCache has get_pointer, Protocol doesn't
                    if exact_key_str is not None:
                        # Dereference pointer: GET the exact-cache key body
                        sem_cached_body = await cache.get(exact_key_str)
                        if sem_cached_body is not None:
                            # SEMANTIC HIT
                            x_cache = "semantic_hit"
                            # cache-alias-billing (B6): read+pop served BEFORE masking.
                            _served_cached_sem = _read_served_from_cache(
                                sem_cached_body, model_id
                            )
                            if metrics_registry is not None:
                                try:
                                    metrics_registry.cache_events_total.labels(
                                        result="semantic_hit"
                                    ).inc()
                                except Exception:  # noqa: S110
                                    pass
                            # Apply post-call PII mask on semantic hit (same as exact hit)
                            if guardrail_evaluator is not None and guardrail_configs:
                                if hasattr(guardrail_evaluator, "evaluate_post"):
                                    try:
                                        sem_cached_body = (
                                            await guardrail_evaluator.evaluate_post(
                                                sem_cached_body, guardrail_configs
                                            )
                                        )
                                    except Exception as _exc:
                                        _log.warning(
                                            "guardrail evaluate_post raised on"
                                            " semantic HIT (fail-OPEN)",
                                            exc_info=_exc,
                                        )
                            sem_usage_raw = sem_cached_body.get("usage")
                            sem_usage: dict[str, Any] | None = (
                                sem_usage_raw if isinstance(sem_usage_raw, dict) else None
                            )
                            _fire_record_cached(
                                usage_recorder,
                                tenant_id=authz.tenant_id,
                                key_id=authz.key_id,
                                model=_served_cached_sem,
                                usage=sem_usage,
                                team_id=authz.team_id,
                                request_id=request_id,
                            )
                            # payload-capture-store §3: semantic-cache-HIT hook.
                            # request-log-metering-fields §3: usage=sem_usage verbatim;
                            # latency_ms from the SAME threaded-in start_ns.
                            _dispatch_capture(
                                self._payload_capture,
                                enabled=authz.payload_capture_enabled,
                                tenant_id=authz.tenant_id,
                                key_id=authz.key_id,
                                model=_served_cached_sem,
                                request_body=body,
                                response_body=sem_cached_body,
                                status=200,
                                stream=False,
                                cached=True,
                                guardrail_configs=guardrail_configs,
                                usage=sem_usage,
                                latency_ms=round((time.time_ns() - start_ns) / 1_000_000),
                                request_id=request_id,
                            )
                            if authz.tpm_limit is not None and sem_usage is not None:
                                total_tokens = sem_usage.get("total_tokens")
                                if isinstance(total_tokens, int) and total_tokens > 0:
                                    _fire_record_tpm(
                                        self._rate_limiter,
                                        key_id=authz.key_id,
                                        tokens=total_tokens,
                                    )
                            return (200, sem_cached_body, x_cache), x_cache
                        else:
                            # Dangling pointer: exact key expired — treat as MISS
                            _log.debug(
                                "semantic_cache: dangling pointer (exact key expired), "
                                "treating as MISS",
                                extra={"sem_key": sem_key, "exact_key": exact_key_str},
                            )
                # Step 4.5c: exact + normalization miss — try the embedding-similarity
                # (vector) layer when wired. A hit takes the SAME billing/metric/PII/TPM
                # path as the exact/semantic hit. The internal embed call is NEVER billed.
                if self._vector_cache is not None:
                    vec_body = await self._vector_cache.lookup(
                        tenant_id=str(authz.tenant_id), model=model_id, body=body
                    )
                    if vec_body is not None:
                        x_cache = "vector_hit"
                        # cache-alias-billing (B6): read+pop served BEFORE masking.
                        _served_cached_vec = _read_served_from_cache(vec_body, model_id)
                        if metrics_registry is not None:
                            try:
                                metrics_registry.cache_events_total.labels(
                                    result="vector_hit"
                                ).inc()
                            except Exception:  # noqa: S110
                                pass
                        if guardrail_evaluator is not None and guardrail_configs:
                            if hasattr(guardrail_evaluator, "evaluate_post"):
                                try:
                                    vec_body = await guardrail_evaluator.evaluate_post(
                                        vec_body, guardrail_configs
                                    )
                                except Exception as _exc:
                                    _log.warning(
                                        "guardrail evaluate_post raised on"
                                        " vector HIT (fail-OPEN)",
                                        exc_info=_exc,
                                    )
                        vec_usage_raw = vec_body.get("usage")
                        vec_usage: dict[str, Any] | None = (
                            vec_usage_raw if isinstance(vec_usage_raw, dict) else None
                        )
                        _fire_record_cached(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=_served_cached_vec,
                            usage=vec_usage,
                            team_id=authz.team_id,
                            request_id=request_id,
                        )
                        # payload-capture-store §3: vector (embedding-similarity)
                        # cache-HIT hook.
                        # request-log-metering-fields §3: usage=vec_usage verbatim;
                        # latency_ms from the SAME threaded-in start_ns.
                        _dispatch_capture(
                            self._payload_capture,
                            enabled=authz.payload_capture_enabled,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=_served_cached_vec,
                            request_body=body,
                            response_body=vec_body,
                            status=200,
                            stream=False,
                            cached=True,
                            guardrail_configs=guardrail_configs,
                            usage=vec_usage,
                            latency_ms=round((time.time_ns() - start_ns) / 1_000_000),
                            request_id=request_id,
                        )
                        if authz.tpm_limit is not None and vec_usage is not None:
                            total_tokens = vec_usage.get("total_tokens")
                            if isinstance(total_tokens, int) and total_tokens > 0:
                                _fire_record_tpm(
                                    self._rate_limiter,
                                    key_id=authz.key_id,
                                    tokens=total_tokens,
                                )
                        return (200, vec_body, x_cache), x_cache
                # MISS (exact miss + semantic miss/disabled + vector miss/off)
                x_cache = "miss"
                if metrics_registry is not None:
                    try:
                        metrics_registry.cache_events_total.labels(result="miss").inc()
                    except Exception:  # noqa: S110
                        pass
                return None, x_cache
        else:
            # BYPASS: Cache-Control: no-cache
            x_cache = "bypass"
            if metrics_registry is not None:
                try:
                    metrics_registry.cache_events_total.labels(result="bypass").inc()
                except Exception:  # noqa: S110
                    pass
            return None, x_cache

    async def complete(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
        cache_ttl_seconds: int = 300,
        metrics_registry: Any = None,
        request_headers: dict[str, str] | None = None,
        model_router: FallbackModelRouter | None = None,
        batch_processor: object | None = None,
    ) -> tuple[int, dict[str, Any] | BatchDivertedStream, str | None]:
        """Handle a non-streaming completion.

        Returns (status_code, json_body, x_cache_value). json_body is a
        BatchDivertedStream (batch-window-grouping §3) instead of a dict exactly when
        this request was genuinely accepted into the batch accumulation buffer — the
        caller (proxy/api/router.py) MUST check isinstance() and wrap body_stream in a
        StreamingResponse rather than a JSONResponse in that case.
        x_cache_value is "hit", "miss", "bypass", or None (cache disabled).
        On upstream 4xx: pass-through verbatim.
        On upstream 5xx / circuit open: raise ProblemError 502.
        """
        _start_ns = time.time_ns()
        # request-log-metering-fields TASK.md §3: one correlation UUID minted once per
        # call, mirroring _emit_span_fire_forget's own local-generation idiom (trace_id).
        _request_id = uuid.uuid4()
        _authz: AuthzResult | None = None
        _status_code: int = 502
        _model_id: str = ""
        _fallback: bool = False
        _cached: bool = False
        _guardrail_blocked: bool = False
        _pii_masked: bool = False
        _error_code: str | None = None
        _cred_token: object = None  # set by _resolve_credential — reset in finally
        try:
            authz = await self._authenticate(raw_key)
            _authz = authz  # set ONLY after _authenticate succeeds — pre-authz 401 → no span
            # preset-resolution-ingress (v56 §3): resolve a <preset>:<alias> selector to the
            # tenant's target model BEFORE payload validation/governance/catalog/upstream.
            await self._resolve_preset(body, authz.tenant_id)
            # Validate payload fields (format only — catalog check is in _enforce_governance).
            model_id, _, _output_validation_engaged = await self._validate_payload(body)
            _model_id = model_id
            # Enforce governance: expiry → allowlist → catalog+tenant check → budget → rate limits
            # Governance ALWAYS runs before cache lookup (contract §3 enforcement order).
            # Pass model_groups from model_router for alias-aware catalog check (§3 A4).
            _model_groups = model_router.model_groups if model_router is not None else None
            await self._enforce_governance(authz, model_id, self._budget_guard, _model_groups)

            # unsupported-input-guard: check input modalities AFTER governance and BEFORE
            # bandwidth acquire / upstream / usage (contract §3 — refused = never billed).
            await self._check_input_modalities(body, model_id, _model_groups)

            # chat-modality-guard (v56 §3): coarse operation-type guard — a resolved model
            # whose CACHED modality is known and != "chat" is rejected before any I/O.
            await self._check_chat_modality(model_id)

            # bandwidth-pacing pre-flight (stream-bandwidth-pacing v36): charge the per-key
            # bucket BEFORE the upstream call so a non-stream request is SHED (never paid) when
            # the bounded wait is exhausted. estimate = prompt chars/4 + max_tokens (a coarse
            # upper bound; task 3 reconciles to the real total at close). Default-OFF ⇒ Passthrough
            # ⇒ immediate grant ⇒ byte-identical. fail-open is guaranteed by the bucket (admits).
            _bw_prompt_chars = sum(
                len(str(_m.get("content", "")))
                for _m in body.get("messages", [])
                if isinstance(_m, dict)
            )
            # max_tokens is untrusted: coerce defensively (a non-numeric value must never
            # 500 the request here — payload validation owns rejection, not this estimator).
            _bw_max_tokens = body.get("max_tokens")
            _bw_extra = int(_bw_max_tokens) if isinstance(_bw_max_tokens, (int, float)) else 0
            _bw_estimate = max(1, _bw_prompt_chars // 4 + max(0, _bw_extra))
            try:
                await self._bandwidth_bucket.acquire(
                    authz.key_id, _bw_estimate, self._bandwidth_max_wait_s
                )
            except BandwidthExhaustedError as _bw_exc:
                raise BANDWIDTH_EXHAUSTED.exc(
                    detail=f"Bandwidth limit exceeded for key {_bw_exc.key_id}",
                    headers={"Retry-After": str(_bw_exc.retry_after_s)},
                ) from None

            # Credential resolution (credential-resolution-seam §3): resolve the per-tenant
            # provider credential and place it in the request-scoped contextvar so Bearer
            # adapters can read it in _auth_headers() without a signature change.
            _cred_token = await self._resolve_credential(authz.tenant_id, model_id)

            # --- Step 4: Pre-call guardrails (after governance, before cache lookup) ---
            guardrail_evaluator = self._guardrail_evaluator
            guardrail_configs = getattr(authz, "guardrail_configs", {}) or {}
            if guardrail_evaluator is not None and guardrail_configs:
                # ml-moderation-layer §3 CONTRACT (FROZEN @ v1, §0 R6): the frozen 2-arg
                # evaluate_pre(messages, guardrail_configs) call below is UNTOUCHED —
                # tenant identity for BYOK credential resolution flows via this sibling
                # ContextVar, set immediately before the call and reset in `finally` so
                # it never leaks across requests.
                _gtid_token = set_guardrail_tenant_id(authz.tenant_id)
                try:
                    result = await guardrail_evaluator.evaluate_pre(
                        body.get("messages", []), guardrail_configs
                    )
                except Exception as _exc:
                    # Evaluator itself raised unexpectedly (e.g. ErrorGuardrailEvaluator in tests).
                    # Apply fail-CLOSED/fail-OPEN at the use_case level.
                    _log.warning("guardrail evaluate_pre raised unexpectedly", exc_info=_exc)
                    _has_block = any(
                        isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("mode") == "block"
                        for cfg in guardrail_configs.values()
                    )
                    if _has_block:
                        # Fail-CLOSED: block the request
                        _fire_guardrail_metrics(
                            metrics_registry,
                            [_make_error_event(guardrail_configs)],
                            guardrail_configs,
                        )
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            guardrail_blocked=True,
                            blocked_by="error",
                            request_id=_request_id,
                        )
                        # payload-capture-store §3: pre-call guardrail-BLOCK hook (locked
                        # after the mandated BLOCK-path grounding pass — see TASK.md §5).
                        # response_body=None: the request never reached upstream.
                        # request-log-metering-fields §3: usage=None (no upstream call
                        # was made) -> prompt/completion/total_tokens all NULL, never 0.
                        _dispatch_capture(
                            self._payload_capture,
                            enabled=authz.payload_capture_enabled,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            request_body=body,
                            response_body=None,
                            status=400,
                            stream=False,
                            cached=False,
                            guardrail_configs=guardrail_configs,
                            usage=None,
                            latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                            request_id=_request_id,
                        )
                        _guardrail_blocked = True
                        _status_code = 400
                        raise GUARDRAIL_BLOCKED.exc() from None
                    else:
                        # Fail-OPEN: log error event, proceed
                        _fire_guardrail_metrics(
                            metrics_registry,
                            [_make_error_event(guardrail_configs)],
                            guardrail_configs,
                        )
                        # result is not set — fall through without masking/blocking
                        result = None
                finally:
                    reset_guardrail_tenant_id(_gtid_token)

                if result is not None:
                    _fire_guardrail_metrics(metrics_registry, result.events, guardrail_configs)
                    if result.blocked:
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            guardrail_blocked=True,
                            blocked_by=result.blocked_by,
                            request_id=_request_id,
                        )
                        # payload-capture-store §3: pre-call guardrail-BLOCK hook.
                        # request-log-metering-fields §3: usage=None -> tokens NULL.
                        _dispatch_capture(
                            self._payload_capture,
                            enabled=authz.payload_capture_enabled,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            request_body=body,
                            response_body=None,
                            status=400,
                            stream=False,
                            cached=False,
                            guardrail_configs=guardrail_configs,
                            usage=None,
                            latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                            request_id=_request_id,
                        )
                        _guardrail_blocked = True
                        _status_code = 400
                        raise GUARDRAIL_BLOCKED.exc()
                    if result.masked_messages is not None:
                        body = {**body, "messages": result.masked_messages}
                        # §3: usage raw must carry pii_masked=true when pre-call
                        # masking fired (defect found by live v4 verification).
                        _pii_masked = True
            else:
                guardrail_configs = {}

            # --- Step 4.5: Cache logic (non-streaming only, after guardrails) ---
            cache = self._response_cache
            cache_enabled = authz.cache_enabled
            x_cache: str | None = None

            # output-schema-validation (M9): an engaged request bypasses BOTH the
            # read (here) and write (post-upstream store, below) tiers entirely —
            # response_format is not in _CACHE_KEY_FIELDS (a pre-existing gap this
            # task does not fix), so bypassing is the only way to guarantee a
            # validating caller never silently receives an unvalidated cached body.
            # Step 4.5a/4.5b/4.5c (exact → semantic → vector) lives in
            # _try_cache_lookup, extracted out of this already-large method for the
            # same reason _run_output_validation_retry above was extracted.
            if cache is not None and cache_enabled and not _output_validation_engaged:
                _cache_hit, x_cache = await self._try_cache_lookup(
                    cache=cache,
                    authz=authz,
                    body=body,
                    model_id=model_id,
                    guardrail_evaluator=guardrail_evaluator,
                    guardrail_configs=guardrail_configs,
                    metrics_registry=metrics_registry,
                    usage_recorder=usage_recorder,
                    request_headers=request_headers,
                    start_ns=_start_ns,
                    request_id=_request_id,
                )
                if _cache_hit is not None:
                    _cached = True
                    _status_code = _cache_hit[0]
                    return _cache_hit

            # batch-auto-grouping (v57 §3) / batch-window-grouping (§3, supersedes the
            # size-1-job body): diversion check — sits OUTSIDE the cache block above
            # so it catches every path that would otherwise call upstream (miss,
            # bypass, cache disabled/unconfigured), and NEVER an exact/semantic/vector
            # HIT (those already returned above). authz.batch_grouping_enabled was
            # resolved at authentication time (zero extra DB reads, mirrors
            # semantic_cache_enabled) — try_divert() itself NEVER raises; a None
            # result means "proceed synchronously", identical to policy-disabled.
            if self._batch_diversion is not None and getattr(
                authz, "batch_grouping_enabled", False
            ):
                # G10 fallback closure: captures this call's own local scope (authz/
                # body/model_id/upstream/usage_recorder/model_router) so a deferred
                # SSE-generator invocation — potentially seconds later, well after
                # THIS complete() call has returned and its own credential-resolution
                # contextvar token has already been reset in the finally below — still
                # resolves credentials and records usage correctly, entirely on its
                # own, never assuming any of complete()'s own now-defunct state.
                async def _sync_fallback(
                    _authz: AuthzResult = authz,
                    _body: dict[str, Any] = body,
                    _model_id: str = model_id,
                    _upstream: CompletionUpstream = upstream,
                    _usage_recorder: UsageRecorder = usage_recorder,
                    _model_router: FallbackModelRouter | None = model_router,
                ) -> dict[str, Any]:
                    return await self._run_diverted_fallback(
                        authz=_authz,
                        body=_body,
                        model_id=_model_id,
                        upstream=_upstream,
                        usage_recorder=_usage_recorder,
                        model_router=_model_router,
                    )

                _diverted = await self._batch_diversion.try_divert(
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    body=body,
                    batch_processor=batch_processor,
                    sync_fallback=_sync_fallback,
                )
                if _diverted is not None:
                    _status_code = 200
                    return 200, _diverted, x_cache

            try:
                # Route through model_router when wired (model-fallbacks §3).
                # The router returns a 3-tuple (status, body, served_model_id).
                # served_model_id is the catalog candidate id we actually routed to —
                # used for billing/recording, NEVER the alias string and NEVER body["model"].
                # Plain model ids pass through transparently (served == model_id).
                # When model_router is None (frozen suites that do not wire the router):
                # fall back to direct upstream.complete() with served_model_id = model_id.
                if model_router is not None:
                    status, response_body, served_model_id = await model_router.complete(
                        body, upstream=upstream
                    )
                    # Span attribution (§3 OBSERVABILITY): fallback occurred when the
                    # served candidate differs from the alias group's first choice.
                    _candidates = model_router.candidates_for(model_id)
                    _fallback = _candidates is not None and served_model_id != _candidates[0]
                    _model_id = served_model_id
                else:
                    status, response_body = await upstream.complete(body)
                    served_model_id = model_id
            except AllDeploymentsSaturatedError as exc:
                # Deployment-limits: every candidate of the alias group exceeded its
                # per-deployment RPM/TPM limit. Map to 429 ERR_RATE_LIMITED with
                # Retry-After so the caller knows when to retry.
                raise RATE_LIMITED.exc(
                    detail=f"all deployments for '{exc.alias}' are rate-limited",
                    headers={"Retry-After": "60"},
                ) from exc
            except UpstreamRateLimitedError as exc:
                # upstream-ratelimit-passthrough: upstream returned 429 after exhausting
                # retries. Surface as client 429 ERR_UPSTREAM_RATE_LIMITED + Retry-After
                # (only when the upstream supplied a parseable value).
                _fire_record(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=model_id,
                    usage=None,
                    status=429,
                    team_id=authz.team_id,
                )
                _status_code = 429
                if exc.retry_after is not None:
                    raise UPSTREAM_RATE_LIMITED.exc(
                        headers={"Retry-After": str(int(exc.retry_after))}
                    ) from None
                raise UPSTREAM_RATE_LIMITED.exc() from None
            except (UpstreamUnavailableError, CircuitOpenError):
                # Circuit-breaker proxy has already counted the failure.
                _fire_record(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=model_id,
                    usage=None,
                    status=502,
                    team_id=authz.team_id,
                )
                _status_code = 502
                raise UPSTREAM_UNAVAILABLE.exc() from None

            # output-schema-validation (§3): bounded-retry loop. Runs BEFORE cache
            # store / post-call guardrail masking (M10 — validation must see the RAW,
            # unmasked content) and only when the M1 gate engaged AND attempt 1 came
            # back 200 (an upstream 4xx/5xx pass-through is never a "validation
            # failure" — it falls through unchanged, exactly like today). The retry
            # mechanics live in the module-level _run_output_validation_retry() —
            # extracted out of this already-large method (not inlined) so this
            # method stays within pyright's control-flow complexity limit.
            # _usage_source_final threads into the EXISTING bottom _fire_record_with_raw
            # call below (M8): None ⇒ default "frame" (no retry, or retry succeeded);
            # "validation_retry" ⇒ the retry's own attempt did NOT end in a validated
            # 200 (a genuine upstream error on the retry leg — never a fabricated 422
            # for a response the provider itself didn't say was invalid).
            _usage_source_final: str | None = None
            if _output_validation_engaged and status == 200:
                _rf = extract_response_format(body)  # already meta-validated pre-flight (M3)
                # Unreachable in practice: _check_output_validation already required and
                # meta-validated this exact shape before any upstream call fired. Narrows
                # the type for the subscript below without a strippable assert.
                if (
                    _rf is None
                    or _rf["type"] != "json_schema"
                    or (_json_schema_spec := _rf.get("json_schema")) is None
                ):
                    raise INVALID_JSON_SCHEMA.exc()
                _schema = _json_schema_spec["schema"]
                _outcome = validate_model_output(_schema, response_body)
                if not _outcome["valid"]:
                    # Any ProblemError raised here (429/502/422) propagates up through
                    # this method's own outer `except ProblemError` handler below —
                    # no local except clause needed.
                    (
                        status,
                        response_body,
                        served_model_id,
                        _usage_source_final,
                    ) = await _run_output_validation_retry(
                        authz=authz,
                        body=body,
                        upstream=upstream,
                        usage_recorder=usage_recorder,
                        model_router=model_router,
                        model_id=model_id,
                        schema=_schema,
                        status=status,
                        response_body=response_body,
                        served_model_id=served_model_id,
                    )

            # Store UNMASKED upstream body in cache on 200 (fire-and-forget).
            # Must run BEFORE post-call guardrail masking so the stored body is unmasked.
            # output-schema-validation (M9): engaged requests bypass the write tier too.
            # tenant-retention-zdr TASK.md §3 M6: zdr_enabled=true silently skips BOTH the
            # exact response cache and the vector-cache store (both live inside this same
            # gated block) — the proxied completion itself is unaffected either way.
            if (
                cache is not None
                and cache_enabled
                and status == 200
                and not _output_validation_engaged
                and not getattr(authz, "zdr_enabled", False)
            ):
                from gateway.proxy.infrastructure.response_cache import (
                    build_cache_key,
                    build_semantic_cache_key,
                )

                _sem_enabled = getattr(authz, "semantic_cache_enabled", False)
                _is_bypass = x_cache == "bypass"

                # On bypass with semantic_cache_enabled: the bypassed payload's OWN exact
                # key is NOT stored (it would shadow the semantic layer on the very next
                # request — SC15 contract). Instead the fresh body REFRESHES the entry the
                # semantic pointer references, honoring no-cache refresh intent: future
                # semantic hits serve the fresh body, never a stale one. Cold bypass (no
                # pointer / dangling) degrades to the normal store-both path.
                # On bypass without semantic: store the exact key normally (v4 behavior).
                # On normal miss (not bypass): always store the exact key (+ pointer).
                if not (_is_bypass and _sem_enabled):
                    ck = build_cache_key(str(authz.tenant_id), body)
                    # cache-alias-billing (B6): stamp the served candidate onto the STORED value
                    # (shallow copy — response_body returned to the client is untouched).
                    _fire_cache_set(
                        cache, ck, _stamp_served(response_body, served_model_id), cache_ttl_seconds
                    )

                    # Also store semantic pointer key if semantic cache is enabled (on MISS only,
                    # not on bypass — bypass leaves the pre-existing semantic pointer intact).
                    if _sem_enabled and hasattr(cache, "set_pointer"):
                        _sem_key = build_semantic_cache_key(str(authz.tenant_id), body)

                        def _fire_set_pointer(
                            _cache: Any,
                            _sem_key: str,
                            _exact_key: str,
                            _ttl: int,
                        ) -> None:
                            _task = asyncio.ensure_future(
                                _cache.set_pointer(_sem_key, _exact_key, _ttl)
                            )
                            _task.add_done_callback(
                                lambda t: t.exception() if not t.cancelled() else None
                            )

                        _fire_set_pointer(cache, _sem_key, ck, cache_ttl_seconds)
                elif hasattr(cache, "get_pointer") and hasattr(cache, "set_pointer"):
                    _sem_key = build_semantic_cache_key(str(authz.tenant_id), body)
                    _own_ck = build_cache_key(str(authz.tenant_id), body)

                    async def _refresh_semantic_entry(
                        _cache: Any,
                        _sem_key: str,
                        _own_ck: str,
                        _body: dict[str, Any],
                        _ttl: int,
                    ) -> None:
                        _pointed = await _cache.get_pointer(_sem_key)
                        if _pointed is not None and await _cache.get(_pointed) is not None:
                            # Refresh the pointed-to entry with the fresh upstream body.
                            await _cache.set(_pointed, _body, _ttl)
                        else:
                            # Cold/dangling: behave like a normal miss-store.
                            await _cache.set(_own_ck, _body, _ttl)
                            await _cache.set_pointer(_sem_key, _own_ck, _ttl)

                    _rtask = asyncio.ensure_future(
                        _refresh_semantic_entry(
                            cache,
                            _sem_key,
                            _own_ck,
                            _stamp_served(response_body, served_model_id),  # B6: stamp stored value
                            cache_ttl_seconds,
                        )
                    )
                    _rtask.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

                # Vector (embedding-similarity) layer store on a non-bypass MISS (semantic-cache):
                # register the served prompt's embedding pointing at the exact key just stored.
                # Fire-and-forget; store() is fail-safe (swallows all errors → no-op). The embed
                # call inside store() is internal — never billed to the served request.
                if self._vector_cache is not None and not _is_bypass:
                    _vtask = asyncio.ensure_future(
                        self._vector_cache.store(
                            tenant_id=str(authz.tenant_id),
                            model=model_id,
                            body=body,
                            response_body=response_body,
                            ttl=cache_ttl_seconds,
                        )
                    )
                    _vtask.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

            # Step 5.5: Post-call guardrails (non-streaming only, on 200 response body).
            # Applied AFTER cache store so the cached body remains unmasked.
            # evaluate_post is always fail-OPEN (post-call is MASK/AUDIT only — never BLOCK).
            if guardrail_evaluator is not None and guardrail_configs and status == 200:
                if hasattr(guardrail_evaluator, "evaluate_post"):
                    try:
                        response_body = await guardrail_evaluator.evaluate_post(
                            response_body, guardrail_configs
                        )
                    except Exception as _exc:
                        _log.warning(
                            "guardrail evaluate_post raised (fail-OPEN, returning original body)",
                            exc_info=_exc,
                        )

            # Record successful or upstream 4xx completion.
            # BILLING: use served_model_id (the catalog candidate we actually routed to),
            # NOT model_id (which may be an alias) and NOT response_body["model"]
            # (which may differ from the catalog id due to OpenRouter format variants).
            # §3 A1: served_model_id is the 3rd element of model_router.complete()'s 3-tuple.
            usage_raw = response_body.get("usage")
            usage: dict[str, Any] | None = usage_raw if isinstance(usage_raw, dict) else None
            _fire_record_with_raw(
                usage_recorder,
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=served_model_id,
                usage=usage,
                status=status,
                team_id=authz.team_id,
                pii_masked=_pii_masked,
                # output-schema-validation (M8): None (default) on every request that
                # never engaged the retry loop — "frame", unchanged. Set to
                # "validation_retry" ONLY for a retry leg that ended in a non-200
                # upstream pass-through (see the retry block above) — a validated
                # 200 success keeps the default "frame" too (M8's own rule).
                usage_source=_usage_source_final,
                request_id=_request_id,
            )
            # payload-capture-store §3: non-streaming completion capture hook.
            # response_body is the ALREADY evaluate_post-masked body (when configured) —
            # capture independently re-scrubs it anyway (its own try/except, never
            # trusting evaluate_post's fail-open return value as confirmed-scrubbed).
            # request-log-metering-fields §3: usage is the SAME dict just recorded above
            # (never a second extraction); latency_ms derived from the SAME _start_ns
            # this call's OtelSpan uses — never a second/independent clock read.
            _dispatch_capture(
                self._payload_capture,
                enabled=authz.payload_capture_enabled,
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=served_model_id,
                request_body=body,
                response_body=response_body,
                status=status,
                stream=False,
                cached=False,
                guardrail_configs=guardrail_configs,
                usage=usage,
                latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                request_id=_request_id,
            )
            # M8: Post-stream TPM accounting (non-blocking, swallows Redis errors)
            if authz.tpm_limit is not None and usage is not None:
                total_tokens = usage.get("total_tokens")
                if isinstance(total_tokens, int) and total_tokens > 0:
                    _fire_record_tpm(self._rate_limiter, key_id=authz.key_id, tokens=total_tokens)
            # bandwidth-usage-reconcile (v36): correct the non-stream pre-flight ESTIMATE to the
            # REAL response usage, fire-and-forget. Gated on active pacing (default-OFF schedules
            # nothing); total_tokens absent/0 ⇒ the estimate debit stands.
            if (
                not isinstance(self._bandwidth_bucket, PassthroughBandwidthBucket)
                and usage is not None
            ):
                _bw_real = usage.get("total_tokens")
                if isinstance(_bw_real, int) and _bw_real > 0:
                    _fire_bandwidth_reconcile(
                        self._bandwidth_bucket,
                        key_id=authz.key_id,
                        estimate=_bw_estimate,
                        real_tokens=_bw_real,
                    )
            _status_code = status
            return status, response_body, x_cache

        except ProblemError as _prob_err:
            # ProviderKeyMissing is pre-converted to ProblemError(402) by _resolve_credential.
            _status_code = _prob_err.status
            _error_code = _prob_err.code
            if _prob_err.code == "ERR_GUARDRAIL_BLOCKED":
                _guardrail_blocked = True
            raise
        except Exception:
            _status_code = 502
            raise
        finally:
            # Reset the per-request credential contextvar (credential-resolution-seam §3).
            # MUST run even on exception paths to prevent cross-request credential leaks.
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]
            # Span emission: only when authz succeeded (post-authz) and emitter is wired.
            # Pre-authz 401 → _authz is None → no span (inviolable §3 contract).
            if _authz is not None and self._span_emitter is not None:
                _emit_span_fire_forget(
                    self._span_emitter,
                    _authz,
                    _model_id,
                    _status_code,
                    False,  # stream=False for complete()
                    _cached,
                    _guardrail_blocked,
                    _start_ns,
                    error_code=_error_code,
                    fallback=_fallback,
                )

    async def stream(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
        model_router: FallbackModelRouter | None = None,
    ) -> AsyncIterator[bytes]:
        """Handle a streaming completion.

        Authenticates and validates before yielding any bytes.
        Returns an async generator of raw SSE byte chunks.
        On upstream error: raises ProblemError 502.

        Span emission contract (§3 pinned):
          - Errors raised BEFORE the generator is returned → emitted in the
            method-level finally (status from ProblemError or 502).
          - Successful streams → span emitted inside _wrapped() after the last
            chunk (status 200, end_time at last chunk).
          - Pre-authz 401 → _authz is None → no span (inviolable).
        """
        _start_ns = time.time_ns()
        # request-log-metering-fields TASK.md §3: one correlation UUID minted once per
        # call — captured by _wrapped()'s closure below, same as _start_ns already is.
        _request_id = uuid.uuid4()
        _authz: AuthzResult | None = None
        _stream_error_status: int | None = None  # set only on pre-generator errors
        _stream_error_code: str | None = None
        _stream_guardrail_blocked: bool = False
        _stream_pii_masked: bool = False
        _stream_model_id: str = ""
        _emitter = self._span_emitter
        _stream_cred_token: object = None  # set by _resolve_credential — reset in finally
        try:
            authz = await self._authenticate(raw_key)
            _authz = authz  # set ONLY after _authenticate succeeds — pre-authz 401 → no span
            # preset-resolution-ingress (v56 §3): resolve a <preset>:<alias> selector to the
            # tenant's target model BEFORE payload validation/governance/catalog/upstream.
            await self._resolve_preset(body, authz.tenant_id)
            # Validate payload fields (format only — catalog check is in _enforce_governance).
            # output-schema-validation: the engaged flag is discarded here on purpose —
            # a stream:true request that engages the M1 gate already raised 400
            # ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM inside _validate_payload
            # itself (M11); stream() has no retry/validate work of its own.
            model_id, _, _ = await self._validate_payload(body)
            _stream_model_id = model_id
            # Enforce governance: expiry → allowlist → catalog+tenant check → budget → rate limits
            # Pass model_groups from model_router for alias-aware catalog check (§3 A4).
            _stream_model_groups = model_router.model_groups if model_router is not None else None
            await self._enforce_governance(
                authz, model_id, self._budget_guard, _stream_model_groups
            )

            # unsupported-input-guard: check input modalities AFTER governance and BEFORE
            # credential resolution / upstream stream / usage (contract §3).
            await self._check_input_modalities(body, model_id, _stream_model_groups)

            # chat-modality-guard (v56 §3): coarse operation-type guard — a resolved model
            # whose CACHED modality is known and != "chat" is rejected before any I/O.
            await self._check_chat_modality(model_id)

            # Credential resolution (credential-resolution-seam §3) — same as complete().
            _stream_cred_token = await self._resolve_credential(authz.tenant_id, model_id)

            # openrouter-cost-recovery-wiring (v30 t6.2c): resolve the provider ONCE here
            # so the disconnect handler can gate inline recovery WITHOUT a new await in the
            # GeneratorExit/CancelledError teardown path. None when no resolver is wired.
            _stream_provider: str | None = None
            if self._cost_recovery is not None and self._provider_resolver is not None:
                _stream_provider = await self._provider_resolver.provider_for(model_id)

            # --- Step 4: Pre-call guardrails (after governance, before upstream stream) ---
            guardrail_evaluator = self._guardrail_evaluator
            guardrail_configs = getattr(authz, "guardrail_configs", {}) or {}
            if guardrail_evaluator is not None and guardrail_configs:
                # ml-moderation-layer §3 CONTRACT (FROZEN @ v1, §0 R6) — same seam as
                # complete() above: set/reset around the untouched 2-arg call.
                _gtid_token = set_guardrail_tenant_id(authz.tenant_id)
                try:
                    stream_result = await guardrail_evaluator.evaluate_pre(
                        body.get("messages", []), guardrail_configs
                    )
                except Exception as _exc:
                    _log.warning(
                        "guardrail evaluate_pre raised in stream (fail-CLOSED/OPEN)", exc_info=_exc
                    )
                    _has_block = any(
                        isinstance(cfg, dict) and cfg.get("enabled") and cfg.get("mode") == "block"
                        for cfg in guardrail_configs.values()
                    )
                    if _has_block:
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            guardrail_blocked=True,
                            blocked_by="error",
                            request_id=_request_id,
                        )
                        # payload-capture-store §3: pre-call guardrail-BLOCK hook (stream).
                        # request-log-metering-fields §3: usage=None -> tokens NULL.
                        _dispatch_capture(
                            self._payload_capture,
                            enabled=authz.payload_capture_enabled,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            request_body=body,
                            response_body=None,
                            status=400,
                            stream=True,
                            cached=False,
                            guardrail_configs=guardrail_configs,
                            usage=None,
                            latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                            request_id=_request_id,
                        )
                        _stream_error_status = 400
                        _stream_guardrail_blocked = True
                        raise GUARDRAIL_BLOCKED.exc() from None
                    stream_result = None
                finally:
                    reset_guardrail_tenant_id(_gtid_token)

                if stream_result is not None:
                    if stream_result.blocked:
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            guardrail_blocked=True,
                            blocked_by=stream_result.blocked_by,
                            request_id=_request_id,
                        )
                        # payload-capture-store §3: pre-call guardrail-BLOCK hook (stream).
                        # request-log-metering-fields §3: usage=None -> tokens NULL.
                        _dispatch_capture(
                            self._payload_capture,
                            enabled=authz.payload_capture_enabled,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            request_body=body,
                            response_body=None,
                            status=400,
                            stream=True,
                            cached=False,
                            guardrail_configs=guardrail_configs,
                            usage=None,
                            latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                            request_id=_request_id,
                        )
                        _stream_error_status = 400
                        _stream_guardrail_blocked = True
                        raise GUARDRAIL_BLOCKED.exc()
                    if stream_result.masked_messages is not None:
                        body = {**body, "messages": stream_result.masked_messages}
                        # §3: usage raw must carry pii_masked=true when pre-call
                        # masking fired (defect found by live v4 verification).
                        _stream_pii_masked = True

                # §1 documented v4 limitation: stream BODIES are not post-call inspected.
                # Emit the one-time audit-log event when pii_mask mode=mask is active.
                pii_cfg = guardrail_configs.get("pii_mask")
                if (
                    isinstance(pii_cfg, dict)
                    and pii_cfg.get("enabled")
                    and pii_cfg.get("mode") == "mask"
                ):
                    _log.info(
                        "streaming_pii_mask_skipped: post-call PII masking does not apply "
                        "to stream bodies (guardrails-core v4 documented limitation)"
                    )

            # first_chunk is the peeked pre-first-byte chunk from the resilient path (when
            # enabled) or from the rate-limit peek below; None on the old paths.
            # _wrapped() prepends it before draining `gen`.
            first_chunk: bytes | None = None
            # upstream-ratelimit-passthrough: set the partial-usage ContextVar BEFORE the
            # peek so that any upstream side-effects during the first __anext__() call
            # (e.g. publish_partial_usage in Gemini/Bedrock steppers) land in the same
            # per-request sink that _wrapped() will read on disconnect. _wrapped() skips
            # its own set() and reuses this token for reset(). None = not yet set.
            _pre_peek_partial_token: object = None
            # stream-alias-billing (B1): capture the SERVED candidate id from the routing
            # decision (default stream() -> strategy primary; resilient -> the candidate that
            # COMMITTED after pre-first-byte fallover). NEVER recompute caller-side —
            # simple-shuffle picks randomly, least-busy/latency depend on live state. Used to
            # bill on the served catalog model, not the alias (aliases have no pricing snapshot
            # -> silent $0). Populated synchronously (default) or by return time (resilient).
            _served_holder: list[str] = []

            def _capture_served(_served: str) -> None:
                _served_holder.append(_served)

            try:
                # Route stream through model_router when wired. With stream resilience enabled
                # (streaming-resilience v19), peek the first chunk via stream_resilient so a
                # PRE-first-byte failure falls over to the next candidate; a total pre-byte
                # failure raises here → the SAME except → 502 BEFORE StreamingResponse commits.
                # Otherwise the byte-identical OLD path: resolve to the first candidate only
                # (§3 STREAMING BOUNDARY); no fallback on stream failure.
                if model_router is not None and self._stream_resilience_enabled:
                    first_chunk, gen = await model_router.stream_resilient(
                        body, upstream=upstream, on_served=_capture_served
                    )
                elif model_router is not None:
                    gen = model_router.stream(body, upstream=upstream, on_served=_capture_served)
                    # upstream-ratelimit-passthrough: peek the first chunk so a pre-first-byte
                    # UpstreamRateLimitedError surfaces here (before StreamingResponse commits).
                    # Set the partial-usage sink now so peek-time side-effects are captured.
                    # A plain UpstreamUnavailableError (non-429) must NOT propagate here —
                    # reconstruct a "poisoned" generator so _wrapped() handles it as before
                    # (flag-off: empty-200, not a 502 before response commits).
                    _pre_peek_partial_token = partial_stream_usage.set({})
                    try:
                        first_chunk = await gen.__anext__()
                    except UpstreamRateLimitedError:
                        raise  # surfaces to the outer except UpstreamRateLimitedError handler
                    except UpstreamUnavailableError as _peek_exc:
                        # Plain unavailable pre-byte: reconstruct a generator that immediately
                        # re-raises the original exception so _wrapped()'s mid-stream catch
                        # handles it — emitting the terminal error frame + [DONE]
                        # (stream-upstream-error-frame, v35). The 200 is already committed.
                        # Bind the captured exception as a default arg so it survives the
                        # except block (where `_peek_exc` is deleted) — no late binding.
                        async def _poisoned(
                            _captured: BaseException = _peek_exc,
                        ) -> AsyncIterator[bytes]:
                            raise _captured
                            yield  # make it an async generator  # type: ignore[misc]

                        gen = _poisoned()
                        first_chunk = None
                    except StopAsyncIteration:
                        first_chunk = None  # empty stream — commit transparently
                else:
                    gen = upstream.stream(body)
                    _pre_peek_partial_token = partial_stream_usage.set({})
                    try:
                        first_chunk = await gen.__anext__()
                    except UpstreamRateLimitedError:
                        raise
                    except UpstreamUnavailableError as _peek_exc:
                        # Bind the captured exception as a default arg so it survives the
                        # except block (where `_peek_exc` is deleted) — no late binding.
                        async def _poisoned(
                            _captured: BaseException = _peek_exc,
                        ) -> AsyncIterator[bytes]:
                            raise _captured
                            yield  # make it an async generator  # type: ignore[misc]

                        gen = _poisoned()
                        first_chunk = None
                    except StopAsyncIteration:
                        first_chunk = None  # empty stream — commit transparently
            except UpstreamRateLimitedError as exc:
                # upstream-ratelimit-passthrough: upstream returned 429 pre-first-byte.
                # Status not yet committed — surface as client 429 ERR_UPSTREAM_RATE_LIMITED.
                # Reset the partial-usage sink set by the peek so the ContextVar never leaks
                # (mirrors the v34 _wrapped() finally — this error path skips _wrapped()).
                if _pre_peek_partial_token is not None:
                    partial_stream_usage.reset(_pre_peek_partial_token)
                _fire_record(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=model_id,
                    usage=None,
                    status=429,
                    team_id=authz.team_id,
                )
                _stream_error_status = 429
                if exc.retry_after is not None:
                    raise UPSTREAM_RATE_LIMITED.exc(
                        headers={"Retry-After": str(int(exc.retry_after))}
                    ) from None
                raise UPSTREAM_RATE_LIMITED.exc() from None
            except (UpstreamUnavailableError, CircuitOpenError):
                _fire_record(
                    usage_recorder,
                    tenant_id=authz.tenant_id,
                    key_id=authz.key_id,
                    model=model_id,
                    usage=None,
                    status=502,
                    team_id=authz.team_id,
                )
                _stream_error_status = 502
                raise UPSTREAM_UNAVAILABLE.exc() from None

            # stream-alias-billing (B1): the served candidate is now known — _capture_served
            # ran during routing (default path) / commit (resilient path) above. Bill + span
            # on the served catalog model, not the alias. No alias / no router -> stays model_id.
            if _served_holder:
                _stream_model_id = _served_holder[-1]

            tenant_id = authz.tenant_id
            key_id = authz.key_id
            team_id = authz.team_id
            tpm_limit = authz.tpm_limit
            rate_limiter = self._rate_limiter
            # payload-capture-store §3: captured before _wrapped() (mirrors the locals above).
            _capture_enabled = authz.payload_capture_enabled
            _payload_capture = self._payload_capture

            async def _wrapped() -> AsyncIterator[bytes]:
                collected: list[bytes] = []
                # bandwidth-pacing (v36): set once the shed branch has ALREADY fired its
                # terminal billing record. The shed branch yields the error frame + [DONE]
                # from INSIDE the outer try body, so a client disconnect DURING those yields
                # would otherwise reach the (GeneratorExit, CancelledError) handler below and
                # fire a SECOND record (double-bill). This flag gates that handler off.
                _bw_shed_handled = False
                # bandwidth-usage-reconcile (v36): Σ of the per-chunk estimates actually DEBITED
                # (paced chunks only; the TTFB chunk is unpaced ⇒ excluded). At a clean close — or a
                # disconnect with partial usage — this is reconciled to the REAL total_tokens so the
                # bucket carries true debt, not the estimate. _bw_active gates the fire so the
                # default-OFF (Passthrough) path schedules no reconcile task (byte-identical).
                _bw_estimate_total = 0
                _bw_active = not isinstance(self._bandwidth_bucket, PassthroughBandwidthBucket)
                # disconnect-billing-all-providers (v34): set a fresh per-request
                # partial-usage sink so native steppers can publish accumulated token
                # counts into it.  reset() in the finally block so it never leaks.
                # upstream-ratelimit-passthrough: when the peek already set the sink
                # (_pre_peek_partial_token is not None), reuse that token so we don't
                # overwrite publish_partial_usage() calls made during the peek.
                if _pre_peek_partial_token is not None:
                    _partial_token = _pre_peek_partial_token
                else:
                    _partial_token = partial_stream_usage.set({})
                try:
                    # Resilient path: the peeked first chunk was obtained pre-first-byte and
                    # COMMITS the stream — yield it before draining the rest (no replay after).
                    if first_chunk is not None:
                        collected.append(first_chunk)
                        yield first_chunk  # TTFB commit — UNPACED (never delay the first byte)
                    async for chunk in gen:
                        # bandwidth-pacing (v36): meter each OUTBOUND chunk against the per-key
                        # bucket BEFORE yielding. estimate = max(1, len(chunk)//4) (chars/4; the
                        # real token count is only in the terminal usage frame — reconcile at close
                        # is task 3). Default-OFF ⇒ Passthrough ⇒ this awaits an immediate no-op
                        # grant ⇒ byte-identical. fail-open is guaranteed by the bucket (admits).
                        _bw_chunk_est = max(1, len(chunk) // 4)
                        try:
                            await self._bandwidth_bucket.acquire(
                                key_id, _bw_chunk_est, self._bandwidth_max_wait_s
                            )
                        except BandwidthExhaustedError:
                            # The per-chunk wait budget is spent. A 200 was already sent, so we
                            # cannot 503 — record the streamed prefix (truncation billing, like
                            # the v35 upstream branch), emit a terminal error frame + [DONE], stop.
                            _bw_usage = extract_usage_from_sse(collected)
                            _bw_source = (
                                "frame"
                                if stream_usage_is_complete(_bw_usage)
                                else "stream_fallback"
                            )
                            _fire_record_with_raw(
                                usage_recorder,
                                tenant_id=tenant_id,
                                key_id=key_id,
                                # stream-alias-billing (B1): the bandwidth-shed truncation
                                # record is CHARGED (status=200) — bill the SERVED candidate,
                                # not the alias (third charged site; see clean-close @2048 +
                                # disconnect @1933). _stream_model_id == served here (post-commit).
                                model=_stream_model_id,
                                usage=_bw_usage,
                                status=200,
                                team_id=team_id,
                                pii_masked=_stream_pii_masked,
                                usage_source=_bw_source,
                                request_id=_request_id,
                            )
                            # payload-capture-store §3: bandwidth-shed truncation capture
                            # hook (1st of stream()'s 3 exit branches) — the assembled
                            # (partial) assistant text, size-capped like the other 2.
                            # request-log-metering-fields §3: usage=_bw_usage (the SAME
                            # dict just recorded above); latency_ms from the SAME
                            # _start_ns this call's OtelSpan uses (closure-captured).
                            _dispatch_capture(
                                _payload_capture,
                                enabled=_capture_enabled,
                                tenant_id=tenant_id,
                                key_id=key_id,
                                model=_stream_model_id,
                                request_body=body,
                                response_body=_capture_response_body(collected),
                                status=200,
                                stream=True,
                                cached=False,
                                guardrail_configs=guardrail_configs,
                                usage=_bw_usage,
                                latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                                request_id=_request_id,
                            )
                            # record fired — gate the disconnect handler so a drop during the
                            # two yields below cannot double-bill this request.
                            _bw_shed_handled = True
                            yield _sse_error_frame(
                                "ERR_BANDWIDTH_EXHAUSTED", "bandwidth limit exceeded"
                            )
                            if not (collected and b"[DONE]" in collected[-1]):
                                yield b"data: [DONE]\n\n"
                            return
                        # acquire granted ⇒ this chunk's estimate was debited; track it for the
                        # estimate→real reconcile at close (bandwidth-usage-reconcile v36).
                        _bw_estimate_total += _bw_chunk_est
                        collected.append(chunk)
                        yield chunk
                except (UpstreamUnavailableError, CircuitOpenError) as exc:
                    # Can't change status code mid-stream; record and stop
                    _fire_record(
                        usage_recorder,
                        tenant_id=tenant_id,
                        key_id=key_id,
                        model=model_id,
                        usage=None,
                        status=502,
                        team_id=team_id,
                    )
                    # stream-upstream-error-frame (v35): emit a parseable error chunk so
                    # a [DONE]-waiting agent loop (e.g. Helios) never hangs on truncation.
                    _code = (
                        "ERR_UPSTREAM_RATE_LIMITED"
                        if isinstance(exc, UpstreamRateLimitedError)
                        else "ERR_UPSTREAM_UNAVAILABLE"
                    )
                    yield _sse_error_frame(_code, "upstream error")
                    if not (collected and b"[DONE]" in collected[-1]):
                        yield b"data: [DONE]\n\n"
                    return
                except (GeneratorExit, asyncio.CancelledError):
                    # bandwidth-pacing (v36): if the shed branch already fired its terminal
                    # record, a disconnect during its error-frame/[DONE] yields must NOT bill
                    # again. Still close the upstream and re-raise so the close/cancel completes.
                    if _bw_shed_handled:
                        if isinstance(gen, AsyncGenerator):
                            with contextlib.suppress(BaseException):
                                await gen.aclose()
                        raise
                    # stream-disconnect-billing: the client dropped mid-stream
                    # (GeneratorExit raised at the suspended yield when Starlette calls the
                    # generator's aclose()) or the request task was cancelled
                    # (CancelledError). BEFORE this handler that early-close skipped the
                    # post-stream record block entirely → ZERO ledger rows for a
                    # partially-streamed, paid-for response (a silent $0 distinct from the
                    # v27 missing-frame case). Fire EXACTLY ONE flagged record — no await,
                    # sync fire-and-forget via the independent ensure_future task — then
                    # RE-RAISE so the close/cancel completes (never swallow it). A complete
                    # usage frame that arrived before the disconnect still bills as 'frame'.
                    disconnect_usage = extract_usage_from_sse(collected)
                    # provider-generation-id-capture (v30 t6): stamp the provider's SSE
                    # generation id on the disconnect row so cost-recovery can look up the
                    # authoritative cost later. None when the stream carried no id (→ NULL).
                    disconnect_gen_id = extract_generation_id_from_sse(collected)
                    if stream_usage_is_complete(disconnect_usage):
                        disconnect_source = "frame"
                    else:
                        disconnect_source = "client_disconnect"
                        # disconnect-billing-all-providers (v34): if the complete SSE frame
                        # hasn't arrived yet, read the partial-usage sink that native steppers
                        # publish into as tokens accrue.  A non-empty partial floor becomes
                        # the disconnect usage so the recorder's estimate block can stamp
                        # provider_cost (cost_usd=0, cost_basis='provider') — visible in drift.
                        # Malformed/negative sink data → None + WARN (fail-safe, no raise).
                        if disconnect_usage is None:
                            _partial = read_partial_usage()
                            if _partial is not None:
                                disconnect_usage = _partial
                        _log.warning(
                            "stream_client_disconnect",
                            extra={"model": model_id, "tenant_id": str(tenant_id)},
                        )
                    # disconnect-provider-cost (v34): recoverability-based gate.
                    # A disconnect is recoverable ONLY when the provider is OpenRouter AND
                    # a generation id was captured — the v30 recovery chain (inline + sweep)
                    # keys exclusively on provider_generation_id, so only OpenRouter gen-ids
                    # (gen-…) feed a recovery candidate.  Anthropic (msg_…) and Azure
                    # (chatcmpl-…) gen-ids are NOT recoverable — they were silently invisible
                    # under the old gen-id-absence gate; now they are stamped (estimate=True)
                    # and visible to the drift monitor. Double-count is impossible: OpenRouter
                    # recoverable rows (estimate=False) are left to the recovery chain exactly
                    # as before; their estimate is suppressed regardless of any partial sink.
                    recoverable = _stream_provider == "openrouter" and bool(disconnect_gen_id)
                    disconnect_estimate = (
                        disconnect_source == "client_disconnect" and not recoverable
                    )
                    _fire_record_with_raw(
                        usage_recorder,
                        tenant_id=tenant_id,
                        key_id=key_id,
                        model=_stream_model_id,  # B1: served candidate, not the alias
                        usage=disconnect_usage,
                        status=200,
                        team_id=team_id,
                        pii_masked=_stream_pii_masked,
                        usage_source=disconnect_source,
                        provider_generation_id=disconnect_gen_id,
                        disconnect_estimate=disconnect_estimate,
                        request_id=_request_id,
                    )
                    # payload-capture-store §3: client-disconnect capture hook (2nd of
                    # stream()'s 3 exit branches) — the assembled (partial) text.
                    # request-log-metering-fields §3: usage=disconnect_usage (the SAME
                    # dict just recorded above); latency_ms from the SAME _start_ns.
                    _dispatch_capture(
                        _payload_capture,
                        enabled=_capture_enabled,
                        tenant_id=tenant_id,
                        key_id=key_id,
                        model=_stream_model_id,
                        request_body=body,
                        response_body=_capture_response_body(collected),
                        status=200,
                        stream=True,
                        cached=False,
                        guardrail_configs=guardrail_configs,
                        usage=disconnect_usage,
                        latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                        request_id=_request_id,
                    )
                    # bandwidth-usage-reconcile (v36, Tin): a disconnect ALSO reconciles the
                    # estimate debited so far toward the PARTIAL usage actually generated, when a
                    # partial total is known. Gated on active pacing; total_tokens absent ⇒ the
                    # estimate debit stands (no truth to correct toward). Fire-and-forget.
                    if _bw_active and isinstance(disconnect_usage, dict):
                        _bw_partial_real = disconnect_usage.get("total_tokens")
                        if isinstance(_bw_partial_real, int) and _bw_partial_real > 0:
                            _fire_bandwidth_reconcile(
                                self._bandwidth_bucket,
                                key_id=key_id,
                                estimate=_bw_estimate_total,
                                real_tokens=_bw_partial_real,
                            )
                    # disconnect-provider-cost (v30 t5): "spawn the stop event to the
                    # provider" — deterministically close the upstream generator NOW so the
                    # close (GeneratorExit) propagates into the adapter → the httpx response
                    # is released → TCP FIN to the provider → it STOPS generating (and billing
                    # us) at the disconnect point, instead of the connection lingering until
                    # the event-loop async-gen finalizer (GC) runs. Best-effort: swallow ANY
                    # error from aclose() (a misbehaving adapter raising during teardown must
                    # never mask the original disconnect/cancel, which we re-raise below).
                    # Awaiting here is legal during GeneratorExit/CancelledError handling — we
                    # await, never yield. The incremental-stream refactor (v30 t3/t4) is what
                    # makes this actually save cost on the previously-buffered providers.
                    # suppress BaseException, not just Exception: when this handler runs on
                    # the CancelledError edge, the adapter's own async teardown (httpx stream
                    # __aexit__) can re-raise CancelledError — a BaseException in py3.11+ —
                    # which would otherwise ESCAPE and mask the original disconnect/cancel.
                    if isinstance(gen, AsyncGenerator):
                        with contextlib.suppress(BaseException):
                            await gen.aclose()
                    # openrouter-cost-recovery-wiring (v30 t6.2c): schedule authoritative
                    # cost recovery as a FIRE-AND-FORGET task — ONLY for a RECOVERABLE
                    # disconnect (OpenRouter + gen-id), and only when the service is wired
                    # (default-OFF knob). Best-effort: ensure_future never awaits, and the
                    # whole schedule is suppressed so it can NEVER mask the re-raise below
                    # (a cancelled/failed inline attempt is re-covered by the t6.3 sweep).
                    # No await here — _stream_provider was resolved at setup.
                    # disconnect_gen_id is already guaranteed non-None when recoverable=True
                    # (line above: recoverable = ... and bool(disconnect_gen_id)), so this
                    # extra guard is behavior-preserving and narrows the type for pyright.
                    if (
                        self._cost_recovery is not None
                        and recoverable
                        and disconnect_gen_id is not None
                    ):
                        with contextlib.suppress(BaseException):
                            _recovery_task = asyncio.ensure_future(
                                self._cost_recovery.recover(
                                    tenant_id=tenant_id,
                                    key_id=key_id,
                                    # B1: served candidate — consistent with the disconnect
                                    # billing row so recovery re-prices on the catalog
                                    # candidate, not the alias (which has no pricing snapshot).
                                    model=_stream_model_id,
                                    provider_generation_id=disconnect_gen_id,
                                )
                            )
                            # Same fire-and-forget hygiene as the other ensure_future sites
                            # here: retrieve any exception so a CancelledError escaping
                            # recover() at shutdown (its guard is `except Exception`, not
                            # BaseException) never logs "Task exception was never retrieved".
                            _recovery_task.add_done_callback(
                                lambda t: t.exception() if not t.cancelled() else None
                            )
                    raise
                finally:
                    # disconnect-billing-all-providers (v34): reset the partial-usage sink
                    # so it never outlives the stream.  Tolerates cross-context reset (same
                    # reasoning as the credential reset below).
                    try:
                        partial_stream_usage.reset(_partial_token)
                    except ValueError:
                        pass
                    # credential-resolution-seam §3: clear the per-request credential once
                    # the stream is fully consumed (normal end), errors out, or is closed
                    # early (GeneratorExit on client disconnect). The token was produced in
                    # the request task's context where set_provider_credential() ran in the
                    # method body; reset it here so the success path is symmetric with the
                    # pre-generator error path (which resets in the method-level finally) and
                    # the credential never outlives the stream. Tolerate a cross-context reset
                    # (Starlette may drain the StreamingResponse in a copied context) — the
                    # original context is discarded at task end, so this never leaks.
                    if _stream_cred_token is not None:
                        try:
                            reset_provider_credential(_stream_cred_token)  # type: ignore[arg-type]
                        except ValueError:
                            pass
                # Tee: extract usage from collected SSE chunks after stream completes.
                # stream-usage-completeness: a complete frame wins byte-identically
                # (usage_source="frame"); a missing/partial frame is flagged
                # "stream_fallback" + WARN so the $0 bill is never SILENT (the bytes
                # already reached the client — accuracy is never an availability gate).
                extracted_usage = extract_usage_from_sse(collected)
                if stream_usage_is_complete(extracted_usage):
                    usage_source = "frame"
                else:
                    usage_source = "stream_fallback"
                    _log.warning(
                        "stream_usage_frame_missing",
                        extra={"model": model_id, "tenant_id": str(tenant_id)},
                    )
                _fire_record_with_raw(
                    usage_recorder,
                    tenant_id=tenant_id,
                    key_id=key_id,
                    model=_stream_model_id,  # B1: served candidate, not the alias
                    usage=extracted_usage,
                    status=200,
                    team_id=team_id,
                    pii_masked=_stream_pii_masked,
                    usage_source=usage_source,
                    request_id=_request_id,
                )
                # payload-capture-store §3: clean-close capture hook (3rd of stream()'s
                # 3 exit branches, and the one usual case) — the fully assembled text,
                # keyed off the SAME stream_usage_is_complete completeness signal the
                # billing path just used above (§2 scenario: "streaming capture
                # assembles and writes the full response text").
                # request-log-metering-fields §3: usage=extracted_usage (the SAME dict
                # just recorded above); latency_ms from the SAME _start_ns.
                _dispatch_capture(
                    _payload_capture,
                    enabled=_capture_enabled,
                    tenant_id=tenant_id,
                    key_id=key_id,
                    model=_stream_model_id,
                    request_body=body,
                    response_body=_capture_response_body(collected),
                    status=200,
                    stream=True,
                    cached=False,
                    guardrail_configs=guardrail_configs,
                    usage=extracted_usage,
                    latency_ms=round((time.time_ns() - _start_ns) / 1_000_000),
                    request_id=_request_id,
                )
                # M8: Post-stream TPM accounting (fire-and-forget, never blocks response)
                if tpm_limit is not None and isinstance(extracted_usage, dict):
                    total_tokens = extracted_usage.get("total_tokens")
                    if total_tokens and isinstance(total_tokens, int) and total_tokens > 0:
                        _fire_record_tpm(rate_limiter, key_id=key_id, tokens=total_tokens)
                # bandwidth-usage-reconcile (v36): correct the paced ESTIMATE to the REAL usage at
                # clean close, fire-and-forget (never blocks). Gated on active pacing so default-OFF
                # schedules nothing; total_tokens absent/0 ⇒ no truth ⇒ the estimate debit stands.
                if _bw_active and isinstance(extracted_usage, dict):
                    _bw_real = extracted_usage.get("total_tokens")
                    if isinstance(_bw_real, int) and _bw_real > 0:
                        _fire_bandwidth_reconcile(
                            self._bandwidth_bucket,
                            key_id=key_id,
                            estimate=_bw_estimate_total,
                            real_tokens=_bw_real,
                        )
                # Successful stream span — emitted here after the last chunk (§3 pinned).
                # _authz and _emitter are captured from the enclosing scope.
                if _authz is not None and _emitter is not None:  # pyright: ignore[reportUnnecessaryComparison]  — defensive None check; _authz captured from enclosing scope
                    _emit_span_fire_forget(
                        _emitter,
                        _authz,
                        _stream_model_id,  # B1: served candidate, not the alias
                        200,
                        True,  # stream=True
                        False,  # cached=False (streaming never cache-hits)
                        False,  # guardrail_blocked=False (blocked before reaching here)
                        _start_ns,
                    )

            # Successful generator setup — _wrapped() will emit the span itself.
            # Signal to the method-level finally that no pre-generator error occurred.
            _stream_error_status = None
            return _wrapped()

        except ProblemError as _prob_err:
            # ProviderKeyMissing is pre-converted to ProblemError(402) by _resolve_credential.
            if _stream_error_status is None:
                _stream_error_status = _prob_err.status
            _stream_error_code = _prob_err.code
            if _prob_err.code == "ERR_GUARDRAIL_BLOCKED":
                _stream_guardrail_blocked = True
            raise
        except Exception:
            if _stream_error_status is None:
                _stream_error_status = 502
            raise
        finally:
            # Reset the per-request credential contextvar on the PRE-GENERATOR error path
            # ONLY (auth/governance/guardrail/stream-setup failure → _wrapped() never runs,
            # so it cannot reset). On success (_stream_error_status is None) the contextvar
            # MUST stay set: the resilient peek already fired _auth_headers() in this body,
            # and the lazy path fires it during _wrapped() iteration — _wrapped()'s own
            # finally then resets it after the last chunk (symmetric, exactly-once).
            if _stream_cred_token is not None and _stream_error_status is not None:
                reset_provider_credential(_stream_cred_token)  # type: ignore[arg-type]
            # Only emit here for pre-generator errors (auth/governance/guardrail failures).
            # Successful streams set _stream_error_status=None before returning — _wrapped()
            # handles the span in that case.
            if _authz is not None and _emitter is not None and _stream_error_status is not None:
                _emit_span_fire_forget(
                    _emitter,
                    _authz,
                    _stream_model_id,
                    _stream_error_status,
                    True,  # stream=True
                    False,  # cached=False
                    _stream_guardrail_blocked,
                    _start_ns,
                    error_code=_stream_error_code,
                )
