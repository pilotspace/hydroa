"""Infrastructure adapter: OpenRouterCompletionUpstream.

Wraps httpx.AsyncClient with:
- Platform API key injection (GATEWAY_OPENROUTER_API_KEY)
- Connect timeout 10 s, non-stream total 120 s, stream read 300 s
- Circuit breaker (5 consecutive failures -> 30 s open -> half-open)
- Opt-in bounded retries (default GATEWAY_UPSTREAM_MAX_RETRIES=0 = NEVER retry,
  byte-identical to v5 behavior; operators enable retries by setting the knob).
  The "NEVER retry a completion (non-idempotent)" rule from proxy-completions TASK.md §1
  is superseded by this module's retry-policy contract — preserved by construction:
  max_retries=0 (default) makes the behavior byte-identical to the original rule.
  See .add/tasks/retry-policy/TASK.md §3 SUPERSESSION BLOCK.
- Upstream 4xx: pass through verbatim (never retried)
- Upstream 5xx / connect error / pool timeout: raise UpstreamUnavailableError
  (retried when max_retries > 0); read/write timeout / network error: raise
  UpstreamUnavailableError immediately (never retried — conservative against double-billing)
- Retries are confined to complete() ONLY; stream() has zero retry machinery
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_BASE_URL = "https://openrouter.ai/api/v1"
_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

# Backoff constants (contract §3)
_BACKOFF_CAP_S = 8.0
_RETRY_AFTER_MAX_S = 60.0

_log = structlog.get_logger(__name__)


def _classify_reason(exc_or_status: Exception | int) -> str | None:
    """Return the retry reason label for a failure, or None if not retryable.

    Retryable:
      httpx.ConnectError        -> "connect_error"
      httpx.ConnectTimeout      -> "connect_error"
      httpx.PoolTimeout         -> "pool_timeout"
      HTTP 429                  -> "upstream_429"
      HTTP 5xx (>= 500)         -> "upstream_5xx"

    NOT retryable (returns None):
      httpx.ReadTimeout, httpx.WriteTimeout, httpx.NetworkError (base), other 4xx
    """
    if isinstance(exc_or_status, int):
        if exc_or_status == 429:
            return "upstream_429"
        if exc_or_status >= 500:
            return "upstream_5xx"
        return None  # 4xx passthrough, not retried

    if isinstance(exc_or_status, httpx.PoolTimeout):
        return "pool_timeout"
    if isinstance(exc_or_status, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "connect_error"
    # httpx.ReadTimeout, httpx.WriteTimeout, httpx.NetworkError (base) — NOT retried
    return None


def _parse_retry_after(header_value: str) -> float | None:
    """Parse a Retry-After header value into a delay in seconds.

    Returns a float if the value is a simple non-negative integer <= 60 s.
    Returns None for HTTP-date values and any garbage — caller falls back to backoff.
    Per contract: only simple integer seconds are honored; HTTP-date -> fallback.
    """
    stripped = header_value.strip()
    try:
        seconds = int(stripped)
    except ValueError:
        return None  # HTTP-date or garbage -> fall back to computed backoff
    if 0 <= seconds <= _RETRY_AFTER_MAX_S:
        return float(seconds)
    return None  # out of range -> fall back to computed backoff


class OpenRouterCompletionUpstream:
    """Forwards completions to OpenRouter with circuit breaker protection.

    A single instance is shared for the lifetime of the application
    (wired in main.py onto app.state.completion_upstream).
    The circuit breaker state is per-instance (per-replica).

    Retry policy (opt-in):
      _max_retries=0 (default): exactly one attempt — byte-identical to v5.
      _max_retries>0: up to _max_retries additional attempts with full-jitter
      exponential backoff. Retries only on the complete() path; stream() is unchanged.
    """

    def __init__(
        self,
        api_key: str,
        *,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._api_key = api_key
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=_BASE_URL,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._metrics_registry = metrics_registry

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _compute_backoff(self, attempt: int) -> float:
        """Full-jitter backoff: random.uniform(0, min(cap, base * 2^attempt)).

        attempt in {1, 2, ..., max_retries} where attempt=1 is the first retry.
        """
        window = min(_BACKOFF_CAP_S, self._backoff_base * (2**attempt))
        return random.uniform(0, window)  # noqa: S311 — jitter, not cryptographic

    def _increment_retry_counter(self, reason: str, outcome: str) -> None:
        """Increment the gateway_upstream_retries_total counter if registry is wired."""
        registry = getattr(self, "_metrics_registry", None)
        if registry is not None:
            registry.upstream_retries_total.labels(reason=reason, outcome=outcome).inc()

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward non-streaming request to OpenRouter with optional bounded retries.

        Returns (status_code, json_body).
        Upstream 4xx (!=429): pass-through after exactly 1 attempt.
        Upstream 5xx / 429 / connect error / pool timeout: retried up to _max_retries times.
        Read/write timeout / network error: raise UpstreamUnavailableError (not retried).
        Circuit open: raise CircuitOpenError (re-raised from breaker.guard).

        The 120 s timeout envelope applies per-attempt (httpx Timeout is per-request).
        With max_retries=5 and base=0.5 s the worst-case backoff delay is ~18 s;
        429 Retry-After (capped 60 s) can add up to 5x60 s=300 s of wait in a
        429-storm — operator-opted via the retry knob. A cumulative-deadline guard
        is a sensible future hardening; not a v6 blocker (see TASK.md §3 flags).

        With _max_retries=0 (default): exactly one attempt, no backoff, no sleep —
        byte-identical to v5 behavior.
        """
        last_reason: str | None = None
        for attempt in range(self._max_retries + 1):
            # Circuit breaker guard before EVERY attempt (including retries).
            # CircuitOpenError propagates immediately — no further attempts.
            # When the breaker opens MID-LOOP (a retry burst tripped it), the
            # aborted retry is counted with outcome="breaker_open" (§3 label set).
            try:
                self._breaker.guard()
            except Exception:
                if attempt > 0 and last_reason is not None:
                    self._increment_retry_counter(last_reason, "breaker_open")
                raise

            try:
                resp = await self._client.post(
                    "/chat/completions",
                    json=payload,
                    headers=self._auth_headers(),
                )
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                # Retryable transport errors — safe to retry (no bytes reached upstream)
                self._breaker.on_upstream_error()
                reason = _classify_reason(exc)
                last_reason = reason
                assert reason is not None  # guaranteed by the except clause above

                is_last = attempt >= self._max_retries
                outcome = "exhausted" if is_last else "retried"
                self._increment_retry_counter(reason, outcome)

                if is_last:
                    raise UpstreamUnavailableError(str(exc)) from exc

                delay = self._compute_backoff(attempt + 1)
                _log.warning(
                    "upstream_retry",
                    attempt=attempt + 1,
                    reason=reason,
                    delay=round(delay, 3),
                )
                await asyncio.sleep(delay)
                continue

            except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.NetworkError) as exc:
                # Non-retryable: read timeout risks double-billing; write timeout /
                # network error state is unknown — conservative v6 position.
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from exc

            # --- HTTP response received ---
            status = resp.status_code

            # 429 — retryable, honor Retry-After header if valid integer <= 60 s
            if status == 429:
                self._breaker.on_upstream_error()
                reason = "upstream_429"
                last_reason = reason

                is_last = attempt >= self._max_retries
                outcome = "exhausted" if is_last else "retried"
                self._increment_retry_counter(reason, outcome)

                if is_last:
                    raise UpstreamUnavailableError(f"Upstream returned {status}")

                # Prefer Retry-After header; fall back to computed backoff
                retry_after_hdr = resp.headers.get("Retry-After", "")
                parsed = _parse_retry_after(retry_after_hdr) if retry_after_hdr else None
                delay = parsed if parsed is not None else self._compute_backoff(attempt + 1)

                _log.warning(
                    "upstream_retry",
                    attempt=attempt + 1,
                    reason=reason,
                    delay=round(delay, 3),
                )
                await asyncio.sleep(delay)
                continue

            # 5xx (>= 500, not 429) — retryable
            if status >= 500:
                self._breaker.on_upstream_error()
                reason = "upstream_5xx"
                last_reason = reason

                is_last = attempt >= self._max_retries
                outcome = "exhausted" if is_last else "retried"
                self._increment_retry_counter(reason, outcome)

                if is_last:
                    raise UpstreamUnavailableError(f"Upstream returned {status}")

                delay = self._compute_backoff(attempt + 1)
                _log.warning(
                    "upstream_retry",
                    attempt=attempt + 1,
                    reason=reason,
                    delay=round(delay, 3),
                )
                await asyncio.sleep(delay)
                continue

            # Success or 4xx passthrough — not retried
            self._breaker.record_success()
            return status, resp.json()

        # Should be unreachable (loop always raises or returns), but satisfies mypy
        raise UpstreamUnavailableError("Retry loop exhausted without result")

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields raw SSE byte chunks.

        The circuit breaker is checked before the first byte is yielded.
        Raises CircuitOpenError immediately if the breaker is open.
        Zero retry machinery — stream() is unchanged by the retry-policy task.
        """
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                    headers=self._auth_headers(),
                    timeout=httpx.Timeout(
                        connect=_CONNECT_TIMEOUT,
                        read=_STREAM_READ_TIMEOUT,
                        write=_NON_STREAM_TIMEOUT,
                        pool=_CONNECT_TIMEOUT,
                    ),
                ) as response:
                    if response.status_code >= 500:
                        self._breaker.on_upstream_error()
                        raise UpstreamUnavailableError(
                            f"Upstream returned {response.status_code} on stream"
                        )
                    self._breaker.record_success()
                    async for chunk in response.aiter_bytes():
                        yield chunk
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from exc

        return _gen()
