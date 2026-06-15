"""Infrastructure adapter: AzureEmbeddingsProvider — azure-embeddings §3 FROZEN @ v1.

Azure OpenAI speaks the OpenAI /v1/embeddings wire shape natively, so this adapter is a
thin passthrough — the OpenAI-compatible sibling of OpenAIDirectProvider. It differs only
in the two things Azure owns (azure_config.py):

  1. Deployment-based routing — the client ``model`` maps to an Azure deployment name and
     the deployment is a URL PATH segment (AzureConfig.resolve_deployment + build_url).
  2. The required ``api-version`` query parameter (baked into build_url).

Unlike BedrockEmbeddingsProvider (Titan), there is ZERO body/response translation: the
request payload is forwarded unchanged and ``resp.json()`` is returned unchanged, so the
OpenAI-shaped ``usage`` bills directly in the application layer.

Auth seam: identical semantics to AzureCompletionUpstream._auth_headers — a Bearer token
when an AzureADTokenProvider is injected, the static api-key otherwise. The SAME
token_provider instance is shared with the chat adapter (one token cache); ``get_token`` is
the single point AAD plugs in.

Design-for-failure (CLAUDE.md): connect/non-stream timeouts; per-instance CircuitBreaker;
5xx + Timeout/Network → UpstreamUnavailableError; AAD token failure fails CLOSED (raises
before the breaker guard / any POST — never a blank-auth request).

Security: ``api_key`` (config) and the bearer token are SECRETS — they enter only the
api-key / Authorization header, NEVER a log field, metric label, span attribute, URL, or
exception message.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider
    from gateway.proxy.infrastructure.azure_config import AzureConfig

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0


class AzureEmbeddingsProvider:
    """Direct HTTP adapter for Azure OpenAI embedding deployments.

    Implements the UpstreamProvider Protocol (post_json / post_multipart / stream_bytes).
    A single instance is created per create_app() call when Azure config is present and
    stored in app.state.provider_registry under the key "azure".

    SECURITY: the api-key and any AAD bearer token are NEVER logged, echoed, or placed in
    any metric label / span attribute / exception message / URL.
    """

    def __init__(
        self,
        *,
        config: AzureConfig,
        token_provider: AzureADTokenProvider | None = None,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        self._config = config
        self._token_provider = token_provider
        self._metrics_registry = metrics_registry
        self._breaker = CircuitBreaker()
        # No base_url — build_url emits an absolute deployment-routed URL per call.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
        )

    async def _auth_headers(self) -> dict[str, str]:
        """Bearer when an AAD provider is injected, static api-key otherwise.

        Token acquisition fails CLOSED — get_token() raises UpstreamUnavailableError on any
        IDP failure, which propagates out of post_json before the breaker guard / POST so we
        never send a blank/absent Authorization header.
        """
        if self._token_provider is not None:
            token = await self._token_provider.get_token()
            return {"Authorization": f"Bearer {token}"}
        return {"api-key": self._config.api_key}

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """POST OpenAI-shaped embeddings to an Azure deployment; return (status, json).

        Args:
            path:    Ignored — the Azure URL is derived from payload["model"] (deployment
                     routing), mirroring BedrockEmbeddingsProvider. The op segment is
                     hard-coded "embeddings".
            payload: OpenAI embeddings request (keys "model", "input", optional "dimensions").
                     Forwarded UNCHANGED (Azure is OpenAI-compatible).

        Raises:
            UpstreamUnavailableError: on 5xx, ConnectError/Timeout/Network, or AAD token failure.

        Returns:
            (200, OpenAI-shaped body) on success, or (4xx, error_body) passed through
            unchanged (incl. Azure content_filter, which is OpenAI-shaped).
        """
        deployment = self._config.resolve_deployment(payload["model"])
        url = self._config.build_url(deployment, "embeddings")

        # Auth headers FIRST — a token failure fails closed before the breaker/POST.
        headers = {**(await self._auth_headers()), "content-type": "application/json"}

        self._breaker.guard()
        try:
            resp = await self._client.post(url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            # FAIL-CLOSED + secret hygiene: suppress the exception chain. The httpx error
            # carries the request object whose HEADERS hold the api-key (or AAD bearer);
            # `from None` keeps that secret out of any crash-reporter / chained-traceback
            # inspection (mirrors azure_ad.py). str(exc) is the clean transport message.
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from None

        status = resp.status_code
        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        # success/4xx: not an upstream outage — record success, pass the body through.
        self._breaker.record_success()
        return status, resp.json()

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Raise UpstreamUnavailableError — Azure images/audio out of scope for v21."""
        raise UpstreamUnavailableError("azure-embeddings: unsupported modality")

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Return an async generator that raises on first iteration.

        Embeddings do not stream — never reached for embedding-modality models.
        """

        async def _gen() -> AsyncIterator[bytes]:
            raise UpstreamUnavailableError("azure-embeddings: unsupported modality")
            yield b""  # pragma: no cover — unreachable; marks this an async generator

        return _gen()


__all__ = ["AzureEmbeddingsProvider"]
