"""Red suite for azure-embeddings (v21 task 5): Azure OpenAI embeddings provider.

Azure is OpenAI-compatible, so this adapter is a thin passthrough: deployment-routed
URL + api-version + the shared api-key/AAD auth seam, with ZERO body/response
translation (unlike BedrockEmbeddingsProvider). HTTP via httpx.MockTransport; no network.

CONTRACT (FROZEN @ v1): azure-embeddings TASK.md §3
  - AzureEmbeddingsProvider implements UpstreamProvider (post_json/post_multipart/stream_bytes).
  - post_json: resolve_deployment → build_url(dep, "embeddings") → POST payload unchanged →
    (status, resp.json()) unchanged; 5xx/Timeout/Network → UpstreamUnavailableError; 4xx pass-through.
  - _auth_headers: Bearer when token_provider set, api-key otherwise (shared AAD seam).
  - create_app registers _providers["azure"] iff Azure config present.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# RED: azure_embeddings does not exist yet → ModuleNotFoundError.
from gateway.proxy.infrastructure.azure_embeddings import AzureEmbeddingsProvider

_AZ_CFG = AzureConfig(
    api_key="api-key-secret",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"text-embedding-3-small": "prod-embed"},
)


class _FakeProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token


class _RaisingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        raise UpstreamUnavailableError("Azure AD token request failed")


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
    handler: object, *, token_provider: object | None = None, breaker: CircuitBreaker | None = None
) -> AzureEmbeddingsProvider:
    adapter = AzureEmbeddingsProvider(
        config=_AZ_CFG,
        token_provider=token_provider,  # type: ignore[arg-type]
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
    status, body = await adapter.post_json(
        "/embeddings", {"model": "text-embedding-3-small", "input": "hello"}
    )

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
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    adapter = _make_adapter(handler, token_provider=_FakeProvider("tok-xyz"))
    await adapter.post_json("/embeddings", {"model": "m", "input": ["a", "b"]})

    assert seen["authorization"] == "Bearer tok-xyz"
    assert seen["api_key"] is None


async def test_identity_deployment_for_unmapped_model() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    cfg = AzureConfig(
        api_key="k",
        endpoint="https://r.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
    )
    adapter = AzureEmbeddingsProvider(config=cfg)
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    await adapter.post_json("/embeddings", {"model": "my-embed", "input": "x"})

    assert "/openai/deployments/my-embed/embeddings?api-version=2024-10-21" in str(seen["url"])
    assert '"model":"my-embed"' in str(seen["body"]).replace(" ", "")


async def test_upstream_500_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    with pytest.raises(UpstreamUnavailableError):
        await adapter.post_json("/embeddings", {"model": "m", "input": "x"})
    # 5xx is an upstream outage → the breaker must record the failure (resilience seam).
    assert spy.errors == 1
    assert spy.successes == 0


async def test_4xx_content_filter_passes_through() -> None:
    err = {"error": {"code": "content_filter", "message": "blocked"}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=err)

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    status, body = await adapter.post_json("/embeddings", {"model": "m", "input": "x"})
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
    with pytest.raises(UpstreamUnavailableError) as exc:
        await adapter.post_json("/embeddings", {"model": "m", "input": "x"})
    assert exc.value.__cause__ is None
    assert "api-key-secret" not in str(exc.value)
    assert spy.errors == 1
    assert spy.successes == 0


async def test_success_records_breaker_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    status, _ = await adapter.post_json("/embeddings", {"model": "m", "input": "x"})
    assert status == 200
    assert spy.successes == 1
    assert spy.errors == 0


async def test_token_failure_fails_closed_no_post() -> None:
    hits = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(200, json={"object": "list", "data": [], "usage": {}})

    raiser = _RaisingProvider()
    adapter = _make_adapter(handler, token_provider=raiser)
    with pytest.raises(UpstreamUnavailableError):
        await adapter.post_json("/embeddings", {"model": "m", "input": "x"})
    assert raiser.calls == 1
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


def test_wiring_registers_azure_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_OPENROUTER_API_KEY",
        "GATEWAY_OPENAI_API_KEY",
        "GATEWAY_ANTHROPIC_API_KEY",
        "GATEWAY_GOOGLE_API_KEY",
        "GATEWAY_AZURE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        azure_endpoint="https://r.openai.azure.com",
        azure_api_key="azure-key",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert isinstance(app.state.provider_registry.get("azure"), AzureEmbeddingsProvider)


def test_wiring_absent_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_OPENROUTER_API_KEY",
        "GATEWAY_OPENAI_API_KEY",
        "GATEWAY_ANTHROPIC_API_KEY",
        "GATEWAY_GOOGLE_API_KEY",
        "GATEWAY_AZURE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert app.state.provider_registry.get("azure") is None


def test_wiring_aad_only_shares_token_provider_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    # FINDING 4/5: under AAD-only config (no api-key) the embeddings provider must be
    # registered AND must reuse the SAME AzureADTokenProvider instance as the chat adapter
    # (one token cache — no double IDP calls / divergent refresh locks).
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_OPENROUTER_API_KEY",
        "GATEWAY_OPENAI_API_KEY",
        "GATEWAY_ANTHROPIC_API_KEY",
        "GATEWAY_GOOGLE_API_KEY",
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
        azure_endpoint="https://r.openai.azure.com",
        azure_tenant_id="t",
        azure_client_id="c",
        azure_client_secret="s",
    )  # type: ignore[arg-type]
    app = create_app(settings)

    embed = app.state.provider_registry.get("azure")
    chat = app.state.chat_adapters["azure"]
    assert isinstance(embed, AzureEmbeddingsProvider)
    # SAME shared instance — identity, not just equality.
    assert embed._token_provider is chat._token_provider
    assert embed._token_provider is not None
