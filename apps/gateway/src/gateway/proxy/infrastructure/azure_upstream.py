"""Infrastructure adapter: AzureCompletionUpstream (azure-chat §3 FROZEN @ v1).

Azure OpenAI is OpenAI-compatible on the wire, so this adapter is a thin PASSTHROUGH
(no request/response translation) — it mirrors OpenRouterCompletionUpstream with two
Azure-specific deltas:

  1. Auth header is ``api-key: <key>`` (NOT ``Authorization: Bearer``).
  2. The request URL is computed PER-REQUEST from the client model:
       config.build_url(config.resolve_deployment(model), "chat/completions")
     The deployment is a path segment that varies by model, so the httpx client has
     NO base_url; complete() POSTs to the full URL each call.

Resilience reuses the shared seam (execute_with_retry + CircuitBreaker), identical to
OpenRouter/Bedrock:
  - Upstream 4xx → pass through verbatim as (status, body); NEVER raised (so the v19
    fallback router can classify content_filter / context_window).
  - Upstream 5xx / 429 / 408 / connect error / pool timeout → UpstreamUnavailableError
    (retried up to max_retries); read/write timeout / network error → raised, not retried.

Security: ``api_key`` (config.api_key) is a SECRET — it enters ONLY the ``api-key``
request header, NEVER a log field, metric label, span attribute, URL, or exception message.

Streaming is implemented in azure-streaming-passthrough (task 3); stream() is a stub here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0


class AzureCompletionUpstream:
    """Forward chat completions to Azure OpenAI deployments (OpenAI-shaped passthrough).

    A single instance is shared for the app lifetime (wired in main.py onto
    _chat_adapters["azure"]). The circuit breaker is per-instance (per-replica).
    Internal attrs follow the OpenRouterCompletionUpstream convention (self._client,
    self._breaker) so tests can swap _client for a MockTransport-backed client.
    """

    def __init__(
        self,
        *,
        config: AzureConfig,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._config = config
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
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
        # Azure uses the `api-key` header — NOT `Authorization: Bearer`.
        return {"api-key": self._config.api_key}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the resolved Azure deployment.

        Returns (status_code, json_body). 4xx pass through verbatim (no exception);
        5xx / transport errors raise UpstreamUnavailableError via the shared retry seam.
        With max_retries=0 (default): exactly one attempt — byte-identical to the
        OpenRouter default.
        """
        model = str(payload.get("model", ""))
        deployment = self._config.resolve_deployment(model)
        url = self._config.build_url(deployment, "chat/completions")
        headers = {**self._auth_headers(), "content-type": "application/json"}

        async def _do_request() -> httpx.Response:
            return await self._client.post(url, json=payload, headers=headers)

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider="azure",
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Return an async generator that yields raw SSE byte chunks (byte-passthrough).

        Mirrors OpenRouterCompletionUpstream.stream exactly, bar the per-request Azure
        deployment URL + `api-key` header. The circuit breaker is checked before the
        first byte; a 5xx open raises UpstreamUnavailableError BEFORE yielding any chunk
        (v19 failover). Zero retry machinery. Billing is application-layer: the caller
        drains these bytes and extract_usage_from_sse reads the terminal usage frame.
        """
        self._breaker.guard()

        model = str(payload.get("model", ""))
        deployment = self._config.resolve_deployment(model)
        url = self._config.build_url(deployment, "chat/completions")
        headers = {**self._auth_headers(), "content-type": "application/json"}

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    json=payload,
                    headers=headers,
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


__all__ = ["AzureCompletionUpstream"]
