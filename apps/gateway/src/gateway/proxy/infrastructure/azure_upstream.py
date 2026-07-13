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

from gateway.core.egress_policy import (
    DenyPrivateAndMetadataEgressPolicy,
    EgressDeniedError,
    EgressPolicy,
)
from gateway.core.error_catalog import UPSTREAM_EGRESS_DENIED
from gateway.proxy.domain.credential_context import (
    get_credential_tenant,
    get_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential, ProviderKeyMissing
from gateway.proxy.domain.web_search import WEB_SEARCH_FLAG
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.upstream_retry import execute_with_retry

if TYPE_CHECKING:
    from gateway.observability.metrics import MetricsRegistry
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache

_CONNECT_TIMEOUT = 10.0
_NON_STREAM_TIMEOUT = 120.0
_STREAM_READ_TIMEOUT = 300.0


class AzureCompletionUpstream:
    """Forward chat completions to Azure OpenAI deployments (OpenAI-shaped passthrough).

    A single instance is shared for the app lifetime (wired in main.py onto
    _chat_adapters["azure"]). The circuit breaker is per-instance (per-replica).
    Internal attrs follow the OpenRouterCompletionUpstream convention (self._client,
    self._breaker) so tests can swap _client for a MockTransport-backed client.

    Credentials are resolved per-request from the AzureCredential contextvar (task-3 BYOK).
    No boot-time config or token_provider is accepted; fail-closed when contextvar is unset.
    """

    def __init__(
        self,
        *,
        token_provider_cache: AzureADTokenProviderCache | None = None,
        max_retries: int = 0,
        backoff_base: float = 0.5,
        retry_deadline_s: float = 0.0,
        metrics_registry: MetricsRegistry | None = None,
        egress_policy: EgressPolicy | None = None,
    ) -> None:
        # Credentials resolved per-request from the contextvar (task-3 BYOK).
        self._token_provider_cache = token_provider_cache
        self._breaker = CircuitBreaker()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT,
                read=_NON_STREAM_TIMEOUT,
                write=_NON_STREAM_TIMEOUT,
                pool=_CONNECT_TIMEOUT,
            ),
            follow_redirects=False,
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._retry_deadline_s = retry_deadline_s
        self._metrics_registry = metrics_registry
        # S3 SSRF/IMDS deny (edge-input-hardening §3 Part B) — DEFAULTS TO THE REAL POLICY
        # (fail-closed by default); tests must EXPLICITLY opt out via AllowAllEgressPolicy.
        self._egress_policy: EgressPolicy = (
            egress_policy if egress_policy is not None else DenyPrivateAndMetadataEgressPolicy()
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

    async def _auth_headers_for_credential(self, cred: AzureCredential) -> dict[str, str]:
        """Build auth headers from a resolved AzureCredential (contextvar path).

        api_key mode  → ``api-key: <key>`` header.
        aad mode      → Bearer token minted via the per-tenant cache (or a direct
                         AzureADTokenProvider when cache is None — verify / test path).
        """
        if cred.mode == "aad":
            ad_cfg = cred.to_azure_ad_config()
            if self._token_provider_cache is not None:
                # M4 CR-2: scope cached token per (hydroa_tenant, identity) — two Hydroa
                # tenants sharing one Azure AD app registration must not cross tokens.
                tp = self._token_provider_cache.get_or_create(ad_cfg, get_credential_tenant())
            else:
                # No cache supplied (e.g. verify tests) — instantiate directly per-call,
                # propagating THIS adapter's own egress_policy (e.g. a test's
                # AllowAllEgressPolicy) so the fallback path isn't silently stricter than
                # the cached path.
                from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider

                tp = AzureADTokenProvider(config=ad_cfg, egress_policy=self._egress_policy)
            token = await tp.get_token()
            return {"Authorization": f"Bearer {token}"}
        # api_key mode
        cfg = cred.to_azure_config()
        return {"api-key": cfg.api_key}

    def _resolve_config_and_cred(self) -> tuple[AzureConfig, AzureCredential]:
        """Resolve the AzureConfig and AzureCredential from the request contextvar.

        Raises ProviderKeyMissing("azure") when the contextvar is unset or holds a
        wrong-type value (fail-closed — no unauthenticated request, ever).
        """
        cred = self._get_credential()
        return cred.to_azure_config(), cred

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Forward a non-streaming chat request to the resolved Azure deployment.

        Returns (status_code, json_body). 4xx pass through verbatim (no exception);
        5xx / transport errors raise UpstreamUnavailableError via the shared retry seam.
        With max_retries=0 (default): exactly one attempt — byte-identical to the
        OpenRouter default.

        Credential is read from the request contextvar (AzureCredential); raises
        ProviderKeyMissing("azure") before any HTTP if unset or wrong type (fail-closed).
        """
        cfg, cred = self._resolve_config_and_cred()
        model = str(payload.get("model", ""))
        deployment = cfg.resolve_deployment(model)
        url = cfg.build_url(deployment, "chat/completions")
        # S3 SSRF/IMDS deny — checked FRESH on every dial, BEFORE the tenant's secret is
        # attached (auth headers below). Never cached from the write-time check.
        try:
            await self._egress_policy.check(url)
        except EgressDeniedError:
            raise UPSTREAM_EGRESS_DENIED.exc() from None
        auth = await self._auth_headers_for_credential(cred)
        headers = {**auth, "content-type": "application/json"}
        # Azure is a non-grounding provider: strip the raw web_search flag so it never
        # reaches upstream as an unknown field (would 400). No tool injection needed.
        outbound = {k: v for k, v in payload.items() if k != WEB_SEARCH_FLAG}

        async def _do_request() -> httpx.Response:
            return await self._client.post(url, json=outbound, headers=headers)

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
        deployment URL + auth header derived from the tenant's AzureCredential contextvar.
        The circuit breaker is checked before the first byte; a 5xx open raises
        UpstreamUnavailableError BEFORE yielding any chunk (v19 failover). Zero retry
        machinery. Billing is application-layer: the caller drains these bytes and
        extract_usage_from_sse reads the terminal usage frame.

        Credential is read from the request contextvar (AzureCredential); raises
        ProviderKeyMissing("azure") before any HTTP if unset or wrong type (fail-closed).
        """
        # Fail-closed: read & validate credential BEFORE yielding the generator object.
        cfg, cred = self._resolve_config_and_cred()
        model = str(payload.get("model", ""))
        deployment = cfg.resolve_deployment(model)
        url = cfg.build_url(deployment, "chat/completions")
        self._breaker.guard()
        # Azure is a non-grounding provider: strip web_search flag before it reaches upstream.
        outbound = {k: v for k, v in payload.items() if k != WEB_SEARCH_FLAG}

        async def _gen() -> AsyncIterator[bytes]:
            # S3 SSRF/IMDS deny — checked FRESH on every dial, BEFORE the tenant's secret
            # is attached (auth headers below). Never cached from the write-time check.
            try:
                await self._egress_policy.check(url)
            except EgressDeniedError:
                raise UPSTREAM_EGRESS_DENIED.exc() from None
            # auth headers are awaited INSIDE the generator (the token fetch is async;
            # a token failure raises UpstreamUnavailableError before the first byte).
            auth = await self._auth_headers_for_credential(cred)
            headers = {**auth, "content-type": "application/json"}
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    json=outbound,
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
            # RemoteProtocolError = graceful mid-stream peer-close (Finding C, v35):
            # a ProtocolError, not a NetworkError — map it like any upstream failure so
            # the use-case mid-stream catch can emit the terminal SSE error frame + [DONE].
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
            ) as exc:
                self._breaker.on_upstream_error()
                raise UpstreamUnavailableError(str(exc)) from None

        return _gen()


__all__ = ["AzureCompletionUpstream"]
