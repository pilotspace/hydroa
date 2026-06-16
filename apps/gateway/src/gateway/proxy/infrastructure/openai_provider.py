"""Infrastructure adapter: OpenAIDirectProvider — provider-seam TASK.md §3.

Wraps httpx.AsyncClient with:
- Authorization: Bearer <api_key> injection
- Connect timeout 10 s, non-stream total 120 s, stream read 300 s
  (same constants as OpenRouterCompletionUpstream)
- Per-instance CircuitBreaker (same class as OpenRouterCompletionUpstream)

Implements UpstreamProvider Protocol (post_json / post_multipart / stream_bytes).
Satisfies isinstance(x, UpstreamProvider) at runtime (Protocol is runtime_checkable).

Security: api_key is stored as self._api_key and NEVER appears in any log field,
metric label, span attribute, or repr. Follow the same rule as openrouter_api_key.

No retries in v7 (conservative default). Follow-up: openai_max_retries knob.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0

_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIDirectProvider:
    """Direct HTTP adapter for OpenAI-compatible endpoints.

    A single instance is created per create_app() call when openai_api_key is set
    and stored in app.state.provider_registry under the key "openai".

    Internal attributes follow the OpenRouterCompletionUpstream convention exactly
    so PS8 can inject them via __new__:
      self._api_key  — the secret bearer token (never logged/echoed)
      self._client   — httpx.AsyncClient with base_url + timeout wired
      self._breaker  — per-instance CircuitBreaker
    """

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
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
        # metrics_registry is stored for future use (follow-up: per-provider counters).
        # Not used in v7; kept so the constructor signature matches the contract.
        self._metrics_registry = metrics_registry

    def _auth_headers(self) -> dict[str, str]:
        """Build OpenAI auth headers from the request-scoped credential contextvar.

        Raises ProviderKeyMissing when the contextvar is unset or non-Bearer.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing("openai")
        return {"Authorization": f"Bearer {cred.secret.get_secret_value()}"}

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST path with JSON body; returns (status_code, json_body).

        Used by: embeddings (POST /embeddings), images (POST /images/generations).
        Circuit breaker guards every call.
        5xx → breaker.on_upstream_error(); success/4xx → breaker.record_success().
        """
        self._breaker.guard()
        try:
            resp = await self._client.post(
                path,
                json=payload,
                headers=self._auth_headers(),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from None

        status = resp.status_code
        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        self._breaker.record_success()
        return status, resp.json()

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST path with multipart/form-data; returns (status_code, json_body).

        Used by: audio STT (POST /audio/transcriptions).
        Circuit breaker guards every call.
        """
        self._breaker.guard()
        try:
            resp = await self._client.post(
                path,
                files=files,
                data=data,
                headers=self._auth_headers(),
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from None

        status = resp.status_code
        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        self._breaker.record_success()
        return status, resp.json()

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an async generator yielding raw bytes.

        Used by: audio TTS (POST /audio/speech).
        Circuit breaker is checked before the first byte (same as CompletionUpstream.stream).
        Zero retry machinery.
        """
        self._breaker.guard()

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "POST",
                    path,
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
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()


__all__ = ["OpenAIDirectProvider"]
