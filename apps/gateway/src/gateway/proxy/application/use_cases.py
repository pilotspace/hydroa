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
import datetime
import logging
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog.contextvars

from gateway.budgets.domain.ports import BudgetGuard, PassthroughBudgetGuard
from gateway.core.errors import ProblemError
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import (
    CompletionUpstream,
    KeyAuthenticator,
    ModelChecker,
    UsageRecorder,
)
from gateway.rate_limits.application.passthrough import PassthroughRateLimiter
from gateway.rate_limits.domain.errors import RateLimitExceededError
from gateway.rate_limits.domain.ports import RateLimiter
from gateway.usage.domain.extractor import extract_usage_from_sse

_log = logging.getLogger(__name__)
_ZERO = Decimal("0")


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


def _fire_record(
    usage_recorder: UsageRecorder,
    *,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    usage: dict[str, Any] | None,
    status: int,
) -> None:
    """Schedule a fire-and-forget usage record.

    Stores the Task reference to satisfy RUF006 (avoids garbage-collected task).
    Failures in the recorder are intentionally ignored — recording must never
    affect the caller's response.
    """
    task = asyncio.ensure_future(
        usage_recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model=model,
            usage=usage,
            status=status,
        )
    )
    # Suppress unhandled-exception noise if recorder raises unexpectedly.
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
        raise ProblemError(401, "ERR_AUTH_KEY_EXPIRED", "API key has expired")


def _check_model_allowlist(authz: AuthzResult, model_id: str) -> None:
    """Raise ProblemError 403 ERR_MODEL_NOT_ALLOWED if model is not in allowlist (M9).

    null allowlist = all models allowed.
    empty [] = no models allowed (security-strict, A3).
    """
    if authz.model_allowlist is None:
        # null = unlimited — all models allowed
        return
    if model_id not in authz.model_allowlist:
        raise ProblemError(403, "ERR_MODEL_NOT_ALLOWED", "Model not permitted for this API key")


def _parse_spend(raw: bytes | str | None) -> Decimal:
    """Parse Redis spend counter value; returns 0 on any failure (fail-open)."""
    if raw is None:
        return _ZERO
    try:
        return Decimal(raw.decode() if isinstance(raw, bytes) else str(raw))
    except (InvalidOperation, AttributeError):
        return _ZERO


