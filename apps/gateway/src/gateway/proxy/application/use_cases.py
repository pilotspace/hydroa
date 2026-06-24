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
    BUDGET_EXCEEDED,
    GUARDRAIL_BLOCKED,
    MODEL_DISABLED,
    MODEL_NOT_ALLOWED,
    MODEL_UNKNOWN,
    PAYLOAD_MESSAGES_REQUIRED,
    PAYLOAD_MODEL_REQUIRED,
    RATE_LIMITED,
    UPSTREAM_RATE_LIMITED,
    UPSTREAM_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
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
from gateway.proxy.domain.ports import (
    CompletionUpstream,
    GuardrailEvaluator,
    KeyAuthenticator,
    ModelAccess,
    ModelChecker,
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
from gateway.rate_limits.application.passthrough import PassthroughRateLimiter
from gateway.rate_limits.domain.errors import RateLimitExceededError
from gateway.rate_limits.domain.ports import RateLimiter
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
) -> None:
    """Fire-and-forget usage record; forwards team_id when set (team-governance seam)."""
    extras: UsageRecordExtras = {}
    if team_id is not None:
        extras["team_id"] = team_id
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
) -> None:
    """Fire-and-forget usage record for a cache hit (cached=true, cost_usd=0).

    Cost is 0 because the recorder's INCRBYFLOAT guard only runs when
    cost_usd > 0 — no spend counter increment.
    """
    extras: UsageRecordExtras = {"cached": True}
    if team_id is not None:
        extras["team_id"] = team_id
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
) -> None:
    """Fire-and-forget usage record with optional guardrail raw markers.

    guardrail_blocked/blocked_by/pii_masked are forwarded via the typed
    UsageRecordExtras seam (declared-capability filtering in _dispatch_record).

    Additive extension (pricing-units TASK.md §3):
      pricing_unit / quantity are forwarded via UsageRecordExtras when set.
      Chat/embeddings callers pass nothing new — defaults None → per_token path.
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
    _dispatch_record(
        usage_recorder,
        tenant_id=tenant_id,
        key_id=key_id,
        model=model,
        usage=usage,
        status=status,
        extras=extras,
    )


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

    async def _validate_payload(
        self,
        body: dict[str, Any],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Validate model and messages fields (format only — no catalog check here).

        Returns (model_id, messages).
        Raises ProblemError 422 on validation failure.

        NOTE: The catalog active check (ERR_MODEL_UNKNOWN) and tenant-disabled check
        (ERR_MODEL_DISABLED) are performed in _enforce_governance, AFTER the key-level
        allowlist check — enforcing §3 M7 order:
          1. model_id format validation  [here]
          2. key-level allowlist check   [_enforce_governance step 1]
          3. catalog+tenant check        [_enforce_governance step 2]
        """
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise PAYLOAD_MODEL_REQUIRED.exc()

        messages = body.get("messages")
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            raise PAYLOAD_MESSAGES_REQUIRED.exc()

        return model_id, messages

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
    ) -> tuple[int, dict[str, Any], str | None]:
        """Handle a non-streaming completion.

        Returns (status_code, json_body, x_cache_value).
        x_cache_value is "hit", "miss", "bypass", or None (cache disabled).
        On upstream 4xx: pass-through verbatim.
        On upstream 5xx / circuit open: raise ProblemError 502.
        """
        _start_ns = time.time_ns()
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
            # Validate payload fields (format only — catalog check is in _enforce_governance).
            model_id, _ = await self._validate_payload(body)
            _model_id = model_id
            # Enforce governance: expiry → allowlist → catalog+tenant check → budget → rate limits
            # Governance ALWAYS runs before cache lookup (contract §3 enforcement order).
            # Pass model_groups from model_router for alias-aware catalog check (§3 A4).
            _model_groups = model_router.model_groups if model_router is not None else None
            await self._enforce_governance(authz, model_id, self._budget_guard, _model_groups)

            # Credential resolution (credential-resolution-seam §3): resolve the per-tenant
            # provider credential and place it in the request-scoped contextvar so Bearer
            # adapters can read it in _auth_headers() without a signature change.
            _cred_token = await self._resolve_credential(authz.tenant_id, model_id)

            # --- Step 4: Pre-call guardrails (after governance, before cache lookup) ---
            guardrail_evaluator = self._guardrail_evaluator
            guardrail_configs = getattr(authz, "guardrail_configs", {}) or {}
            if guardrail_evaluator is not None and guardrail_configs:
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

            if cache is not None and cache_enabled:
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
                        _cached = True
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
                            model=model_id,
                            usage=cached_usage,
                            team_id=authz.team_id,
                        )
                        # TPM post-accounting uses cached token counts
                        if authz.tpm_limit is not None and cached_usage is not None:
                            total_tokens = cached_usage.get("total_tokens")
                            if isinstance(total_tokens, int) and total_tokens > 0:
                                _fire_record_tpm(
                                    self._rate_limiter, key_id=authz.key_id, tokens=total_tokens
                                )
                        _status_code = 200
                        return 200, cached_body, x_cache
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
                                    _cached = True
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
                                        model=model_id,
                                        usage=sem_usage,
                                        team_id=authz.team_id,
                                    )
                                    if authz.tpm_limit is not None and sem_usage is not None:
                                        total_tokens = sem_usage.get("total_tokens")
                                        if isinstance(total_tokens, int) and total_tokens > 0:
                                            _fire_record_tpm(
                                                self._rate_limiter,
                                                key_id=authz.key_id,
                                                tokens=total_tokens,
                                            )
                                    _status_code = 200
                                    return 200, sem_cached_body, x_cache
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
                                _cached = True
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
                                    model=model_id,
                                    usage=vec_usage,
                                    team_id=authz.team_id,
                                )
                                if authz.tpm_limit is not None and vec_usage is not None:
                                    total_tokens = vec_usage.get("total_tokens")
                                    if isinstance(total_tokens, int) and total_tokens > 0:
                                        _fire_record_tpm(
                                            self._rate_limiter,
                                            key_id=authz.key_id,
                                            tokens=total_tokens,
                                        )
                                _status_code = 200
                                return 200, vec_body, x_cache
                        # MISS (exact miss + semantic miss/disabled + vector miss/off)
                        x_cache = "miss"
                        if metrics_registry is not None:
                            try:
                                metrics_registry.cache_events_total.labels(result="miss").inc()
                            except Exception:  # noqa: S110
                                pass
                else:
                    # BYPASS: Cache-Control: no-cache
                    x_cache = "bypass"
                    if metrics_registry is not None:
                        try:
                            metrics_registry.cache_events_total.labels(result="bypass").inc()
                        except Exception:  # noqa: S110
                            pass

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

            # Store UNMASKED upstream body in cache on 200 (fire-and-forget).
            # Must run BEFORE post-call guardrail masking so the stored body is unmasked.
            if cache is not None and cache_enabled and status == 200:
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
                    _fire_cache_set(cache, ck, response_body, cache_ttl_seconds)

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
                            cache, _sem_key, _own_ck, response_body, cache_ttl_seconds
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
            )
            # M8: Post-stream TPM accounting (non-blocking, swallows Redis errors)
            if authz.tpm_limit is not None and usage is not None:
                total_tokens = usage.get("total_tokens")
                if isinstance(total_tokens, int) and total_tokens > 0:
                    _fire_record_tpm(self._rate_limiter, key_id=authz.key_id, tokens=total_tokens)
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
            # Validate payload fields (format only — catalog check is in _enforce_governance).
            model_id, _ = await self._validate_payload(body)
            _stream_model_id = model_id
            # Enforce governance: expiry → allowlist → catalog+tenant check → budget → rate limits
            # Pass model_groups from model_router for alias-aware catalog check (§3 A4).
            _stream_model_groups = model_router.model_groups if model_router is not None else None
            await self._enforce_governance(
                authz, model_id, self._budget_guard, _stream_model_groups
            )

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
                        )
                        _stream_error_status = 400
                        _stream_guardrail_blocked = True
                        raise GUARDRAIL_BLOCKED.exc() from None
                    stream_result = None

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
            try:
                # Route stream through model_router when wired. With stream resilience enabled
                # (streaming-resilience v19), peek the first chunk via stream_resilient so a
                # PRE-first-byte failure falls over to the next candidate; a total pre-byte
                # failure raises here → the SAME except → 502 BEFORE StreamingResponse commits.
                # Otherwise the byte-identical OLD path: resolve to the first candidate only
                # (§3 STREAMING BOUNDARY); no fallback on stream failure.
                if model_router is not None and self._stream_resilience_enabled:
                    first_chunk, gen = await model_router.stream_resilient(body, upstream=upstream)
                elif model_router is not None:
                    gen = model_router.stream(body, upstream=upstream)
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

            tenant_id = authz.tenant_id
            key_id = authz.key_id
            team_id = authz.team_id
            tpm_limit = authz.tpm_limit
            rate_limiter = self._rate_limiter

            async def _wrapped() -> AsyncIterator[bytes]:
                collected: list[bytes] = []
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
                        yield first_chunk
                    async for chunk in gen:
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
                        model=model_id,
                        usage=disconnect_usage,
                        status=200,
                        team_id=team_id,
                        pii_masked=_stream_pii_masked,
                        usage_source=disconnect_source,
                        provider_generation_id=disconnect_gen_id,
                        disconnect_estimate=disconnect_estimate,
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
                    if self._cost_recovery is not None and recoverable:
                        with contextlib.suppress(BaseException):
                            _recovery_task = asyncio.ensure_future(
                                self._cost_recovery.recover(
                                    tenant_id=tenant_id,
                                    key_id=key_id,
                                    model=model_id,
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
                    model=model_id,
                    usage=extracted_usage,
                    status=200,
                    team_id=team_id,
                    pii_masked=_stream_pii_masked,
                    usage_source=usage_source,
                )
                # M8: Post-stream TPM accounting (fire-and-forget, never blocks response)
                if tpm_limit is not None and isinstance(extracted_usage, dict):
                    total_tokens = extracted_usage.get("total_tokens")
                    if total_tokens and isinstance(total_tokens, int) and total_tokens > 0:
                        _fire_record_tpm(rate_limiter, key_id=key_id, tokens=total_tokens)
                # Successful stream span — emitted here after the last chunk (§3 pinned).
                # _authz and _emitter are captured from the enclosing scope.
                if _authz is not None and _emitter is not None:  # pyright: ignore[reportUnnecessaryComparison]  — defensive None check; _authz captured from enclosing scope
                    _emit_span_fire_forget(
                        _emitter,
                        _authz,
                        model_id,
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
