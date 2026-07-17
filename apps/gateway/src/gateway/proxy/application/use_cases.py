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
import contextvars
import datetime
import json
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
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
    PAYLOAD_TAGS_INVALID,
    PLAN_MODEL_NOT_ALLOWED,
    PRESET_NOT_FOUND,
    RATE_LIMITED,
    RESIDENCY_NO_ELIGIBLE_REGION,
    UPSTREAM_RATE_LIMITED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.credits.domain.ports import CreditGuard, PassthroughCreditGuard
from gateway.guardrail_analytics.application.verdict_recorder import record_guardrail_verdicts
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.logs.domain.sse_content_extractor import extract_content_from_sse
from gateway.proxy.application.residency import (
    cache_hit_region_ok,
    check_residency_existence,
)
from gateway.proxy.domain.credential_context import (
    mark_platform_fallback,
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import (
    AllCandidatesOutOfRegionError,
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
    PlatformCredentialFallback,
    ProviderResolver,
    ResidencyLookup,
    ResponseCache,
    TenantCredentialResolver,
    UsageRecorder,
    UsageRecordExtras,
    VectorCache,
)
from gateway.proxy.domain.post_call_redaction import (
    POST_MASK_REDACTION as _POST_MASK_REDACTION,
)
from gateway.proxy.domain.post_call_redaction import (
    redact_response_body as _redact_response_body,
)
from gateway.proxy.domain.provider_credentials import (
    BYOK_PROVIDERS,
    ProviderKeyMissing,
)
from gateway.proxy.domain.response_format_translation import extract_response_format
from gateway.proxy.domain.tier_capacity import ServiceTier, TierCapacityGuard
from gateway.proxy.infrastructure.tier_capacity_guard import PassthroughTierCapacityGuard
from gateway.rate_limits.application.passthrough import (
    PassthroughBandwidthBucket,
    PassthroughRateLimiter,
)
from gateway.rate_limits.application.tenant_rate_limit import (
    enforce_tenant_rate_limit,
    tenant_tpm_ctx,
)
from gateway.rate_limits.domain.errors import (
    BandwidthExhaustedError,
    RateLimitExceededError,
)
from gateway.rate_limits.domain.ports import BandwidthBucket, BandwidthGrant, RateLimiter
from gateway.rate_limits.infrastructure.plan_rate_limit_resolver import PlanRateLimitResolver
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


def _fire_record_tpm_tenant(rate_limiter: RateLimiter, *, tokens: int) -> None:
    """Fire the tenant-window TPM record IF this request's tenant TPM ceiling was
    active (plan-rate-enforcement TASK.md §3, M4) — consumes `tenant_tpm_ctx` published
    by `enforce_tenant_rate_limit()`. Mirrors `_fire_record_tpm`'s own fire-and-forget
    shape (never blocks, swallows all errors via that same helper); no-op when the
    ContextVar was never set (unplanned/uncapped tenant — inert by construction).
    """
    ctx = tenant_tpm_ctx.get()
    if ctx is None:
        return
    tenant_id, _tenant_tpm_limit = ctx
    _fire_record_tpm(rate_limiter, key_id=tenant_id, tokens=tokens)


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


# cost-attribution-tags (TASK.md §3): X-Gateway-Tags bounds — a reasoned analogy from
# tenants/api/guardrail_router.py's _MAX_CUSTOM_PATTERNS/_MAX_PATTERN_BYTES precedent,
# Tin-confirmed at freeze for the cost-tags domain specifically.
_TAGS_MAX_HEADER_BYTES = 2048
_TAGS_MAX_COUNT = 8
_TAGS_MAX_KEY_LEN = 32
_TAGS_MAX_VALUE_BYTES = 256
# Key format: same regex reused (mirrored, not imported — see usage/api/router.py's
# own _TAG_KEY_RE) by the GET /admin/usage/cost-by-tag ?tag_key= filter (R8).
TAG_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")


def _parse_tags_header(raw: str | None) -> dict[str, str]:
    """Parse + validate the X-Gateway-Tags request header (cost-attribution-tags §3).

    Byte-identical fast path (M3): raw is None (header absent, the overwhelming
    majority of traffic) -> {} immediately, no json.loads, no regex — one cheap
    "header present?" check.

    Validation order (mirrors _validate_custom_patterns's ordered V1..Vn style):
      R5 raw header byte-length > 2048           -> PAYLOAD_TAGS_INVALID (checked
                                                     FIRST — json.loads never runs
                                                     on an oversized value)
      R1 malformed JSON                          -> PAYLOAD_TAGS_INVALID
      R1 parsed value is not a flat JSON object   -> PAYLOAD_TAGS_INVALID
      R2 more than 8 distinct keys                -> PAYLOAD_TAGS_INVALID
      R3 a key >32 chars or not matching TAG_KEY_RE -> PAYLOAD_TAGS_INVALID
      R1 a value is not a string                  -> PAYLOAD_TAGS_INVALID
      R4 a value >256 UTF-8 bytes                  -> PAYLOAD_TAGS_INVALID

    Raised BEFORE governance/upstream by every caller (R1-R5) — a malformed-tags
    request is NEVER billed. Keys/values are stored EXACTLY as sent — no case
    folding, no normalization (an explicit Must in §1).
    """
    if raw is None:
        return {}
    if len(raw.encode("utf-8")) > _TAGS_MAX_HEADER_BYTES:
        raise PAYLOAD_TAGS_INVALID.exc(detail="tags header too large")
    try:
        parsed: Any = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        raise PAYLOAD_TAGS_INVALID.exc(detail="malformed JSON in X-Gateway-Tags") from None
    if not isinstance(parsed, dict):
        raise PAYLOAD_TAGS_INVALID.exc(detail="X-Gateway-Tags must be a flat JSON object")
    if len(parsed) > _TAGS_MAX_COUNT:
        raise PAYLOAD_TAGS_INVALID.exc(detail="too many tags")
    result: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str) or len(k) > _TAGS_MAX_KEY_LEN or not TAG_KEY_RE.match(k):
            raise PAYLOAD_TAGS_INVALID.exc(detail=f"invalid tag key: {k!r}")
        if not isinstance(v, str):
            raise PAYLOAD_TAGS_INVALID.exc(detail=f"tag value must be a string for key {k!r}")
        if len(v.encode("utf-8")) > _TAGS_MAX_VALUE_BYTES:
            raise PAYLOAD_TAGS_INVALID.exc(detail=f"tag value too long for key {k!r}")
        result[k] = v
    return result


# credits-ledger TASK.md §3: set (once) by CompletionUseCase.complete()/stream() right
# after an admission HOLD is placed (_enforce_governance succeeds); read by
# _dispatch_record so settle()/release() fire from the SAME fire-and-forget site as
# usage_recorder.record() (§3 wiring note) WITHOUT threading credit_guard through every
# _fire_record/_fire_record_cached/_fire_record_with_raw call site (~25 of them). asyncio
# copies the contextvar context at ensure_future()/create_task() time, so each concurrent
# request (its own top-level Task) sees only its OWN hold — no cross-request leakage.
_credit_hold_ctx: contextvars.ContextVar[tuple[CreditGuard, uuid.UUID] | None] = (
    contextvars.ContextVar("_credit_hold_ctx", default=None)
)

# service-tiers TASK.md §3 (FROZEN @ v1, M9): set alongside _credit_hold_ctx.set(...),
# immediately after the tier hold succeeds — consumed by _settle_or_release_hold to
# release the tier-capacity slot once the (possibly-streaming) request's usage record
# is fully dispatched, mirroring _credit_hold_ctx's own publish/consume shape exactly.
_tier_hold_ctx: contextvars.ContextVar[tuple[TierCapacityGuard, uuid.UUID] | None] = (
    contextvars.ContextVar("_tier_hold_ctx", default=None)
)

# service-tiers TASK.md §3 (M10, a Build judgment call — not itself a §3-named symbol):
# the (tier_served, tier_capacity_degraded) pair resolved ONCE in _enforce_governance/
# NonChatGovernance.authorize, published alongside _tier_hold_ctx, and consumed by
# _dispatch_record so tier_served/tier_capacity_degraded reach whichever _fire_record*
# call site this SAME request eventually dispatches through — WITHOUT threading two new
# kwargs through ~25 _fire_record*/_fire_record_with_raw call sites (the same rationale
# _credit_hold_ctx's own docstring gives for its contextvar-publish-then-consume shape).
_tier_served_ctx: contextvars.ContextVar[tuple[ServiceTier, bool] | None] = contextvars.ContextVar(
    "_tier_served_ctx", default=None
)

# claude-gateway-protocol-compat TASK.md §3 (M4): published once near the top of
# complete()/stream() from `request_headers` (already a parameter of both), consumed
# by _dispatch_record alongside _tier_served_ctx — the SAME "avoid threading 3 new
# kwargs through ~25 _fire_record*/_fire_record_with_raw call sites" rationale
# _credit_hold_ctx's own docstring gives. None (default, or every header absent) ⇒
# every entry stays None ⇒ _dispatch_record adds nothing ⇒ byte-identical to today.
_cc_attribution_ctx: contextvars.ContextVar[tuple[str | None, str | None, str | None] | None] = (
    contextvars.ContextVar("_cc_attribution_ctx", default=None)
)

