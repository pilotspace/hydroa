"""Infrastructure adapter: OpenRouterCompletionUpstream.

Wraps httpx.AsyncClient with:
- Platform API key injection (GATEWAY_OPENROUTER_API_KEY)
- Connect timeout 10 s, non-stream total 120 s, stream read 300 s
- Circuit breaker (5 consecutive failures → 30 s open → half-open)
- NEVER retries (completions are non-idempotent)
- Upstream 4xx: pass through verbatim
- Upstream 5xx / timeout / network error: raise UpstreamUnavailableError
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

_BASE_URL = "https://openrouter.ai/api/v1"
_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0


class OpenRouterCompletionUpstream:
    """Forwards completions to OpenRouter with circuit breaker protection.

    A single instance is shared for the lifetime of the application
    (wired in main.py onto app.state.completion_upstream).
    The circuit breaker state is per-instance (per-replica).
    """

    def __init__(self, api_key: str) -> None:
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

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward non-streaming request to OpenRouter.

        Returns (status_code, json_body).
        Upstream 4xx: pass-through.
        Upstream 5xx / network / timeout: raise UpstreamUnavailableError.
        Circuit open: raise CircuitOpenError (re-raised from breaker.guard).
        NEVER retries.
        """
        self._breaker.guard()
        try:
            resp = await self._client.post(
                "/chat/completions",
                json=payload,
                headers=self._auth_headers(),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from exc

        if resp.status_code >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {resp.status_code}")

        self._breaker.record_success()
        return resp.status_code, resp.json()

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields raw SSE byte chunks.

        The circuit breaker is checked before the first byte is yielded.
        Raises CircuitOpenError immediately if the breaker is open.
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
