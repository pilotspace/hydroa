"""Unified upstream retry seam — shared by every provider's complete() path.

A single, provider-agnostic retry executor: the bounded attempt loop, full-jitter
exponential backoff, a per-error RetryPolicy (retryable vs. terminal), an optional
cumulative deadline, the per-instance circuit-breaker protocol, and the metrics
increment. Extracted from the v5 OpenRouter retry loop (behavior-preserving) and
generalized so Anthropic and Gemini share exactly the same policy.

Invariants (frozen — retry-seam-unify §3 CONTRACT):
  * complete()-ONLY. stream() is never retried.
  * max_retries=0 (default) → exactly ONE attempt, no sleep → byte-identical to v5.
  * deadline_s=0 (default) → no cumulative deadline.
  * The executor receives NO API-key material and emits no secret in logs/labels.
  * Billing: the executor returns exactly the served terminal response — the use
    case calls complete() once, so a retry never double-bills.

Metric label vocabulary:
  reason  ∈ {"connect_error","pool_timeout","upstream_408","upstream_429","upstream_5xx"}
  outcome ∈ {"retried","exhausted","breaker_open","deadline_exceeded"}
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.errors import UpstreamRateLimitedError, UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

_log = structlog.get_logger(__name__)

DEFAULT_BACKOFF_CAP_S: float = 8.0
RETRY_AFTER_MAX_S: float = 60.0


@dataclass(frozen=True)
class RetryPolicy:
    """Classifies a failure into a retry-reason label, or None if it is TERMINAL.

    Retryable statuses: 429, 408, any >= 500.
    Retryable exceptions: ConnectError / ConnectTimeout (connect_error), PoolTimeout.
    Terminal (None): every other 4xx, ReadTimeout / WriteTimeout / NetworkError(base) —
    these may have reached the upstream, so retrying risks double-execution.
    """

    def classify_status(self, status: int) -> str | None:
        if status == 429:
            return "upstream_429"
        if status == 408:
            return "upstream_408"
        if status >= 500:
            return "upstream_5xx"
        return None  # 2xx/3xx success or terminal 4xx — not retried

    def classify_exception(self, exc: BaseException) -> str | None:
        # PoolTimeout first — it is a TimeoutException, distinct from connect errors.
        if isinstance(exc, httpx.PoolTimeout):
            return "pool_timeout"
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "connect_error"
        # ReadTimeout, WriteTimeout, NetworkError(base), anything else → terminal.
        return None


DEFAULT_RETRY_POLICY = RetryPolicy()


def compute_backoff(
    attempt: int, *, backoff_base: float, cap_s: float = DEFAULT_BACKOFF_CAP_S
) -> float:
    """Full-jitter backoff: random.uniform(0, min(cap_s, backoff_base * 2**attempt)).

    `attempt` is 1-indexed (attempt=1 is the first retry).
    """
    window = min(cap_s, backoff_base * (2**attempt))
    return random.uniform(0, window)  # noqa: S311 — jitter, not cryptographic


def parse_retry_after(header_value: str, *, max_s: float = RETRY_AFTER_MAX_S) -> float | None:
    """Parse a Retry-After header into seconds.

    Honors a simple non-negative integer in [0, max_s]. HTTP-date values, negatives,
    out-of-range, and garbage return None → caller falls back to computed backoff.
    """
    stripped = header_value.strip()
    try:
        seconds = int(stripped)
    except ValueError:
        return None
    if 0 <= seconds <= max_s:
        return float(seconds)
    return None


async def execute_with_retry(
    do_request: Callable[[], Awaitable[httpx.Response]],
    render_response: Callable[[httpx.Response], tuple[int, dict[str, Any]]],
    *,
    breaker: CircuitBreaker,
    provider: str,
    max_retries: int,
    backoff_base: float,
    deadline_s: float = 0.0,
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    metrics_registry: MetricsRegistry | None = None,
) -> tuple[int, dict[str, Any]]:
    """Run `do_request` with bounded retries, returning `render_response` of the first
    TERMINAL response (a success or a non-retryable status).

    `do_request` performs exactly one attempt (the POST) — request translation MUST
    happen outside, by the caller. `render_response` transforms a terminal response
    (200 or a passed-through 4xx) into the OpenAI-shaped (status, body) tuple.

    Raises UpstreamUnavailableError when retries are exhausted, the deadline is hit,
    or a terminal transport error occurs; re-raises CircuitOpenError from the breaker.
    """
    start = time.monotonic()
    last_reason: str | None = None

    def _count(reason: str, outcome: str) -> None:
        if metrics_registry is not None:
            metrics_registry.upstream_retries_total.labels(
                provider=provider, reason=reason, outcome=outcome
            ).inc()

    for attempt in range(max_retries + 1):
        # Circuit breaker guard before EVERY attempt (including retries).
        try:
            breaker.guard()
        except Exception:
            if attempt > 0 and last_reason is not None:
                _count(last_reason, "breaker_open")
            raise

        reason: str | None
        status: int | None = None
        retry_after: float | None = None
        terminal_exc: BaseException | None = None

        try:
            resp = await do_request()
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Retryable transport (no bytes reached upstream).
            breaker.on_upstream_error()
            reason = policy.classify_exception(exc)
            last_reason = reason
            terminal_exc = exc
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.NetworkError) as exc:
            # Terminal transport — read/write may have partially executed; never retry.
            breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from None
        else:
            status = resp.status_code
            reason = policy.classify_status(status)
            if reason is None:
                # Success OR terminal 4xx passthrough — the upstream responded.
                breaker.record_success()
                return render_response(resp)
            breaker.on_upstream_error()
            last_reason = reason
            if status == 429:
                hdr = resp.headers.get("Retry-After", "")
                retry_after = parse_retry_after(hdr) if hdr else None

        # --- shared retry tail (reason is a retryable label) ---
        assert reason is not None

        # Exhaustion is decided FIRST: on the last attempt no sleep follows, so the
        # deadline (which gates the NEXT sleep+attempt) is irrelevant — labeling it
        # "exhausted" here keeps that outcome reachable when a deadline is active and
        # avoids a wasted backoff computation.
        if attempt >= max_retries:
            _count(reason, "exhausted")
            if terminal_exc is not None:
                raise UpstreamUnavailableError(str(terminal_exc)) from None
            if reason == "upstream_429":
                raise UpstreamRateLimitedError("Upstream returned 429", retry_after=retry_after)
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        # A retry will follow — compute its backoff and check it against the deadline.
        delay = (
            retry_after
            if retry_after is not None
            else compute_backoff(attempt + 1, backoff_base=backoff_base)
        )

        if deadline_s > 0 and (time.monotonic() - start) + delay > deadline_s:
            _count(reason, "deadline_exceeded")
            if terminal_exc is not None:
                raise UpstreamUnavailableError(str(terminal_exc)) from None
            if reason == "upstream_429":
                raise UpstreamRateLimitedError("Upstream returned 429", retry_after=retry_after)
            raise UpstreamUnavailableError(f"Upstream retry deadline exceeded after {reason}")

        _count(reason, "retried")

        _log.warning(
            "upstream_retry",
            provider=provider,
            attempt=attempt + 1,
            reason=reason,
            delay=round(delay, 3),
        )
        await asyncio.sleep(delay)

    # Unreachable: the loop always returns or raises.
    raise UpstreamUnavailableError("Retry loop exhausted without result")