# platform-key-default fallback-usage-marker TASK.md §3 (FROZEN @ v1, M4): the credential-source
# provenance for THIS request. Published ONCE inside _resolve_platform_fallback (set "platform"
# right after mark_platform_fallback()), consumed by _dispatch_record alongside _cc_attribution_ctx
# — the SAME "avoid threading a kwarg through ~25 _fire_record* call sites" rationale the
# contextvars above give. Deliberately NOT reset mid-request (unlike the credential-scoped
# served_via flag, which reset_provider_credential clears BEFORE the stream terminal record
# dispatches — the §0 ordering trap): this marker must outlive that reset. Default None ⇒ byok ⇒
# _dispatch_record adds nothing ⇒ byte-identical to today. Each proxied request is its own asyncio
# Task (context copied at task creation), so a set-only-on-fallback value cannot leak platform→byok
# across requests — the SAME guarantee _credit_hold_ctx / _tier_served_ctx already depend on.
_credential_source_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_credential_source_ctx", default=None
)


def _publish_cc_attribution(request_headers: dict[str, str] | None) -> None:
    """Extract x-claude-code-session-id/-agent-id/-parent-agent-id from the inbound
    request headers (CONSUMED here, never forwarded upstream — no adapter ever reads
    `request_headers`) and publish them onto `_cc_attribution_ctx` for this request.

    A no-op call (all three absent) still `.set()`s a (None, None, None) tuple rather
    than leaving the ContextVar unset — harmless (the consumer already checks each
    element for None) and cheaper than a conditional set.
    """
    headers = request_headers or {}
    session_id = headers.get("x-claude-code-session-id")
    agent_id = headers.get("x-claude-code-agent-id")
    parent_agent_id = headers.get("x-claude-code-parent-agent-id")
    _cc_attribution_ctx.set((session_id, agent_id, parent_agent_id))


