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

Chat completions (complete()) retry via the shared execute_with_retry seam — parity
with the other 5 chat adapters; gated by max_retries (default 0 = single attempt).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential, ProviderKeyMissing
from gateway.proxy.domain.web_search import WEB_SEARCH_FLAG, native_web_search_tool
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

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

    The three retry knobs are declared as CLASS-LEVEL defaults so that __new__-built
    instances (which skip __init__, e.g. the openai_chat_dispatch test doubles) still
    resolve them to a single-attempt no-retry configuration rather than AttributeError.
    """

    # Class-level defaults — read by __new__-built instances that skip __init__.
    _max_retries: int = 0
    _backoff_base: float = 0.5
    _retry_deadline_s: float = 0.0

    # Class-level default — read by __new__-built instances that skip __init__.
    _provider_name: str = "openai"

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        provider_name: str = "openai",
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
        connect_timeout: float = _CONNECT_TIMEOUT,
        read_timeout: float = _NON_STREAM_TIMEOUT,
    ) -> None:
        # connect_timeout/read_timeout (ml-moderation-layer §3 CONTRACT — FROZEN @ v1):
        # optional override so a SECOND, dedicated instance (moderation) can use a
        # tighter timeout budget than the chat-completion default. Both default to the
        # existing module constants, so every pre-existing caller (chat/embeddings/
        # images/audio adapters) is byte-identical.
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(
                connect=connect_timeout,
                read=read_timeout,
                write=read_timeout,
                pool=connect_timeout,
            ),
        )
        # Retry knobs — threaded through to the shared execute_with_retry seam by complete().
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry
        self._provider_name = provider_name

    def _auth_headers(self) -> dict[str, str]:
        """Build OpenAI auth headers from the request-scoped credential contextvar.

        Raises ProviderKeyMissing when the contextvar is unset or non-Bearer.
        """
        cred = get_provider_credential()
        if not isinstance(cred, BearerCredential):
            raise ProviderKeyMissing(self._provider_name)
        return {"Authorization": f"Bearer {cred.secret.get_secret_value()}"}

    def _maybe_inject_web_search(self, payload: dict[str, object]) -> dict[str, object]:
        """Inject the web_search_preview native tool when web_search flag is truthy.

        Returns a shallow copy with:
          - "web_search" key removed (NEVER reaches upstream).
          - {"type":"web_search_preview"} appended to a copy of the tools list
            when web_search was truthy (preserves any existing function tools).

        When web_search is absent/falsy, returns a copy with the flag stripped but
        tools untouched — byte-identical payload for the non-web-search case.
        Non-destructive: the caller's dict is never mutated.
        """
        if WEB_SEARCH_FLAG not in payload:
            return payload
        outbound = {k: v for k, v in payload.items() if k != WEB_SEARCH_FLAG}
        if payload.get(WEB_SEARCH_FLAG):
            ws_tool = native_web_search_tool(self._provider_name)
            if ws_tool is not None:
                existing = list(outbound.get("tools", []))  # type: ignore[arg-type]
                outbound["tools"] = [*existing, ws_tool]
        return outbound

    # -- CompletionUpstream surface (chat dispatch) --------------------------
    # provider="openai" routes here via ProviderAwareCompletionUpstream.complete/stream.
    # Pinned to "/chat/completions"; mirrors post_json / stream_bytes exactly so the
    # circuit-breaker + error semantics match the rest of the chat adapters.

    async def complete(
        self,
        payload: dict[str, object],
    ) -> tuple[int, dict[str, object]]:
        """Forward a non-streaming chat request to POST /chat/completions.

        Credential resolved per-request from the contextvar via _auth_headers()
        (raises ProviderKeyMissing("openai") when unset/non-Bearer — fail-closed,
        BEFORE any HTTP call). Delegates to the shared execute_with_retry seam:
        retryable 5xx / 408 / 429 / connect-timeout are retried up to _max_retries
        with full-jitter backoff bounded by _retry_deadline_s (429 honors Retry-After);
        200 and terminal 4xx pass through; read/write/network errors raise
        UpstreamUnavailableError and are NOT retried. With _max_retries=0 (default)
        this is exactly one attempt — byte-identical to the pre-retry behavior.
        """
        outbound = self._maybe_inject_web_search(payload)

        async def _do_request() -> httpx.Response:
            return await self._client.post(
                "/chat/completions",
                json=outbound,
                headers=self._auth_headers(),
            )

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider=self._provider_name,
            max_retries=self._max_retries,
            backoff_base=self._backoff_base,
            deadline_s=self._retry_deadline_s,
            metrics_registry=self._metrics_registry,
        )

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        """Forward a streaming chat request to POST /chat/completions.

        Mirrors stream_bytes but pinned to the chat path. The breaker is checked
        before the first byte; a >=500 status raises UpstreamUnavailableError
        BEFORE any byte is yielded (clean 502, never a leaked partial body).
        """
        self._breaker.guard()
        outbound = self._maybe_inject_web_search(payload)

        async def _gen() -> AsyncIterator[bytes]:
            try:
                async with self._client.stream(
                    "POST",
                    "/chat/completions",
                    json=outbound,
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

    async def post_json_with_retry(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        max_retries: int,
        backoff_base: float,
        deadline_s: float = 0.0,
        provider_label: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """POST path with JSON body, retried via the shared execute_with_retry seam.

        Like post_json (guard → auth → POST → breaker bookkeeping) but with bounded
        retry — for an ancillary IO seam on a DEDICATED instance that needs retry
        semantics distinct from post_json's single attempt (embeddings/images) or
        complete()'s pinned /chat/completions path. `provider_label` overrides the
        metrics/log provider label (e.g. "openai_moderation") so a secondary seam on
        the same underlying API is distinguishable from primary chat/embeddings
        traffic in upstream_retries_total.

        ml-moderation-layer §3 CONTRACT (FROZEN @ v1): the moderation adapter
        (OpenAiModerationClient) is this method's first caller — added here (a public
        method) rather than reaching into self._client/_breaker/_auth_headers() from
        a different infrastructure module, which the project's strict pyright config
        (reportPrivateUsage) correctly rejects.
        """

        async def _do_request() -> httpx.Response:
            return await self._client.post(path, json=payload, headers=self._auth_headers())

        return await execute_with_retry(
            _do_request,
            lambda resp: (resp.status_code, resp.json()),
            breaker=self._breaker,
            provider=provider_label or self._provider_name,
            max_retries=max_retries,
            backoff_base=backoff_base,
            deadline_s=deadline_s,
            metrics_registry=self._metrics_registry,
        )

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
