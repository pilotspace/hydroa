"""Red suite for azure-embeddings (v21 task 5): Azure OpenAI embeddings provider.

Azure is OpenAI-compatible, so this adapter is a thin passthrough: deployment-routed
URL + api-version + the shared api-key/AAD auth seam, with ZERO body/response
translation (unlike BedrockEmbeddingsProvider). HTTP via httpx.MockTransport; no network.

CONTRACT (FROZEN @ v1): azure-embeddings TASK.md §3
  - AzureEmbeddingsProvider implements UpstreamProvider (post_json/post_multipart/stream_bytes).
  - post_json: resolve_deployment → build_url(dep, "embeddings") → POST payload unchanged →
    (status, resp.json()) unchanged; 5xx/Timeout/Network → UpstreamUnavailableError; 4xx pass-through.
  - _auth_headers: Bearer when aad mode, api-key otherwise (shared AAD seam via contextvar).
  - create_app registers _providers["azure"] unconditionally (v25 task-3 BYOK).

v25 task-3 amendment: ctor drops config=/token_provider=; credentials travel via
AzureCredential in the contextvar. Each post_json() call is wrapped with
set_provider_credential / reset_provider_credential.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.core.egress_policy import AllowAllEgressPolicy
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# RED: azure_embeddings does not exist yet → ModuleNotFoundError.
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider

# v25 task-3: AzureCredential (api_key mode) mirrors the old _AZ_CFG.
_AZ_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"text-embedding-3-small": "prod-embed"},
    api_key="api-key-secret",
)

# v25 task-3: AzureCredential (aad mode) mirrors the old token_provider path.
_AZ_AAD_CRED = AzureCredential(
    mode="aad",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"text-embedding-3-small": "prod-embed"},
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="top-secret",
)


class _SpyBreaker(CircuitBreaker):
    """Records breaker transitions so resilience semantics are asserted, not assumed."""

    def __init__(self) -> None:
        super().__init__()
        self.errors = 0
        self.successes = 0

    def on_upstream_error(self) -> None:
        self.errors += 1
        super().on_upstream_error()

    def record_success(self) -> None:
        self.successes += 1
        super().record_success()


def _make_adapter(
    handler: object, *, breaker: CircuitBreaker | None = None
) -> AzureEmbeddingsProvider:
    adapter = AzureEmbeddingsProvider(
        token_provider_cache=None, egress_policy=AllowAllEgressPolicy()
    )
    if breaker is not None:
        adapter._breaker = breaker  # type: ignore[attr-defined]
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def test_api_key_routes_to_deployment_url() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": "text-embedding-3-small",
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            },
        )

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        status, body = await adapter.post_json(
            "/embeddings", {"model": "text-embedding-3-small", "input": "hello"}
        )
    finally:
        reset_provider_credential(tok)

    assert status == 200
    assert (
        seen["url"]
        == "https://r.openai.azure.com/openai/deployments/prod-embed/embeddings?api-version=2024-10-21"
    )
    assert seen["api_key"] == "api-key-secret"
    assert seen["authorization"] is None
    # passthrough — body returned unchanged, usage intact for billing
    assert body["usage"]["total_tokens"] == 3
    assert body["data"][0]["embedding"] == [0.1, 0.2]


async def test_aad_sends_bearer_token() -> None:
    """AAD mode: AzureCredential with mode='aad' produces a Bearer token header.

    token_provider_cache=None causes the adapter to instantiate AzureADTokenProvider
    directly per-call. We patch the provider's _client to return a token so no real
    IDP call is made.
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    adapter = _make_adapter(handler)

    # Patch the AzureADTokenProvider to return a fake token without hitting IDP.
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider

    original_get_token = AzureADTokenProvider.get_token

    async def _fake_get_token(self: AzureADTokenProvider) -> str:
        return "tok-xyz"

    AzureADTokenProvider.get_token = _fake_get_token  # type: ignore[method-assign]
    try:
        tok = set_provider_credential(_AZ_AAD_CRED)
        try:
            await adapter.post_json(
                "/embeddings", {"model": "text-embedding-3-small", "input": ["a", "b"]}
            )
        finally:
            reset_provider_credential(tok)
    finally:
        AzureADTokenProvider.get_token = original_get_token  # type: ignore[method-assign]

    assert seen["authorization"] == "Bearer tok-xyz"
    assert seen["api_key"] is None


