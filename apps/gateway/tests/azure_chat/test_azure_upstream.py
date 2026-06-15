"""Red suite for azure-chat (v21 task 2): AzureCompletionUpstream.

OpenAI-shaped passthrough chat adapter with two Azure deltas — `api-key` header
(not Bearer) + per-request deployment URL. HTTP behavior is exercised via
httpx.MockTransport (no network), mirroring tests/bedrock_provider.

CONTRACT (FROZEN @ v1): azure-chat TASK.md §3
  - AzureCompletionUpstream(*, config, max_retries=0, backoff_base=0.5,
    retry_deadline_s=0.0, metrics_registry=None) impl CompletionUpstream.
  - complete: 200/4xx → (status, body) verbatim; 5xx/timeout → UpstreamUnavailableError.
  - stream: NotImplementedError (task 3 implements).
  - main.py registers _chat_adapters["azure"] iff resolve_azure_config(settings).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from gateway.proxy.application.fallback_triggers import classify_fallback_trigger
from gateway.proxy.domain.errors import UpstreamUnavailableError

# RED: azure_upstream does not exist yet → ModuleNotFoundError.
from gateway.proxy.infrastructure.azure_config import AzureConfig
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream

_CFG = AzureConfig(
    api_key="secret-az-key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
)

_PAYLOAD: dict[str, object] = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "hi"}],
}


def _make_adapter(handler: object, *, max_retries: int = 0) -> AzureCompletionUpstream:
    """Construct the adapter, then swap its _client for a MockTransport-backed one."""
    adapter = AzureCompletionUpstream(
        config=_CFG,
        max_retries=max_retries,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


async def test_routes_to_deployment_url_with_api_key() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    adapter = _make_adapter(handler)
    await adapter.complete(_PAYLOAD)
    assert seen["url"] == (
        "https://r.openai.azure.com/openai/deployments/prod-4o/chat/completions"
        "?api-version=2024-10-21"
    )
    assert seen["api_key"] == "secret-az-key"
    assert seen["authorization"] is None


async def test_200_passthrough_exact_usage() -> None:
    usage = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"id": "chatcmpl-1", "object": "chat.completion", "usage": usage}
        )

    adapter = _make_adapter(handler)
    status, body = await adapter.complete(_PAYLOAD)
    assert status == 200
    assert body["usage"] == usage


async def test_content_filter_400_passthrough_and_classifies() -> None:
    err_body = {
        "error": {
            "code": "content_filter",
            "message": "The response was filtered due to Azure OpenAI's content management policy.",
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=err_body)

    adapter = _make_adapter(handler)
    status, body = await adapter.complete(_PAYLOAD)
    assert status == 400
    assert body == err_body
    assert classify_fallback_trigger(status, body) == "content_policy"


async def test_5xx_raises_upstream_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    adapter = _make_adapter(handler)
    with pytest.raises(UpstreamUnavailableError):
        await adapter.complete(_PAYLOAD)


async def test_api_key_not_in_url() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    adapter = _make_adapter(handler)
    await adapter.complete(_PAYLOAD)
    assert "secret-az-key" not in str(seen["url"])
    assert seen["api_key"] == "secret-az-key"


# NOTE: test_stream_not_implemented (the task-2 stub guard) was retired by
# azure-streaming-passthrough (task 3), which implements stream() — see
# tests/azure_streaming/test_azure_streaming.py for the real streaming coverage.


def test_wiring_registers_azure_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
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

    def _settings(**over: object) -> Settings:
        base: dict[str, object] = {
            "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            "jwt_secret": "test-secret-not-for-production-0123456789",
            "redis_url": "redis://localhost:6380/9",
            "environment": "test",
        }
        base.update(over)
        return Settings(**base)  # type: ignore[arg-type]

    app_on = create_app(
        _settings(azure_api_key="sk-az", azure_endpoint="https://r.openai.azure.com")
    )
    assert "azure" in app_on.state.chat_adapters

    app_off = create_app(_settings())
    assert "azure" not in app_off.state.chat_adapters
