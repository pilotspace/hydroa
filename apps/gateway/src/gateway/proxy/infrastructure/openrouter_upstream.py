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

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx
import structlog

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_BASE_URL = "https://openrouter.ai/api/v1"
_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

_log = structlog.get_logger(__name__)


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
        base_url: str = _BASE_URL,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward non-streaming request to OpenRouter via the unified retry seam.

        Returns (status_code, json_body).
        Upstream 4xx (!=429/408): pass-through after exactly 1 attempt.
        Upstream 5xx / 429 / 408 / connect error / pool timeout: retried up to
        _max_retries times with full-jitter backoff, bounded by _retry_deadline_s.
        Read/write timeout / network error: raise UpstreamUnavailableError (not retried).
        Circuit open: raise CircuitOpenError (re-raised from breaker.guard).

        With _max_retries=0 (default): exactly one attempt, no backoff, no sleep —
        byte-identical to v5 behavior. The retry policy lives in upstream_retry.py.
        """

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                "/chat/completions",
                json=payload,
                headers=self._auth_headers(),
            )

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider="openrouter",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

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