class CompletionUseCase:
    """Orchestrate a single /v1/chat/completions request."""

    def __init__(
        self,
        authenticator: KeyAuthenticator,
        model_checker: ModelChecker,
        budget_guard: BudgetGuard = PassthroughBudgetGuard(),  # noqa: B008
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._authenticator = authenticator
        self._model_checker = model_checker
        self._budget_guard = budget_guard
        self._rate_limiter: RateLimiter = (
            rate_limiter if rate_limiter is not None else PassthroughRateLimiter()
        )

    async def _authenticate(self, raw_key: str | None) -> AuthzResult:
        """Extract bearer key and return AuthzResult with governance fields.

        Raises ProblemError 401 on any authentication failure.
        Returns the full AuthzResult so callers can enforce governance fields.
        """
        if not raw_key:
            raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Missing or invalid API key")
        try:
            result = await self._authenticator.authenticate(raw_key)
        except InvalidApiKeyError:
            raise ProblemError(401, "ERR_AUTH_INVALID_KEY", "Missing or invalid API key") from None
        # Bind tenant_id to the structlog context so the access log line (emitted
        # by RequestIdMiddleware after the response) carries it for authenticated paths.
        # On pre-auth 401 exits above, this line is never reached — field stays absent.
        structlog.contextvars.bind_contextvars(tenant_id=str(result.tenant_id))
        return result

    async def _validate_payload(self, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """Validate model and messages fields.

        Returns (model_id, messages).
        Raises ProblemError 422/400 on validation failure.
        """
        model_id = body.get("model")
        if not model_id or not isinstance(model_id, str) or not model_id.strip():
            raise ProblemError(
                422, "ERR_PAYLOAD_INVALID", "Field 'model' is required and non-empty"
            )

        messages = body.get("messages")
        if not messages or not isinstance(messages, list) or len(messages) == 0:
            raise ProblemError(
                422,
                "ERR_PAYLOAD_INVALID",
                "Field 'messages' must be a non-empty list",
            )

        is_active = await self._model_checker.is_active(model_id)
        if not is_active:
            raise ProblemError(400, "ERR_MODEL_UNKNOWN", f"Model '{model_id}' is not available")

        return model_id, messages

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
                raise ProblemError(
                    429,
                    "ERR_RATE_LIMITED",
                    "Rate limit exceeded",
                    detail=f"RPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

        # M6: TPM pre-flight check
        if authz.tpm_limit is not None:
            try:
                await limiter.check_tpm(authz.key_id, authz.tpm_limit)
            except RateLimitExceededError as exc:
                raise ProblemError(
                    429,
                    "ERR_RATE_LIMITED",
                    "Rate limit exceeded",
                    detail=f"TPM limit {exc.limit} exceeded for key {exc.key_id}",
                    headers={"Retry-After": str(exc.retry_after_s)},
                ) from None

    async def _enforce_governance(
        self,
        authz: AuthzResult,
        model_id: str,
        budget_guard: BudgetGuard,
    ) -> None:
        """Enforce all governance rules in priority order (M8-M10, M12).

        Order: expiry → model allowlist → per-key budget → tenant budget (fallback)
               → RPM check → TPM check.
        All governance data comes from AuthzResult — zero extra DB queries.

        Per-key budget: fail-open on Redis failure (advisory counter, A2/M13).
        Soft budget: no blocking — the seam is exposed via _compute_soft_exceeded().
        Rate limits: fail-open on Redis failure (M14); RPM before TPM (M7).
        """
        # M8: Expiry check (fail-closed, DB-sourced — no infra failure risk)
        _check_expiry(authz)

        # M9: Model allowlist check (fail-closed, DB-sourced — no infra failure risk)
        _check_model_allowlist(authz, model_id)

        # M10: Most-specific-wins budget enforcement
        if authz.monthly_budget_usd is not None:
            # Per-key budget is set — use it (key budget wins over tenant budget)
            await self._check_per_key_budget(authz)
        else:
            # No per-key budget — fall back to tenant budget (existing RedisBudgetGuard)
            await budget_guard.check(authz.tenant_id)

        # M10 (rate limits): RPM check → TPM check — after governance, before upstream
        await self._enforce_rate_limits(authz)

    async def _check_per_key_budget(self, authz: AuthzResult) -> None:
        """Check per-key Redis spend counter against key's monthly_budget_usd.

        Fail-open: Redis unavailable → allow (advisory counter pattern, M13).
        Counter key: usage:spend:key:{key_id}:{YYYYMM}
        """
        budget = authz.monthly_budget_usd
        if budget is None:
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

        # Soft budget seam (M11): compute but do not block
        # The soft_budget_exceeded boolean is available here for health-alerting tasks.
        # TODO(spend-windows): consume soft_budget_exceeded from request context here.
        if authz.soft_budget_usd is not None:
            _soft_exceeded = spent >= authz.soft_budget_usd

        if spent >= budget:
            raise ProblemError(
                402,
                "ERR_BUDGET_EXCEEDED",
                "Monthly budget exceeded",
                detail=f"Per-key spend {spent} >= budget {budget} for key {authz.key_id}",
            )

    def _get_redis(self) -> Any:
        """Return the Redis client if available via the budget guard.

        Attempts to extract redis from RedisBudgetGuard; returns None if unavailable.
        This avoids adding a direct Redis dependency to CompletionUseCase.
        """
        guard = self._budget_guard
        return getattr(guard, "_redis", None)

    async def complete(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
    ) -> tuple[int, dict[str, Any]]:
        """Handle a non-streaming completion.

        Returns (status_code, json_body).
        On upstream 4xx: pass-through verbatim.
        On upstream 5xx / circuit open: raise ProblemError 502.
        """
        authz = await self._authenticate(raw_key)
        # Validate payload first to get model_id for allowlist check
        model_id, _ = await self._validate_payload(body)
        # Enforce governance (expiry, allowlist, per-key budget, tenant budget)
        await self._enforce_governance(authz, model_id, self._budget_guard)

        try:
            status, response_body = await upstream.complete(body)
        except (UpstreamUnavailableError, CircuitOpenError):
            # Circuit-breaker proxy has already counted the failure.
            _fire_record(
                usage_recorder,
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=model_id,
                usage=None,
                status=502,
            )
            raise ProblemError(
                502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream service unavailable"
            ) from None

        # Record successful or upstream 4xx completion
        usage_raw = response_body.get("usage")
        usage: dict[str, Any] | None = usage_raw if isinstance(usage_raw, dict) else None
        _fire_record(
            usage_recorder,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=model_id,
            usage=usage,
            status=status,
        )
        # M8: Post-stream TPM accounting (non-blocking, swallows Redis errors)
        if authz.tpm_limit is not None and usage is not None:
            total_tokens = usage.get("total_tokens")
            if isinstance(total_tokens, int) and total_tokens > 0:
                _fire_record_tpm(self._rate_limiter, key_id=authz.key_id, tokens=total_tokens)
        return status, response_body

    async def stream(
        self,
        *,
        raw_key: str | None,
        body: dict[str, Any],
        upstream: CompletionUpstream,
        usage_recorder: UsageRecorder,
    ) -> AsyncIterator[bytes]:
        """Handle a streaming completion.

        Authenticates and validates before yielding any bytes.
        Returns an async generator of raw SSE byte chunks.
        On upstream error: raises ProblemError 502.
        """
        authz = await self._authenticate(raw_key)
        model_id, _ = await self._validate_payload(body)
        await self._enforce_governance(authz, model_id, self._budget_guard)

        try:
            gen = upstream.stream(body)
        except (UpstreamUnavailableError, CircuitOpenError):
            _fire_record(
                usage_recorder,
                tenant_id=authz.tenant_id,
                key_id=authz.key_id,
                model=model_id,
                usage=None,
                status=502,
            )
            raise ProblemError(
                502, "ERR_UPSTREAM_UNAVAILABLE", "Upstream service unavailable"
            ) from None

        tenant_id = authz.tenant_id
        key_id = authz.key_id
        tpm_limit = authz.tpm_limit
        rate_limiter = self._rate_limiter

        async def _wrapped() -> AsyncIterator[bytes]:
            collected: list[bytes] = []
            try:
                async for chunk in gen:
                    collected.append(chunk)
                    yield chunk
            except (UpstreamUnavailableError, CircuitOpenError):
                # Can't change status code mid-stream; record and stop
                _fire_record(
                    usage_recorder,
                    tenant_id=tenant_id,
                    key_id=key_id,
                    model=model_id,
                    usage=None,
                    status=502,
                )
                return
            # Tee: extract usage from collected SSE chunks after stream completes
            extracted_usage = extract_usage_from_sse(collected)
            _fire_record(
                usage_recorder,
                tenant_id=tenant_id,
                key_id=key_id,
                model=model_id,
                usage=extracted_usage,
                status=200,
            )
            # M8: Post-stream TPM accounting (fire-and-forget, never blocks response)
            if tpm_limit is not None and isinstance(extracted_usage, dict):
                total_tokens = extracted_usage.get("total_tokens")
                if total_tokens and isinstance(total_tokens, int) and total_tokens > 0:
                    _fire_record_tpm(rate_limiter, key_id=key_id, tokens=total_tokens)

        return _wrapped()