def _settle_or_release_hold(task: asyncio.Task[object | None]) -> None:
    """Task done-callback: consume the recorder's UsageRecordOutcome (if any) and settle
    or release the open credit hold for the current request. Never raises — a missing
    outcome (older/duck-typed recorder, or record() itself failed) is a no-op; the M6
    reconciliation sweep is the backstop for anything left unresolved here.

    service-tiers TASK.md §3 M9: ALSO consumes _tier_hold_ctx and releases the tier
    capacity hold UNCONDITIONALLY (no settle/release split needed — a capacity slot is
    a binary occupied/free thing, not a money amount, deliberate simplification vs
    CreditGuard's separate settle()/release()). This release fires regardless of
    whether the credit-hold branch above found anything to settle, so a tier hold is
    never stranded by a credit-side no-op (e.g. a free/cached response).
    """
    tier_ctx = _tier_hold_ctx.get()
    if tier_ctx is not None and not task.cancelled() and task.exception() is None:
        tier_guard, tier_request_id = tier_ctx
        result = task.result()
        tier_tenant_id = getattr(result, "tenant_id", None)
        if tier_tenant_id is not None:
            release_task = asyncio.ensure_future(
                tier_guard.release(tier_tenant_id, tier_request_id)
            )
            release_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    if task.cancelled() or task.exception() is not None:
        return
    ctx = _credit_hold_ctx.get()
    if ctx is None:
        return
    credit_guard, request_id = ctx
    result = task.result()
    cost_usd = getattr(result, "cost_usd", None)
    usage_record_id = getattr(result, "usage_record_id", None)
    free = getattr(result, "free", None)
    if cost_usd is None or usage_record_id is None or free is None:
        return
    tenant_id = getattr(result, "tenant_id", None)
    if tenant_id is None:
        return
    if free:
        settle_task = asyncio.ensure_future(credit_guard.release(tenant_id, request_id))
    else:
        settle_task = asyncio.ensure_future(
            credit_guard.settle(tenant_id, request_id, usage_record_id, cost_usd)
        )
    settle_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)


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

    credits-ledger TASK.md §3: when a credit hold is open for the CURRENT request
    (see _credit_hold_ctx) AND usage_recorder exposes `record_with_outcome` (duck-typed
    via hasattr — RecordingUsageRecorder does; a v1-Protocol test fake normally does
    not), that method is called INSTEAD of record() so the settle/release callback can
    read back the already-computed cost. record()'s own contract/behavior is completely
    unchanged for every other caller (byte-identical — see record_with_outcome's
    docstring for why this is a NEW method rather than widening record()'s return).

    service-tiers TASK.md §3 (M9/M10, a Build judgment call): the SAME
    record_with_outcome branch also fires when a TIER hold is open (independent of
    whether a credit hold is ALSO open — the two guards are orthogonal, and a
    tenant may have tiering wired without credits), so _settle_or_release_hold
    always gets a chance to release the tier-capacity slot. Before dispatch,
    tier_served/tier_capacity_degraded (resolved once in _enforce_governance,
    published via _tier_served_ctx) are folded into kwargs — filtered against
    `supported_extras` exactly like every other extra, so a v1-Protocol fake
    without the capability silently receives only the base kwargs.
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

    tier_served_ctx = _tier_served_ctx.get()
    if tier_served_ctx is not None and "tier_served" in supported:
        _tier_served_value, _tier_degraded_value = tier_served_ctx
        kwargs["tier_served"] = _tier_served_value
        kwargs["tier_capacity_degraded"] = _tier_degraded_value

    # claude-gateway-protocol-compat TASK.md §3 (M4): fold cc_session_id/cc_agent_id/
    # cc_parent_agent_id in from the ContextVar published once by
    # _publish_cc_attribution — same filter-against-supported_extras discipline as
    # every other extra above; a v1-Protocol fake without the capability silently
    # receives nothing new.
    cc_ctx = _cc_attribution_ctx.get()
    if cc_ctx is not None:
        _cc_session_id, _cc_agent_id, _cc_parent_agent_id = cc_ctx
        if _cc_session_id is not None and "cc_session_id" in supported:
            kwargs["cc_session_id"] = _cc_session_id
        if _cc_agent_id is not None and "cc_agent_id" in supported:
            kwargs["cc_agent_id"] = _cc_agent_id
        if _cc_parent_agent_id is not None and "cc_parent_agent_id" in supported:
            kwargs["cc_parent_agent_id"] = _cc_parent_agent_id

    # fallback-usage-marker §3 (M1/M4): fold in the platform-fallback provenance published once by
    # _resolve_platform_fallback — same filter-against-supported_extras discipline as every extra
    # above; default None (own key / no fallback) ⇒ nothing added ⇒ byte-identical to today (M2/R2),
    # and a v1-Protocol fake without the capability silently receives nothing (M5/R1).
    credential_source = _credential_source_ctx.get()
    if credential_source is not None and "credential_source" in supported:
        kwargs["credential_source"] = credential_source

    credit_ctx = _credit_hold_ctx.get()
    tier_hold_ctx = _tier_hold_ctx.get()
    # Explicit annotation (not `callable()` narrowing) — callable() narrows to
    # Callable[..., object], which pyright then rejects as an ensure_future() arg
    # (object is not Awaitable). getattr's own Any-typed return, propagated through
    # this declared annotation, keeps the awaited call's return type concrete.
    record_with_outcome: Callable[..., Awaitable[Any]] | None = getattr(
        usage_recorder, "record_with_outcome", None
    )
    if (credit_ctx is not None or tier_hold_ctx is not None) and record_with_outcome is not None:
        task = asyncio.ensure_future(record_with_outcome(**kwargs))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        task.add_done_callback(_settle_or_release_hold)
        return

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
    tags: dict[str, str] | None = None,
    agent_principal_id: uuid.UUID | None = None,
) -> None:
    """Fire-and-forget usage record; forwards team_id when set (team-governance seam)."""
    extras: UsageRecordExtras = {}
    if team_id is not None:
        extras["team_id"] = team_id
    if request_id is not None:
        extras["request_id"] = request_id
    if tags:
        extras["tags"] = tags
    if agent_principal_id is not None:
        extras["agent_principal_id"] = agent_principal_id
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
    tags: dict[str, str] | None = None,
    agent_principal_id: uuid.UUID | None = None,
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
    if tags:
        extras["tags"] = tags
    if agent_principal_id is not None:
        extras["agent_principal_id"] = agent_principal_id
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
    tags: dict[str, str] | None = None,
    agent_principal_id: uuid.UUID | None = None,
) -> None:
    """Fire-and-forget usage record with optional guardrail raw markers.

    guardrail_blocked/blocked_by/pii_masked are forwarded via the typed
    UsageRecordExtras seam (declared-capability filtering in _dispatch_record).

    Additive extension (pricing-units TASK.md §3):
      pricing_unit / quantity are forwarded via UsageRecordExtras when set.
      Chat/embeddings callers pass nothing new — defaults None → per_token path.

    request_id (request-log-metering-fields TASK.md §3): correlation key forwarded
    via the SAME typed extras seam when set.

    tags (cost-attribution-tags TASK.md §3): validated X-Gateway-Tags dict forwarded
    via the SAME typed extras seam when non-empty — None/{} omits the extra entirely,
    and the recorder itself still stamps tags="{}" on the row (byte-identical, M3).
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
    if tags:
        extras["tags"] = tags
    if agent_principal_id is not None:
        extras["agent_principal_id"] = agent_principal_id
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
    tags: dict[str, str] | None = None,
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
        agent_principal_id=authz.agent_principal_id,
        usage_source="validation_retry",
        tags=tags,
    )
    # M5/M6/M7: exactly ONE bounded retry, the IDENTICAL routed call, same
    # unmodified body — no re-run of governance/budget/rate-limit (M6); a transport
    # failure raises the SAME path attempt 1 would (M7 — the breaker is never
    # bypassed "because it's just a retry").
    try:
        if model_router is not None:
            retry_status, retry_body, retry_served = await model_router.complete(
                body, upstream=upstream, tenant_id=authz.tenant_id
            )
        else:
            retry_status, retry_body = await upstream.complete(body)
            retry_served = model_id
    except AllDeploymentsSaturatedError as exc:
        raise RATE_LIMITED.exc(
            detail=f"all deployments for '{exc.alias}' are rate-limited",
            headers={"Retry-After": "60"},
        ) from exc
    except AllCandidatesOutOfRegionError as exc:
        # residency-policy TASK.md §3 M8: never billed, no usage record — the
        # attempt-1 usage was already fired above (M8 bounded-retry billing rule);
        # this retry leg itself produced no usage.
        raise RESIDENCY_NO_ELIGIBLE_REGION.exc(region=exc.region or "", model_id=exc.alias) from exc
    except UpstreamRateLimitedError as exc:
        _fire_record_with_raw(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=None,
            status=429,
            team_id=authz.team_id,
            agent_principal_id=authz.agent_principal_id,
            usage_source="validation_retry",
            tags=tags,
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
        agent_principal_id=authz.agent_principal_id,
        usage_source="validation_retry",
        tags=tags,
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


def _extract_content_by_choice(chunks: list[bytes]) -> dict[int, str]:
    """Assemble assistant text PER CHOICE INDEX from a completed SSE byte stream.

    Mirrors extract_content_from_sse's frame-parsing (`data: {...}` frames,
    `delta.content` / `message.content`) but keeps EACH choice's fragments
    separate, keyed by its `index` field (default 0 when absent — the
    single-choice case every provider adapter this gateway wraps uses).

    streaming-output-pii-mask (A3, HOLE 1 fix): needed because evaluate_post /
    _mask_pii_in_body masks each choice's content INDEPENDENTLY (complete()'s
    contract) — merging every choice into one string (as extract_content_from_sse
    does, by design, for capture/billing where choice identity doesn't matter)
    would corrupt an n>1 response: choice 0's masked text would be a blend of
    every choice's content and every other choice would render empty.

    Pure, total, never raises (malformed lines are skipped, matching
    extract_content_from_sse's tolerance).
    """
    text_stream = b"".join(chunks).decode("utf-8", errors="replace")
    by_choice: dict[int, list[str]] = {}
    for line in text_stream.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        payload = stripped[len("data:") :].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        choices = parsed.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            idx_raw = choice.get("index", 0)
            idx = idx_raw if isinstance(idx_raw, int) else 0
            content: Any = None
            delta = choice.get("delta")
            if isinstance(delta, dict):
                c = delta.get("content")
                if isinstance(c, str) and c:
                    content = c
            if content is None:
                message = choice.get("message")
                if isinstance(message, dict):
                    c = message.get("content")
                    if isinstance(c, str) and c:
                        content = c
            if content is not None:
                by_choice.setdefault(idx, []).append(content)
    return {idx: "".join(parts) for idx, parts in by_choice.items()}


def _rewrite_sse_content(chunks: list[bytes], new_content_by_choice: dict[int, str]) -> list[bytes]:
    """Rewrite a completed SSE byte stream so EACH choice's assembled delta content
    becomes its OWN entry in `new_content_by_choice`, WITHOUT disturbing any other
    frame (ids, roles, finish_reason, the usage frame, [DONE]) — used by
    streaming-output-pii-mask (HIGH remediation A3) to emit the evaluate_post-MASKED
    text instead of the raw upstream text.

    Post-call masking (regex substitution over each choice's assembled string) can
    change a choice's text length and can span that choice's original per-chunk
    delta boundaries (a provider may split a PII match, e.g. an email, across two
    chunks) — so this does NOT try to preserve the original per-chunk split. Instead,
    for each choice INDEX independently, the FIRST content-bearing field found for
    that index gets the FULL new content for that index, every subsequent
    content-bearing field for the SAME index is blanked to "" — tracked via a
    PER-INDEX `assigned` map (HOLE 1 fix: previously a single call-wide flag, which
    corrupted n>1 responses by merging every choice into choice 0's slot and blanking
    every other choice to empty). The concatenation invariant
    (`_extract_content_by_choice` sums all of a choice's own fragments) still holds
    per choice for downstream consumers. Choice indices with no entry in
    `new_content_by_choice` (e.g. a choice that carried no content at all) are left
    byte-for-byte unchanged. Every non-content frame is untouched.

    Total + pure: never raises. FAIL-CLOSED (audit-remediation Blocker 3): this rewrite
    only runs when post-call masking is active and content actually changed, so the
    stream is KNOWN to carry text that must be masked. It must therefore NEVER fall back
    to emitting the original raw (unmasked) bytes on a parse hiccup. A single unparseable
    `data:` frame is DROPPED (we cannot inspect it for PII, and each choice's masked text
    is already carried in that choice's first content frame); any other unexpected error
    reconstructs a minimal stream from the already-masked per-choice text — never `chunks`.
    """

    def _masked_fallback() -> list[bytes]:
        # Never emit raw chunks. Rebuild a minimal valid SSE carrying ONLY the
        # already-masked per-choice text + [DONE].
        frames = [
            "data: " + json.dumps({"choices": [{"index": idx, "delta": {"content": masked}}]})
            for idx, masked in sorted(new_content_by_choice.items())
        ]
        frames.append("data: [DONE]")
        return [("\n\n".join(frames) + "\n\n").encode("utf-8")]

    try:
        text_stream = b"".join(chunks).decode("utf-8", errors="replace")
        lines = text_stream.split("\n")
        assigned: dict[int, bool] = {}
        out_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped.startswith("data:"):
                out_lines.append(line)
                continue
            payload = stripped[len("data:") :].strip()
            if payload in ("[DONE]", ""):
                out_lines.append(line)
                continue
            try:
                parsed = json.loads(payload)
            except Exception:  # noqa: S112 -- deliberate fail-closed drop; the payload is
                # NOT logged on purpose: the unparseable frame may itself carry the PII we
                # are masking, so logging it would leak the very data we drop it to protect.
                continue
            if not isinstance(parsed, dict):
                # Not a content frame we can inspect → drop (fail-closed), don't emit raw.
                continue
            choices = parsed.get("choices")
            if not isinstance(choices, list):
                # A dict frame with no choices (usage/role/metadata) carries no assistant
                # content — safe to pass through verbatim.
                out_lines.append(line)
                continue
            frame_changed = False
            for choice in choices:
                if not isinstance(choice, dict):
                    continue
                idx_raw = choice.get("index", 0)
                idx = idx_raw if isinstance(idx_raw, int) else 0
                if idx not in new_content_by_choice:
                    continue
                for key in ("delta", "message"):
                    holder = choice.get(key)
                    if (
                        isinstance(holder, dict)
                        and isinstance(holder.get("content"), str)
                        and holder["content"]
                    ):
                        holder["content"] = (
                            new_content_by_choice[idx] if not assigned.get(idx) else ""
                        )
                        assigned[idx] = True
                        frame_changed = True
            out_lines.append("data: " + json.dumps(parsed) if frame_changed else line)
        return [("\n".join(out_lines)).encode("utf-8")]
    except Exception:
        # Any other unexpected failure → masked reconstruction, NEVER the raw stream.
        return _masked_fallback()


# post-call-mask-fail-closed (Issue 2): the redaction primitive (_POST_MASK_REDACTION /
# _redact_response_body) is imported at the top of the module from
# gateway.proxy.domain.post_call_redaction — a single source of truth shared with the
# masking evaluator, which now also fails CLOSED internally, so both the evaluator's own
# error path and these call-site guards withhold the output identically.


async def _apply_stream_post_mask(
    collected: list[bytes],
    guardrail_evaluator: Any,
    guardrail_configs: dict[str, Any],
) -> list[bytes]:
    """Buffer-mask a completed streaming response before its bytes reach the client.

    streaming-output-pii-mask (HIGH remediation A3): mirrors complete()'s Step 5.5
    post-call evaluate_post call, but for the streaming path — the defect this closes
    is that stream() never called evaluate_post at all, so a `pii_mask=mask` guardrail
    that masked non-streaming output left `stream:true` responses completely raw.

    Assembles each choice's text INDEPENDENTLY from the collected SSE frames
    (_extract_content_by_choice — HOLE 1 fix: NOT the all-choices-merged
    extract_content_from_sse, which would corrupt an n>1 response), builds ONE
    synthetic {"choices": [...]} envelope with one entry per choice index (position
    order = sorted index order), and runs it through evaluate_post EXACTLY ONCE — the
    same _mask_pii_in_body contract complete() already relies on masks each choice
    independently and preserves list order/length, so the returned choices are
    zipped back to their original indices by position. Only choices whose masked
    text actually differs trigger a rewrite (_rewrite_sse_content) — a per-choice
    diff, not an all-or-nothing one.

    Fail-CLOSED but NON-BLOCKING (Issue 2, Tin-approved policy inversion — matches the
    four non-streaming sites via _redact_response_body): an evaluator exception, or a
    shape mismatch (masked_choices length disagreeing with what was sent), means we
    cannot prove the streamed text PII-free, so each choice's content is REDACTED to
    `_POST_MASK_REDACTION` (via _rewrite_sse_content) rather than streaming the ORIGINAL
    unmasked bytes. The stream still completes normally (well-formed, terminated) — we
    withhold, never block. When there is no content, or masking made no change anywhere
    (no PII found in any choice), the original bytes are returned untouched
    (byte-identical passthrough).
    """
    original_by_choice = _extract_content_by_choice(collected)
    if not original_by_choice:
        return collected
    ordered_indices = sorted(original_by_choice)
    try:
        masked_body = await guardrail_evaluator.evaluate_post(
            {
                "choices": [
                    {"index": idx, "message": {"content": original_by_choice[idx]}}
                    for idx in ordered_indices
                ]
            },
            guardrail_configs,
        )
    except Exception as _exc:
        _log.warning(
            "guardrail evaluate_post raised in stream (fail-CLOSED, redacting content)",
            exc_info=_exc,
        )
        return _rewrite_sse_content(
            collected, {idx: _POST_MASK_REDACTION for idx in original_by_choice}
        )
    masked_choices = masked_body.get("choices") if isinstance(masked_body, dict) else None
    if not isinstance(masked_choices, list) or len(masked_choices) != len(ordered_indices):
        # Defensive fail-CLOSED: an evaluator that changes shape (drops/adds choices) is
        # untrusted output — never guess a mapping, and never stream the raw text; redact.
        return _rewrite_sse_content(
            collected, {idx: _POST_MASK_REDACTION for idx in original_by_choice}
        )
    new_content_by_choice: dict[int, str] = {}
    any_changed = False
    for idx, masked_choice in zip(ordered_indices, masked_choices, strict=True):
        original_text = original_by_choice[idx]
        text = original_text
        if isinstance(masked_choice, dict):
            message = masked_choice.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                text = message["content"]
        new_content_by_choice[idx] = text
        if text != original_text:
            any_changed = True
    if not any_changed:
        return collected
    return _rewrite_sse_content(collected, new_content_by_choice)


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


def _dispatch_guardrail_verdicts(
    session_factory: Any,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    team_id: uuid.UUID | None,
    policy_source: str,
    events: list[Any],
) -> None:
    """Schedule a fire-and-forget guardrail_verdict_events write (guardrail-analytics §3).

    No-op when session_factory is unwired (None — every existing frozen test fake that
    does not wire a DB-backed budget_guard stays byte-identical, since session_factory
    comes from CompletionUseCase._get_session_factory(), the SAME zero-new-dependency
    extraction the soft-budget-alert seam already uses) or events is empty. All
    own-session/timeout/swallow-all logic lives INSIDE record_guardrail_verdicts —
    this call site's only job is the None-gate + fire-and-forget dispatch, mirrors
    _dispatch_capture's idiom exactly (task reference kept to satisfy RUF006; exception
    suppressed so a verdict-write failure can never surface as "Task exception was
    never retrieved").
    """
    if session_factory is None or not events:
        return
    task = asyncio.ensure_future(
        record_guardrail_verdicts(
            session_factory,
            tenant_id=tenant_id,
            key_id=key_id,
            team_id=team_id,
            policy_source=policy_source,
            events=events,
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


def _check_plan_model_allowlist(authz: AuthzResult, model_id: str) -> None:
    """Raise ProblemError 403 ERR_PLAN_MODEL_NOT_ALLOWED (plan-enforcement TASK.md §3, M4,
    M9) if the model is outside the tenant's ASSIGNED PLAN's model_allowlist.

    Composes by INTERSECTION with the existing key-level _check_model_allowlist above —
    called immediately AFTER it, never instead of it. A no-op (M7 grandfathered) when the
    tenant has no assigned plan; a no-op when the plan itself imposes no restriction
    (null allowlist, same convention as the key-level allowlist).
    """
    if authz.plan_id is None:
        return  # M7 — unplanned, grandfathered-unlimited
    if authz.plan_model_allowlist is None:
        return  # plan imposes no restriction
    if model_id not in authz.plan_model_allowlist:
        raise PLAN_MODEL_NOT_ALLOWED.exc(
            extra={
                "upgrade_hint": {
                    "plan_id": str(authz.plan_id),
                    "plan_name": authz.plan_name,
                    "model": model_id,
                }
            }
        )


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
    *,
    platform_fallback: PlatformCredentialFallback | None = None,
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

    Platform-key-default (platform-key-default TASK.md §3, FROZEN @ v1): when
    ``platform_fallback`` is supplied AND enabled AND the requesting tenant has no own
    key, resolution falls back to the reserved ``kind='platform'`` tenant's own credential
    for ``provider``. The fallback is composed OUTSIDE ``resolver.resolve()`` — the
    requesting tenant is tried first (own key ALWAYS wins), and only its ``ProviderKeyMissing``
    triggers a SECOND, explicitly-separate resolve against the platform tenant id. This
    keeps ``TenantCredentialResolver.resolve()``'s fail-closed invariant intact for every
    other caller. ``platform_fallback=None`` (default) is byte-identical to the pre-fallback seam.

    Raises:
        ProblemError(402, ERR_PROVIDER_KEY_MISSING): the tenant has no enabled credential
            for the requested provider AND (fallback off, OR the platform tenant also has no
            key, OR the platform tenant row is unprovisioned). Fail-closed — no secret in the chain.
    """
    if resolver is None or provider not in BYOK_PROVIDERS:
        return None
    try:
        cred = await resolver.resolve(tenant_id, provider)
    except ProviderKeyMissing as pkm:
        # Own key missing. Attempt the platform-tenant fallback only when explicitly wired
        # AND the global kill-switch is ON; otherwise fail closed exactly as before (M6/R2).
        if platform_fallback is None or not platform_fallback.enabled:
            # §3 CONTRACT: ERR_PROVIDER_KEY_MISSING → HTTP 402 (no secret in the chain).
            raise ProblemError(
                402, pkm.code, "Provider key not configured for this tenant"
            ) from None
        return await _resolve_platform_fallback(
            resolver, tenant_id, provider, platform_fallback, pkm
        )
    # Pass tenant_id so the BYOK token caches (vertex_ad / azure_ad) scope cached bearer
    # tokens per (hydroa_tenant, identity) — the cross-tenant confused-deputy fix
    # (vertex-adapter M4 CR-2). The returned handle resets BOTH contextvars.
    return set_provider_credential(cred, tenant_id)


async def _resolve_platform_fallback(
    resolver: TenantCredentialResolver,
    tenant_id: Any,
    provider: str,
    platform_fallback: PlatformCredentialFallback,
    pkm: ProviderKeyMissing,
) -> object:
    """Serve the platform-tenant credential for a keyless requesting tenant (fallback ON).

    Called ONLY after the requesting tenant's own resolve raised ``ProviderKeyMissing`` and
    the kill-switch is ON. Fail-closed at every branch: the platform row missing (R3) or the
    platform tenant also lacking a key (R1) both surface the same 402 the tenant would have
    seen, never a fabricated id or empty credential.
    """
    plat_id = await platform_fallback.platform_tenant_id()
    if plat_id is None:
        # R3: the reserved kind='platform' row is unprovisioned (or a DB error/timeout).
        # Loud operator misconfig audit, tenant-facing 402 (Tin 2026-07-15 — never a 500-storm).
        await platform_fallback.audit_misconfig(tenant_id=tenant_id, provider=provider)
        raise ProblemError(402, pkm.code, "Provider key not configured for this tenant") from None
    try:
        # Resolve under the PLATFORM tenant id so the shared (tenant_id, provider)-keyed cache
        # stores this under (plat_id, provider) — never cross-cached under the requester (M4).
        plat_cred = await resolver.resolve(plat_id, provider)
    except ProviderKeyMissing:
        # R1: neither the requesting tenant nor the platform tenant has a key for this provider.
        raise ProblemError(402, pkm.code, "Provider key not configured for this tenant") from None
    # M5: audit the served fallback (requesting tenant + provider), fire-and-forget.
    await platform_fallback.audit_served(tenant_id=tenant_id, provider=provider)
    # M3 (SECURITY): the token-cache owner is the PLATFORM tenant id (the secret's real owner),
    # NOT the requesting tenant's — the confused-deputy boundary for Azure-AAD / Vertex caches.
    scope = set_provider_credential(plat_cred, plat_id)
    # M8: flag the request as platform-served (read by the usage recorder). Set LAST, once the
    # credential scope exists and nothing else can throw before we return the reset handle — so
    # the caller's finally ALWAYS resets the signal (no cross-request leak window).
    mark_platform_fallback()
    # fallback-usage-marker §3 (M4): publish the credential-source provenance for the usage record.
    # A SEPARATE, never-reset contextvar (not the credential-scoped served_via flag) so the marker
    # survives reset_provider_credential firing before the stream terminal record dispatches.
    _credential_source_ctx.set("platform")
    return scope


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
        team_id: Any = None,
        agent_principal_id: Any = None,
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
        credit_guard: CreditGuard = PassthroughCreditGuard(),  # noqa: B008
        hold_estimate_usd: Decimal = Decimal("0.50"),
        residency_lookup: ResidencyLookup | None = None,
        tier_capacity_guard: TierCapacityGuard = PassthroughTierCapacityGuard(),  # noqa: B008
        plan_rate_limit_resolver: PlanRateLimitResolver | None = None,
        platform_credential_fallback: PlatformCredentialFallback | None = None,
    ) -> None:
        self._authenticator = authenticator
        self._model_checker = model_checker
        self._budget_guard = budget_guard
        # residency-policy TASK.md §3 (FROZEN @ v2): None (default) ⇒ zero DB
        # interaction, byte-identical to pre-residency-policy behavior. Backs BOTH
        # the Tier 1 existence check (_enforce_governance) and the M7 cache-hit
        # re-validation below — the SAME instance FallbackModelRouter's Tier 2 filter
        # uses (wired separately onto the router by main.py).
        self._residency_lookup: ResidencyLookup | None = residency_lookup
        # credits-ledger TASK.md §3: PassthroughCreditGuard (default) ⇒ check_and_hold is a
        # no-op ⇒ byte-identical to today. When wired, the credit gate runs at the SAME
        # choke point as the budget ladder — see _enforce_governance for the insertion.
        self._credit_guard: CreditGuard = credit_guard
        self._hold_estimate_usd: Decimal = hold_estimate_usd
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
        # service-tiers TASK.md §3 (FROZEN @ v1): PassthroughTierCapacityGuard
        # (default) ⇒ check_and_hold always returns the requested tier unchanged,
        # release is a no-op ⇒ byte-identical to today. When wired, the tier gate
        # runs at the SAME choke point as the credit hold — see _enforce_governance.
        self._tier_capacity_guard: TierCapacityGuard = tier_capacity_guard
        # plan-rate-enforcement TASK.md §3 (FROZEN @ v1): None (default) ⇒ feature off ⇒
        # _enforce_rate_limits' tenant-window check is byte-identical to today (per-key
        # enforcement only, unchanged). When wired, composes ALONGSIDE the per-key RPM/
        # TPM windows already checked — see enforce_tenant_rate_limit's own docstring.
        self._plan_rate_limit_resolver: PlanRateLimitResolver | None = plan_rate_limit_resolver
        self._platform_credential_fallback: PlatformCredentialFallback | None = (
            platform_credential_fallback
        )

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

        plan-rate-enforcement TASK.md §3 (FROZEN @ v1, M3): AFTER the per-key windows
        below, ALSO check the tenant-scoped plan rpm/tpm ceiling — composes, never
        replaces (a tenant with no plan_rate_limit_resolver wired is byte-identical).
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

        # plan-rate-enforcement TASK.md §3 (M3): tenant-layer plan rpm/tpm ceiling —
        # composes ALONGSIDE (never replaces) the per-key windows just checked above.
        await enforce_tenant_rate_limit(limiter, self._plan_rate_limit_resolver, authz.tenant_id)

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

    async def _check_agent_principal_budget(self, authz: AuthzResult) -> None:
        """agent-identity-governance TASK.md §3 (FROZEN @ v1) — M4.

        Dual-copy governance: structural mirror of _check_team_budget and of
        governance.py::NonChatGovernance._check_agent_principal_budget (never
        staggered). An ADDITIONAL aggregate-spend dimension across every token
        attached to the principal — never replaces the existing per-token check.
        Fail-open: Redis unavailable → allow.
        Counter key: usage:spend:agent_principal:{agent_principal_id}:{YYYYMM}
        """
        budget = authz.agent_principal_budget_usd
        principal_id = authz.agent_principal_id
        if budget is None or principal_id is None:
            return

        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        spend_key = f"usage:spend:agent_principal:{principal_id}:{yyyymm}"

        try:
            redis = self._get_redis()
            if redis is None:
                return  # No Redis wired — fail open
            raw = await redis.get(spend_key)
        except Exception as exc:
            _log.warning(
                "agent_principal_budget_check: Redis GET failed (fail open)",
                exc_info=exc,
                extra={"agent_principal_id": str(principal_id), "spend_key": spend_key},
            )
            return

        spent = _parse_spend(raw)

        if spent >= budget:
            raise BUDGET_EXCEEDED.exc(
                detail=(
                    f"Agent principal spend {spent} >= budget {budget} for principal {principal_id}"
                )
            )

    async def _enforce_governance(
        self,
        authz: AuthzResult,
        model_id: str,
        budget_guard: BudgetGuard,
        model_groups: dict[str, list[str]] | None = None,
        *,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Enforce all governance rules in priority order (M8-M10, M12).

        Order: expiry → model allowlist → catalog+tenant check → per-key budget
               → team budget → tenant budget (fallback) → credit hold → RPM check
               → TPM check.
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

        credits-ledger TASK.md §3 (M1/M12): the credit gate composes IMMEDIATELY
        AFTER the budget ladder resolves (either branch), BEFORE RPM/TPM — a prior
        budget 402 short-circuits before the credit gate ever runs (most-restrictive-
        wins). request_id correlates this admission's HOLD with its later
        settle/release (see _dispatch_record); None (PassthroughCreditGuard callers /
        legacy call sites) ⇒ a fresh id is minted so check_and_hold always has one,
        but nothing will ever call settle/release against it (harmless — a
        PassthroughCreditGuard.check_and_hold is a no-op regardless).
        """
        # M8: Expiry check (fail-closed, DB-sourced — no infra failure risk)
        _check_expiry(authz)

        # M9: Model allowlist check (fail-closed, DB-sourced — no infra failure risk)
        # MUST run BEFORE the tenant-disabled check (§3 M7 enforcement order).
        _check_model_allowlist(authz, model_id)

        # plan-enforcement (M4): plan-level model-allowlist check — composes by
        # INTERSECTION with the key-level allowlist just above; runs immediately after it,
        # BEFORE the catalog check (same ordering rationale as the key-level check).
        _check_plan_model_allowlist(authz, model_id)

        # Catalog active + per-tenant override check (§3 step 3 — after allowlist).
        # Alias-aware when model_groups provided (§3 A4): validates all candidates.
        await self._check_model_catalog(model_id, authz.tenant_id, model_groups)

        # residency-policy TASK.md §3 (FROZEN @ v2) Tier 1: governance-layer existence
        # check, immediately after the catalog check (SAME insertion point as
        # governance.py::NonChatGovernance.authorize — dual-copy governance, never
        # staggered; ONE shared implementation function, see
        # gateway.proxy.application.residency). Alias-aware via the same model_groups
        # already threaded through the catalog check above.
        await check_residency_existence(
            self._residency_lookup, model_id, authz.tenant_id, model_groups
        )

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
            # agent-identity-governance TASK.md §3 (M4): agent-principal aggregate
            # budget — ADDITIONAL dimension, same insertion point as team budget.
            await self._check_agent_principal_budget(authz)
        else:
            if authz.soft_budget_usd is not None:
                # Soft-alert seam only (budget None → no per-key 402 possible)
                await self._check_per_key_budget(authz)
            # Team budget check (§3 step 5) — before the tenant guard (step 6);
            # fail-open on Redis errors (same guarantee as per-key budget check).
            await self._check_team_budget(authz)
            # agent-identity-governance TASK.md §3 (M4): runs regardless of
            # key-budget presence, mirrors team budget exactly.
            await self._check_agent_principal_budget(authz)
            # No hard per-key budget — tenant budget enforces (RedisBudgetGuard)
            await budget_guard.check(authz.tenant_id)

        # service-tiers TASK.md §3 (M3/M5/M7): admission-capacity HOLD, immediately
        # BEFORE the credit hold (capacity is a gateway-wide scarce-resource concern,
        # closer to the base back-pressure guard's job than to per-tenant
        # affordability, §1 M3). check_and_hold raises ERR_TIER_CAPACITY_EXHAUSTED
        # (503) ONLY when every applicable pool is genuinely full (R4) — a Redis/infra
        # failure fails OPEN into a degraded TierHold instead (M8a), never raises here.
        _tier_request_id = request_id if request_id is not None else uuid.uuid4()
        _tier_hold = await self._tier_capacity_guard.check_and_hold(
            authz.tenant_id, authz.tier, _tier_request_id
        )
        tier_served, tier_capacity_degraded = _tier_hold.tier_served, _tier_hold.degraded
        _tier_served_ctx.set((tier_served, tier_capacity_degraded))

        # credits-ledger TASK.md §3 (M1/M2/M3): admission-time HOLD, after the budget
        # ladder, before RPM/TPM. check_and_hold raises ERR_CREDITS_EXHAUSTED (402) INSIDE
        # a row-locked DB transaction — a rejection here writes NO hold row (R1).
        #
        # service-tiers TASK.md §3 (M3): wrapped so a credit-hold rejection ALSO
        # releases the just-placed tier hold — the tenant is never left holding a
        # capacity slot for a request that will never reach the provider.
        _credit_request_id = request_id if request_id is not None else uuid.uuid4()
        try:
            await self._credit_guard.check_and_hold(
                authz.tenant_id, _credit_request_id, self._hold_estimate_usd
            )
        except Exception:
            await self._tier_capacity_guard.release(authz.tenant_id, _tier_request_id)
            raise

        # M5 (edge: partial failure) — a LATER governance step (RPM/TPM) rejecting an
        # already-admitted request must fully reverse the hold: the tenant is never
        # charged for a request that never reached the provider. release() is a no-op
        # against PassthroughCreditGuard and best-effort (never raises) against a real
        # guard, so this never masks or replaces the original RATE_LIMITED error.
        #
        # service-tiers TASK.md §3 (M3): extended to release BOTH holds — a later
        # RPM/TPM rejection must not strand the tier-capacity slot either.
        try:
            # M10 (rate limits): RPM check → TPM check — after governance, before upstream
            await self._enforce_rate_limits(authz)
        except Exception:
            await self._credit_guard.release(authz.tenant_id, _credit_request_id)
            await self._tier_capacity_guard.release(authz.tenant_id, _tier_request_id)
            raise

        # service-tiers TASK.md §3 (M9): publish the tier hold for release once this
        # request's usage record is fully dispatched (mirrors _credit_hold_ctx.set()
        # in complete()/stream(), immediately after _enforce_governance returns) — set
        # HERE rather than in every caller because _enforce_governance is the ONE
        # choke point where the tier hold's own request_id is minted/known; consumed
        # by _settle_or_release_hold via _tier_hold_ctx (M9's own named symbol).
        _tier_hold_ctx.set((self._tier_capacity_guard, _tier_request_id))

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
            self._tenant_credential_resolver,
            tenant_id,
            _provider,
            platform_fallback=self._platform_credential_fallback,
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
                        body, upstream=upstream, tenant_id=authz.tenant_id
                    )
                else:
                    status, response_body = await upstream.complete(body)
                    served_model_id = model_id
            except Exception as exc:
                # residency-policy TASK.md §3: AllCandidatesOutOfRegionError is caught
                # here too (broad except, by design — this deferred fallback never
                # raises to its SSE generator) — degrades to the same generic
                # error-shaped body, never silently serves an out-of-region candidate.
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
                    agent_principal_id=authz.agent_principal_id,
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
                agent_principal_id=authz.agent_principal_id,
            )
            return response_body
        finally:
            if _cred_token is not None:
                reset_provider_credential(_cred_token)  # type: ignore[arg-type]

    async def _residency_cache_ok(
        self, cached_body: dict[str, Any], model_id: str, tenant_id: uuid.UUID
    ) -> bool:
        """M7 — re-validate a cache hit's stamped served region against the tenant's
        CURRENT residency pin, BEFORE the caller decides to treat it as a HIT.

        Non-destructive peek (uses .get, never .pop) — `_read_served_from_cache`'s own
        pop still runs later, unaffected, on an actual HIT. True = safe to serve
        (residency off, no pin, or region satisfies the pin). False = the caller must
        degrade this fetch to a MISS (never replay a stale cross-region body).
        """
        served = cached_body.get(_SERVED_STAMP)
        if not (isinstance(served, str) and served):
            served = cached_body.get("model")
        if not (isinstance(served, str) and served):
            served = model_id
        return await cache_hit_region_ok(self._residency_lookup, served, tenant_id)

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
        tags: dict[str, str] | None = None,
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
            if cached_body is not None and not await self._residency_cache_ok(
                cached_body, model_id, authz.tenant_id
            ):
                # residency-policy TASK.md §3 M7: the stamped served region no longer
                # satisfies the tenant's CURRENT pin — degrade to a MISS, never replay
                # the stale cross-region body. Falls through to semantic/vector/fresh
                # routing below, exactly like an ordinary exact-cache miss.
                cached_body = None
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
                # Step 5.5 on cache HIT: apply post-call PII mask if configured.
                # post-call-mask-fail-closed (Issue 2): masker failure REDACTS the body
                # (never leaks raw), but the 200 still returns (non-blocking).
                if guardrail_evaluator is not None and guardrail_configs:
                    if hasattr(guardrail_evaluator, "evaluate_post"):
                        try:
                            cached_body = await guardrail_evaluator.evaluate_post(
                                cached_body, guardrail_configs
                            )
                        except Exception as _exc:
                            _log.warning(
                                "guardrail evaluate_post raised on cache HIT "
                                "(fail-CLOSED, redacting content)",
                                exc_info=_exc,
                            )
                            cached_body = _redact_response_body(cached_body)
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
                    agent_principal_id=authz.agent_principal_id,
                    request_id=request_id,
                    tags=tags,
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
                if cached_usage is not None:
                    total_tokens = cached_usage.get("total_tokens")
                    if isinstance(total_tokens, int) and total_tokens > 0:
                        if authz.tpm_limit is not None:
                            _fire_record_tpm(
                                self._rate_limiter, key_id=authz.key_id, tokens=total_tokens
                            )
                        # plan-rate-enforcement TASK.md §3 (M4): tenant window sibling —
                        # self-gated on tenant_tpm_ctx, inert unless a tenant TPM ceiling
                        # was active for this request.
                        _fire_record_tpm_tenant(self._rate_limiter, tokens=total_tokens)
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
                        if sem_cached_body is not None and not await self._residency_cache_ok(
                            sem_cached_body, model_id, authz.tenant_id
                        ):
                            # M7: same stale-cross-region degrade-to-MISS as the exact
                            # branch above — falls through to the vector layer below.
                            sem_cached_body = None
                        if sem_cached_body is not None:
                            # SEMANTIC HIT
                            x_cache = "semantic_hit"
                            # cache-alias-billing (B6): read+pop served BEFORE masking.
                            _served_cached_sem = _read_served_from_cache(sem_cached_body, model_id)
                            if metrics_registry is not None:
                                try:
                                    metrics_registry.cache_events_total.labels(
                                        result="semantic_hit"
                                    ).inc()
                                except Exception:  # noqa: S110
                                    pass
                            # Apply post-call PII mask on semantic hit (same as exact hit).
                            # post-call-mask-fail-closed (Issue 2): redact on masker failure.
                            if guardrail_evaluator is not None and guardrail_configs:
                                if hasattr(guardrail_evaluator, "evaluate_post"):
                                    try:
                                        sem_cached_body = await guardrail_evaluator.evaluate_post(
                                            sem_cached_body, guardrail_configs
                                        )
                                    except Exception as _exc:
                                        _log.warning(
                                            "guardrail evaluate_post raised on semantic HIT"
                                            " (fail-CLOSED, redacting content)",
                                            exc_info=_exc,
                                        )
                                        sem_cached_body = _redact_response_body(sem_cached_body)
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
                                agent_principal_id=authz.agent_principal_id,
                                request_id=request_id,
                                tags=tags,
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
                            if sem_usage is not None:
                                total_tokens = sem_usage.get("total_tokens")
                                if isinstance(total_tokens, int) and total_tokens > 0:
                                    if authz.tpm_limit is not None:
                                        _fire_record_tpm(
                                            self._rate_limiter,
                                            key_id=authz.key_id,
                                            tokens=total_tokens,
                                        )
                                    # plan-rate-enforcement TASK.md §3 (M4): tenant window
                                    # sibling — self-gated on tenant_tpm_ctx.
                                    _fire_record_tpm_tenant(self._rate_limiter, tokens=total_tokens)
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
                    if vec_body is not None and not await self._residency_cache_ok(
                        vec_body, model_id, authz.tenant_id
                    ):
                        # M7: same stale-cross-region degrade-to-MISS — falls through
                        # to the ordinary MISS path (fresh, residency-filtered routing).
                        vec_body = None
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
                        # post-call-mask-fail-closed (Issue 2): redact on masker failure.
                        if guardrail_evaluator is not None and guardrail_configs:
                            if hasattr(guardrail_evaluator, "evaluate_post"):
                                try:
                                    vec_body = await guardrail_evaluator.evaluate_post(
                                        vec_body, guardrail_configs
                                    )
                                except Exception as _exc:
                                    _log.warning(
                                        "guardrail evaluate_post raised on vector HIT "
                                        "(fail-CLOSED, redacting content)",
                                        exc_info=_exc,
                                    )
                                    vec_body = _redact_response_body(vec_body)
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
                            agent_principal_id=authz.agent_principal_id,
                            request_id=request_id,
                            tags=tags,
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
                        if vec_usage is not None:
                            total_tokens = vec_usage.get("total_tokens")
                            if isinstance(total_tokens, int) and total_tokens > 0:
                                if authz.tpm_limit is not None:
                                    _fire_record_tpm(
                                        self._rate_limiter,
                                        key_id=authz.key_id,
                                        tokens=total_tokens,
                                    )
                                # plan-rate-enforcement TASK.md §3 (M4): tenant window
                                # sibling — self-gated on tenant_tpm_ctx.
                                _fire_record_tpm_tenant(self._rate_limiter, tokens=total_tokens)
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
        # claude-gateway-protocol-compat TASK.md §3 (M4): publish Claude Code
        # session/subagent attribution EARLY (pure header extraction, no side effect) so
        # it is set in this request's async context before any _dispatch_record fires.
        _publish_cc_attribution(request_headers)
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
            # cost-attribution-tags (TASK.md §3): parse+validate X-Gateway-Tags BEFORE
            # governance/upstream — a malformed header is 422 and NEVER billed (R1-R5).
            # Absent header -> {} with zero extra work (M3 byte-identical fast path).
            _tags = _parse_tags_header((request_headers or {}).get("x-gateway-tags"))
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
            await self._enforce_governance(
                authz, model_id, self._budget_guard, _model_groups, request_id=_request_id
            )
            # credits-ledger TASK.md §3: publish this request's (credit_guard, request_id)
            # so _dispatch_record's settle/release hook can find the open hold once
            # usage_recorder.record() reports the actual cost — see _credit_hold_ctx.
            _credit_hold_ctx.set((self._credit_guard, _request_id))

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
            # guardrail-analytics §3 (M1/M9): resolved ONCE per request, zero extra IO
            # (reuses the SAME getattr(budget_guard, "_session_factory", ...) extraction
            # the soft-budget-alert seam already uses). None ⇒ feature off ⇒ every
            # _dispatch_guardrail_verdicts call below no-ops, byte-identical to today.
            _gv_session_factory = self._get_session_factory()
            _gv_policy_source = getattr(authz, "policy_source", "none")
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
                        _dispatch_guardrail_verdicts(
                            _gv_session_factory,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            team_id=authz.team_id,
                            policy_source=_gv_policy_source,
                            events=[_make_error_event(guardrail_configs)],
                        )
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            agent_principal_id=authz.agent_principal_id,
                            guardrail_blocked=True,
                            blocked_by="error",
                            request_id=_request_id,
                            tags=_tags,
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
                        _dispatch_guardrail_verdicts(
                            _gv_session_factory,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            team_id=authz.team_id,
                            policy_source=_gv_policy_source,
                            events=[_make_error_event(guardrail_configs)],
                        )
                        # result is not set — fall through without masking/blocking
                        result = None
                finally:
                    reset_guardrail_tenant_id(_gtid_token)

                if result is not None:
                    _fire_guardrail_metrics(metrics_registry, result.events, guardrail_configs)
                    _dispatch_guardrail_verdicts(
                        _gv_session_factory,
                        tenant_id=authz.tenant_id,
                        key_id=authz.key_id,
                        team_id=authz.team_id,
                        policy_source=_gv_policy_source,
                        events=result.events,
                    )
                    if result.blocked:
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            agent_principal_id=authz.agent_principal_id,
                            guardrail_blocked=True,
                            blocked_by=result.blocked_by,
                            request_id=_request_id,
                            tags=_tags,
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
                    tags=_tags,
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
                        body, upstream=upstream, tenant_id=authz.tenant_id
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
            except AllCandidatesOutOfRegionError as exc:
                # residency-policy TASK.md §3 M4/M8: router-layer dial-constraint filter
                # emptied the candidate set. Fail-closed 403 — NEVER the generic
                # RATE_LIMITED/UPSTREAM_UNAVAILABLE path, never billed, no usage record
                # (this except fires BEFORE any _fire_record* call below).
                raise RESIDENCY_NO_ELIGIBLE_REGION.exc(
                    region=exc.region or "", model_id=exc.alias
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
                    agent_principal_id=authz.agent_principal_id,
                    tags=_tags,
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
                    agent_principal_id=authz.agent_principal_id,
                    tags=_tags,
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
                        tags=_tags,
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
            # post-call-mask-fail-closed (Issue 2): masker failure REDACTS the body (never
            # leaks raw output), but the 200 still returns (fail-CLOSED, non-blocking).
            if guardrail_evaluator is not None and guardrail_configs and status == 200:
                if hasattr(guardrail_evaluator, "evaluate_post"):
                    try:
                        response_body = await guardrail_evaluator.evaluate_post(
                            response_body, guardrail_configs
                        )
                    except Exception as _exc:
                        _log.warning(
                            "guardrail evaluate_post raised (fail-CLOSED, redacting response body)",
                            exc_info=_exc,
                        )
                        response_body = _redact_response_body(response_body)

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
                agent_principal_id=authz.agent_principal_id,
                pii_masked=_pii_masked,
                # output-schema-validation (M8): None (default) on every request that
                # never engaged the retry loop — "frame", unchanged. Set to
                # "validation_retry" ONLY for a retry leg that ended in a non-200
                # upstream pass-through (see the retry block above) — a validated
                # 200 success keeps the default "frame" too (M8's own rule).
                usage_source=_usage_source_final,
                request_id=_request_id,
                tags=_tags,
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
            if usage is not None:
                total_tokens = usage.get("total_tokens")
                if isinstance(total_tokens, int) and total_tokens > 0:
                    if authz.tpm_limit is not None:
                        _fire_record_tpm(
                            self._rate_limiter, key_id=authz.key_id, tokens=total_tokens
                        )
                    # plan-rate-enforcement TASK.md §3 (M4): tenant window sibling —
                    # self-gated on tenant_tpm_ctx.
                    _fire_record_tpm_tenant(self._rate_limiter, tokens=total_tokens)
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
        request_headers: dict[str, str] | None = None,
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

        request_headers (cost-attribution-tags TASK.md §3, additive — default None so
        every existing caller/test that omits it is unaffected, byte-identical M3):
        read for X-Gateway-Tags parity with complete() — the ONLY use today. router.py
        computes it once (before branching on stream vs non-stream) and passes it here.
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
            # cost-attribution-tags (TASK.md §3): parse+validate X-Gateway-Tags BEFORE
            # governance/upstream — a malformed header is 422 and NEVER billed (R1-R5).
            # Same parity rule as complete() (M2) — absent header -> {} (M3).
            _tags = _parse_tags_header((request_headers or {}).get("x-gateway-tags"))
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
                authz, model_id, self._budget_guard, _stream_model_groups, request_id=_request_id
            )
            # credits-ledger TASK.md §3: see complete()'s identical insertion for rationale.
            _credit_hold_ctx.set((self._credit_guard, _request_id))

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
            # guardrail-analytics §3 (M1/M9): same resolve-once seam as complete() above —
            # this is the NEW call site that closes the pre-existing streaming
            # metrics-coverage gap (§0): _fire_guardrail_metrics never fires for streaming,
            # but _dispatch_guardrail_verdicts below does, independent of that gap.
            _gv_session_factory = self._get_session_factory()
            _gv_policy_source = getattr(authz, "policy_source", "none")
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
                        _dispatch_guardrail_verdicts(
                            _gv_session_factory,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            team_id=authz.team_id,
                            policy_source=_gv_policy_source,
                            events=[_make_error_event(guardrail_configs)],
                        )
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            agent_principal_id=authz.agent_principal_id,
                            guardrail_blocked=True,
                            blocked_by="error",
                            request_id=_request_id,
                            tags=_tags,
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
                    _dispatch_guardrail_verdicts(
                        _gv_session_factory,
                        tenant_id=authz.tenant_id,
                        key_id=authz.key_id,
                        team_id=authz.team_id,
                        policy_source=_gv_policy_source,
                        events=[_make_error_event(guardrail_configs)],
                    )
                    stream_result = None
                finally:
                    reset_guardrail_tenant_id(_gtid_token)

                if stream_result is not None:
                    _dispatch_guardrail_verdicts(
                        _gv_session_factory,
                        tenant_id=authz.tenant_id,
                        key_id=authz.key_id,
                        team_id=authz.team_id,
                        policy_source=_gv_policy_source,
                        events=stream_result.events,
                    )
                    if stream_result.blocked:
                        _fire_record_with_raw(
                            usage_recorder,
                            tenant_id=authz.tenant_id,
                            key_id=authz.key_id,
                            model=model_id,
                            usage=None,
                            status=400,
                            team_id=authz.team_id,
                            agent_principal_id=authz.agent_principal_id,
                            guardrail_blocked=True,
                            blocked_by=stream_result.blocked_by,
                            request_id=_request_id,
                            tags=_tags,
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

                # streaming-output-pii-mask (HIGH remediation A3): the v4 "stream bodies
                # are not post-call inspected" limitation is CLOSED — _post_mask_active
                # below gates a buffer-then-mask pass in _wrapped() so a configured
                # pii_mask=mask guardrail (or any evaluate_post-capable evaluator) masks
                # streamed output exactly like complete() already masks non-streaming
                # output. No stale skip-log needed: the masking now actually happens.

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
                        body,
                        upstream=upstream,
                        on_served=_capture_served,
                        tenant_id=authz.tenant_id,
                    )
                elif model_router is not None:
                    # residency-policy TASK.md §3 Tier 2: stream() is a plain (non-async)
                    # function that dials upstream EAGERLY (frozen model-fallbacks F11
                    # contract) — it cannot itself await the residency DB lookup. Pre-
                    # compute the filtered candidate set HERE (async context) and check
                    # emptiness ourselves so the 403 carries the correct pinned region —
                    # never even calling stream() when the residency-filtered set is
                    # empty (stream() still defensively re-raises too, see its docstring).
                    _residency_result = await model_router.residency_candidates(
                        model_id, authz.tenant_id
                    )
                    _candidates_override: list[str] | None = None
                    if _residency_result is not None:
                        _pin, _candidates_override = _residency_result
                        if not _candidates_override:
                            raise RESIDENCY_NO_ELIGIBLE_REGION.exc(
                                region=_pin or "", model_id=model_id
                            )
                    gen = model_router.stream(
                        body,
                        upstream=upstream,
                        on_served=_capture_served,
                        candidates_override=_candidates_override,
                    )
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
            except AllCandidatesOutOfRegionError as exc:
                # residency-policy TASK.md §3 M4/M8 — raised by stream_resilient()'s own
                # Tier 2 filter (the sync stream() path is pre-checked above and never
                # reaches here in practice, but the same mapping applies defensively).
                # Status not yet committed — fail-closed 403, never billed, no usage
                # record, never the generic 502/429 path.
                raise RESIDENCY_NO_ELIGIBLE_REGION.exc(
                    region=exc.region or "", model_id=exc.alias
                ) from exc
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
                    agent_principal_id=authz.agent_principal_id,
                    tags=_tags,
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
                    agent_principal_id=authz.agent_principal_id,
                    tags=_tags,
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
            agent_principal_id = authz.agent_principal_id
            tpm_limit = authz.tpm_limit
            rate_limiter = self._rate_limiter
            # payload-capture-store §3: captured before _wrapped() (mirrors the locals above).
            _capture_enabled = authz.payload_capture_enabled
            _payload_capture = self._payload_capture
            # streaming-output-pii-mask (HIGH remediation A3): identical gate to
            # complete()'s Step 5.5 (hasattr + guardrail_configs truthy) — when False,
            # _wrapped() below yields chunks live exactly as before this fix (no
            # buffering overhead added for the common no-guardrail case). When True,
            # content is buffered and masked before any content bytes reach the client.
            _post_mask_active = bool(
                guardrail_evaluator is not None
                and guardrail_configs
                and hasattr(guardrail_evaluator, "evaluate_post")
            )

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
                        # streaming-output-pii-mask (A3): when a post-call mask guardrail
                        # is configured, TTFB is intentionally deferred — nothing is sent
                        # until the full text can be assembled + masked (CORRECTNESS wins
                        # over TTFB here). Byte-identical (still unpaced TTFB) otherwise.
                        if not _post_mask_active:
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
                                agent_principal_id=agent_principal_id,
                                pii_masked=_stream_pii_masked,
                                usage_source=_bw_source,
                                request_id=_request_id,
                                tags=_tags,
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
                            # streaming-output-pii-mask (A3): masking mode withholds ALL
                            # content bytes until now — flush the buffered (masked)
                            # prefix before the truncation error frame so the client
                            # still receives the (masked) content it was paying for.
                            if _post_mask_active:
                                for _flush_chunk in await _apply_stream_post_mask(
                                    collected, guardrail_evaluator, guardrail_configs
                                ):
                                    yield _flush_chunk
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
                        if not _post_mask_active:
                            yield chunk
                    # streaming-output-pii-mask (A3): the upstream generator drained
                    # normally (no bandwidth-shed, no mid-stream upstream error) — mask
                    # the fully-assembled content now and flush it in one shot. No-op
                    # (returns `collected` unchanged) when masking is inactive, when
                    # there's no content, or when evaluate_post found nothing to mask.
                    if _post_mask_active:
                        for _flush_chunk in await _apply_stream_post_mask(
                            collected, guardrail_evaluator, guardrail_configs
                        ):
                            yield _flush_chunk
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
                        agent_principal_id=agent_principal_id,
                        tags=_tags,
                    )
                    # stream-upstream-error-frame (v35): emit a parseable error chunk so
                    # a [DONE]-waiting agent loop (e.g. Helios) never hangs on truncation.
                    _code = (
                        "ERR_UPSTREAM_RATE_LIMITED"
                        if isinstance(exc, UpstreamRateLimitedError)
                        else "ERR_UPSTREAM_UNAVAILABLE"
                    )
                    # streaming-output-pii-mask (A3): same flush-before-error-frame
                    # reasoning as the bandwidth-shed branch above — masking mode has
                    # withheld all content bytes so far.
                    if _post_mask_active:
                        for _flush_chunk in await _apply_stream_post_mask(
                            collected, guardrail_evaluator, guardrail_configs
                        ):
                            yield _flush_chunk
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
                        agent_principal_id=agent_principal_id,
                        pii_masked=_stream_pii_masked,
                        usage_source=disconnect_source,
                        provider_generation_id=disconnect_gen_id,
                        disconnect_estimate=disconnect_estimate,
                        request_id=_request_id,
                        tags=_tags,
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
                                    # agent-identity-governance defect fix: thread the
                                    # SAME team_id/agent_principal_id already stamped on
                                    # the disconnect anchor row a few lines above, so the
                                    # inline recovery's correction reaches the per-team/
                                    # per-agent-principal spend counters too — no DB
                                    # round trip needed here (unlike the sweep backstop,
                                    # which reads it back off the anchor row).
                                    team_id=team_id,
                                    agent_principal_id=agent_principal_id,
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
                except Exception as _unexpected_exc:
                    # streaming-output-pii-mask (A3, HOLE 2 fix): a catch-all for any
                    # exception type OTHER than the two upstream error types explicitly
                    # handled above (BandwidthExhaustedError is caught inline in the loop,
                    # not here). `except Exception` never catches GeneratorExit /
                    # CancelledError — those are BaseException subclasses already handled
                    # by the clause above and must keep re-raising WITHOUT a flush attempt
                    # (the client is already gone, nothing to flush TO).
                    #
                    # Before this fix: when _post_mask_active withheld content (buffering,
                    # never yielding live), an unexpected adapter/provider exception type
                    # propagated straight out of this generator with `collected` NEVER
                    # flushed — a SILENT TOTAL LOSS of already-generated content the client
                    # would have received live in the non-masking (pre-fix) path. Flush the
                    # masked buffered prefix (best-effort — `contextlib.suppress` so a
                    # SECONDARY failure during the flush itself can never mask the
                    # ORIGINAL exception being re-raised) THEN re-raise unchanged. When
                    # masking is inactive this is a no-op (nothing was withheld — content
                    # was already yielded live as it arrived) — byte-identical to today
                    # for the common no-guardrail case.
                    if _post_mask_active:
                        with contextlib.suppress(BaseException):
                            for _flush_chunk in await _apply_stream_post_mask(
                                collected, guardrail_evaluator, guardrail_configs
                            ):
                                yield _flush_chunk
                    _log.warning(
                        "stream_unexpected_exception_flushed_buffered_prefix",
                        exc_info=_unexpected_exc,
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
                    agent_principal_id=agent_principal_id,
                    pii_masked=_stream_pii_masked,
                    usage_source=usage_source,
                    request_id=_request_id,
                    tags=_tags,
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
                if isinstance(extracted_usage, dict):
                    total_tokens = extracted_usage.get("total_tokens")
                    if total_tokens and isinstance(total_tokens, int) and total_tokens > 0:
                        if tpm_limit is not None:
                            _fire_record_tpm(rate_limiter, key_id=key_id, tokens=total_tokens)
                        # plan-rate-enforcement TASK.md §3 (M4): tenant window sibling —
                        # self-gated on tenant_tpm_ctx.
                        _fire_record_tpm_tenant(rate_limiter, tokens=total_tokens)
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