async def test_identity_deployment_for_unmapped_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    cred = AzureCredential(
        mode="api_key",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
        api_key="k",
    )
    adapter = AzureEmbeddingsProvider(
        token_provider_cache=None, egress_policy=AllowAllEgressPolicy()
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    tok = set_provider_credential(cred)
    try:
        await adapter.post_json("/embeddings", {"model": "my-embed", "input": "x"})
    finally:
        reset_provider_credential(tok)

    assert "/openai/deployments/my-embed/embeddings?api-version=2024-10-21" in str(seen["url"])
    assert '"model":"my-embed"' in str(seen["body"]).replace(" ", "")


async def test_upstream_500_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.post_json(
                "/embeddings", {"model": "text-embedding-3-small", "input": "x"}
            )
    finally:
        reset_provider_credential(tok)
    # 5xx is an upstream outage → the breaker must record the failure (resilience seam).
    assert spy.errors == 1
    assert spy.successes == 0


async def test_4xx_content_filter_passes_through() -> None:
    err = {"error": {"code": "content_filter", "message": "blocked"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=err)

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    tok = set_provider_credential(_AZ_CRED)
    try:
        status, body = await adapter.post_json(
            "/embeddings", {"model": "text-embedding-3-small", "input": "x"}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 400
    assert body == err
    # 4xx is NOT an outage (the upstream responded) → record success, not error.
    assert spy.successes == 1
    assert spy.errors == 0


async def test_network_error_fails_closed_no_secret_in_chain() -> None:
    # SECURITY REGRESSION (task-5 review FINDING 1): the httpx transport error carries
    # the request object whose HEADERS hold the api-key. post_json must suppress the
    # exception chain (`from None`) so a crash-reporter walking __cause__.request.headers
    # can never surface the secret. Also asserts the breaker recorded the outage.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable")

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc:
            await adapter.post_json(
                "/embeddings", {"model": "text-embedding-3-small", "input": "x"}
            )
    finally:
        reset_provider_credential(tok)
    assert exc.value.__cause__ is None
    assert "api-key-secret" not in str(exc.value)
    assert spy.errors == 1
    assert spy.successes == 0


async def test_success_records_breaker_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    tok = set_provider_credential(_AZ_CRED)
    try:
        status, _ = await adapter.post_json(
            "/embeddings", {"model": "text-embedding-3-small", "input": "x"}
        )
    finally:
        reset_provider_credential(tok)
    assert status == 200
    assert spy.successes == 1
    assert spy.errors == 0


async def test_token_failure_fails_closed_no_post() -> None:
    """AAD token failure raises before any POST — no blank-auth upstream request."""
    hits = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    adapter = _make_adapter(handler)

    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider

    async def _raising_get_token(self: AzureADTokenProvider) -> str:
        raise UpstreamUnavailableError("Azure AD token request failed")

    original_get_token = AzureADTokenProvider.get_token
    AzureADTokenProvider.get_token = _raising_get_token  # type: ignore[method-assign]
    try:
        tok = set_provider_credential(_AZ_AAD_CRED)
        try:
            with pytest.raises(UpstreamUnavailableError):
                await adapter.post_json(
                    "/embeddings", {"model": "text-embedding-3-small", "input": "x"}
                )
        finally:
            reset_provider_credential(tok)
    finally:
        AzureADTokenProvider.get_token = original_get_token  # type: ignore[method-assign]

    assert hits[0] == 0  # never POSTed with blank/absent auth


async def test_post_multipart_unsupported() -> None:
    adapter = _make_adapter(lambda request: httpx.Response(200, json={}))
    with pytest.raises(UpstreamUnavailableError):
        await adapter.post_multipart("/x", files={}, data={})


async def test_stream_bytes_unsupported() -> None:
    adapter = _make_adapter(lambda request: httpx.Response(200, json={}))
    with pytest.raises(UpstreamUnavailableError):
        async for _ in adapter.stream_bytes("/x", {"model": "m"}):
            pass


def test_wiring_registers_azure_provider_unconditionally(monkeypatch: pytest.MonkeyPatch) -> None:
    """v25 task-3: azure embeddings provider registered unconditionally — no env-cred guard.

    RIGHT-REASON RED: current wiring gates 'azure' on resolve_azure_config(settings)
    being truthy → absent without env creds → assertion fails.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        # No azure_api_key, no azure_client_secret, no azure_endpoint
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert isinstance(app.state.provider_registry.get("azure"), AzureEmbeddingsProvider), (
        "provider_registry.get('azure') must be AzureEmbeddingsProvider UNCONDITIONALLY "
        "after task-3 BUILD. Got None — env-guard still in place (pre-BUILD state)."
    )


def test_wiring_azure_cache_shared_across_chat_and_embeddings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v25 task-3: AzureADTokenProviderCache is shared (same instance) across chat + embeddings.

    The cache on app.state is wired into both AzureCompletionUpstream and
    AzureEmbeddingsProvider — one token cache, no double IDP calls.

    RIGHT-REASON RED: AzureADTokenProviderCache does not exist yet (ImportError on import),
    and the current wiring uses a per-adapter singleton AzureADTokenProvider instead.
    """
    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProviderCache  # type: ignore[attr-defined]
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)

    # Both adapters must share the SAME AzureADTokenProviderCache from app.state
    cache = getattr(app.state, "azure_ad_token_provider_cache", None)
    assert isinstance(cache, AzureADTokenProviderCache), (
        "app.state.azure_ad_token_provider_cache must be AzureADTokenProviderCache "
        "after task-3 BUILD."
    )

    embed = app.state.provider_registry.get("azure")
    chat = app.state.chat_adapters.get("azure")
    assert isinstance(embed, AzureEmbeddingsProvider)
    # Both adapters must reference the same cache instance
    assert getattr(embed, "_token_provider_cache", None) is cache, (
        "AzureEmbeddingsProvider must reference app.state.azure_ad_token_provider_cache"
    )
    assert getattr(chat, "_token_provider_cache", None) is cache, (
        "AzureCompletionUpstream must reference app.state.azure_ad_token_provider_cache"
    )
