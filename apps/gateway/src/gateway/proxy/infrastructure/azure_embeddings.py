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

from gateway.proxy.domain.credential_context import get_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential, ProviderKeyMissing
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0


class AzureEmbeddingsProvider:
    """Direct HTTP adapter for Azure OpenAI embedding deployments.

    Implements the UpstreamProvider Protocol (post_json / post_multipart / stream_bytes).
    A single instance is created per create_app() call and stored in
    app.state.provider_registry under the key "azure".

    SECURITY: the api-key and any AAD bearer token are NEVER logged, echoed, or placed in
    any metric label / span attribute / exception message / URL.

    Credentials are resolved per-request from the AzureCredential contextvar (task-3 BYOK).
    No boot-time config or token_provider is accepted; fail-closed when contextvar is unset.
    """

    def __init__(
        self,
        *,
        token_provider_cache: AzureADTokenProviderCache | None = None,
        metrics_registry: MetricsRegistry | None = None,
    ) -> None:
        # Credentials resolved per-request from the contextvar (task-3 BYOK).
        self._token_provider_cache = token_provider_cache
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

    def _get_credential(self) -> AzureCredential:
        """Read AzureCredential from the request contextvar (fail-closed).

        Raises ProviderKeyMissing("azure") when the contextvar is unset OR holds a
        wrong-type value — NEVER produces an unauthenticated upstream request.
        """
        cred = get_provider_credential()
        if cred is None or not isinstance(cred, AzureCredential):
            raise ProviderKeyMissing("azure")
        return cred

    def _resolve_config_and_cred(self) -> tuple[AzureConfig, AzureCredential]:
        """Resolve the AzureConfig and AzureCredential from the request contextvar.

        Raises ProviderKeyMissing("azure") when the contextvar is unset or holds a
        wrong-type value (fail-closed — no unauthenticated request, ever).
        """
        cred = self._get_credential()
        return cred.to_azure_config(), cred

    async def _auth_headers_for_credential(self, cred: AzureCredential) -> dict[str, str]:
        """Build auth headers from a resolved AzureCredential (contextvar path).

        api_key mode  → ``api-key: <key>`` header.
        aad mode      → Bearer token minted via the per-tenant cache (or a direct
                         AzureADTokenProvider when cache is None — verify / test path).
        Token acquisition fails CLOSED — get_token() raises UpstreamUnavailableError on any
        IDP failure, propagating before the breaker guard / POST so we never send a blank header.
        """
        if cred.mode == "aad":
            ad_cfg = cred.to_azure_ad_config()
            if self._token_provider_cache is not None:
                tp = self._token_provider_cache.get_or_create(ad_cfg)
            else:
                from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider
                tp = AzureADTokenProvider(config=ad_cfg)
            token = await tp.get_token()
            return {"Authorization": f"Bearer {token}"}
        cfg: AzureConfig = cred.to_azure_config()
        return {"api-key": cfg.api_key}

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

        Credential is read from the request contextvar (AzureCredential); raises
        ProviderKeyMissing("azure") before any HTTP if unset or wrong type (fail-closed).

        Raises:
            UpstreamUnavailableError: on 5xx, ConnectError/Timeout/Network, or AAD token failure.

        Returns:
            (200, OpenAI-shaped body) on success, or (4xx, error_body) passed through
            unchanged (incl. Azure content_filter, which is OpenAI-shaped).
        """
        # Fail-closed: read & validate credential BEFORE any network activity.
        cfg, cred = self._resolve_config_and_cred()

        deployment = cfg.resolve_deployment(payload["model"])
        url = cfg.build_url(deployment, "embeddings")

        # Auth headers FIRST — a token failure fails closed before the breaker/POST.
        auth = await self._auth_headers_for_credential(cred)
        headers = {**auth, "content-type": "application/json"}

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
