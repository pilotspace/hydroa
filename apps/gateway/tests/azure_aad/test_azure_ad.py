"""Red suite for azure-aad-auth (v21 task 4): Azure AD client-credentials token auth.

The genuinely-new auth sub-system: AzureADTokenProvider (acquire + cache + refresh +
single-flight + fail-closed) and the async auth-header seam on AzureCompletionUpstream.
HTTP behavior via httpx.MockTransport (token endpoint + chat endpoint); time via an
injected clock. No network.

CONTRACT (FROZEN @ v1): azure-aad-auth TASK.md §3
  - AzureADConfig (frozen; client_secret repr-hidden) + resolve_azure_ad_config.
  - AzureADTokenProvider.get_token(): cache/refresh/single-flight/FAIL-CLOSED.
  - AzureCompletionUpstream(token_provider=…): Bearer when set, api-key when None.
  - GATEWAY_AZURE_CLIENT_SECRET boot-guard.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from gateway.core.config import EmptyUpstreamKeyError, validate_upstream_keys
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.azure_config import AzureConfig

# RED: azure_ad does not exist yet → ModuleNotFoundError.
from gateway.proxy.infrastructure.azure_ad import (
    AzureADConfig,
    AzureADTokenProvider,
    resolve_azure_ad_config,
)
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream

_AD_CFG = AzureADConfig(
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="top-secret",
)

_AZ_CFG = AzureConfig(
    api_key="api-key-fallback",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
)


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _make_provider(
    handler: object, *, clock: _Clock | None = None, skew: float = 60.0
) -> AzureADTokenProvider:
    provider = AzureADTokenProvider(
        config=_AD_CFG,
        now_fn=clock or _Clock(),
        expiry_skew_s=skew,
    )
    provider._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return provider


def _token_handler(hits: list[int], *, token: str = "tok-1", expires_in: int = 3600):
    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(
            200, json={"access_token": f"{token}-{hits[0]}", "expires_in": expires_in}
        )

    return handler


async def test_token_acquired_once_and_cached() -> None:
    hits = [0]
    provider = _make_provider(_token_handler(hits))
    a = await provider.get_token()
    b = await provider.get_token()
    assert hits[0] == 1
    assert a == b == "tok-1-1"


async def test_token_refreshes_after_expiry() -> None:
    hits = [0]
    clock = _Clock(0.0)
    provider = _make_provider(_token_handler(hits, expires_in=3600), clock=clock, skew=60.0)
    first = await provider.get_token()
    clock.t = 3600.0  # past expiry - skew
    second = await provider.get_token()
    assert hits[0] == 2
    assert first != second


async def test_concurrent_refresh_single_flight() -> None:
    hits = [0]
    provider = _make_provider(_token_handler(hits))
    results = await asyncio.gather(*[provider.get_token() for _ in range(8)])
    assert hits[0] == 1
    assert set(results) == {"tok-1-1"}


async def test_token_failure_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid_client"})

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()
    # nothing cached → a second call tries again (still fails)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()


async def test_client_secret_not_in_repr_or_error() -> None:
    assert "top-secret" not in repr(_AD_CFG)

    def handler(request: httpx.Request) -> httpx.Response:
        # the secret was sent in the request body; the error must not echo it back
        return httpx.Response(400, json={"error": "bad", "sent": request.content.decode()})

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await provider.get_token()
    assert "top-secret" not in str(exc.value)


async def test_token_timeout_secret_not_in_exception_chain() -> None:
    # The httpx exception carries the request body (incl. client_secret) at
    # exc.__cause__.request.content — get_token() must suppress the chain (from None).
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("idp unreachable")

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await provider.get_token()
    assert exc.value.__cause__ is None
    assert "top-secret" not in str(exc.value)


async def test_non_json_200_fails_closed() -> None:
    # A 200 with a non-JSON body (e.g. a corporate proxy login page) must fail closed
    # as UpstreamUnavailableError — never leak a JSONDecodeError past the auth seam.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>proxy login</html>")

    provider = _make_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.get_token()


class _FakeProvider:
    def __init__(self, token: str) -> None:
        self._token = token

    async def get_token(self) -> str:
        return self._token


def _make_adapter(handler: object, *, token_provider: object | None) -> AzureCompletionUpstream:
    adapter = AzureCompletionUpstream(
        config=_AZ_CFG,
        token_provider=token_provider,  # type: ignore[arg-type]
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def test_adapter_uses_bearer_with_token_provider() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    adapter = _make_adapter(handler, token_provider=_FakeProvider("tok-123"))
    await adapter.complete({"model": "gpt-4o", "messages": []})
    assert seen["authorization"] == "Bearer tok-123"
    assert seen["api_key"] is None


async def test_adapter_keeps_api_key_without_provider() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    adapter = _make_adapter(handler, token_provider=None)
    await adapter.complete({"model": "gpt-4o", "messages": []})
    assert seen["authorization"] is None
    assert seen["api_key"] == "api-key-fallback"


def test_resolve_ad_config_gates_on_all_three() -> None:
    from types import SimpleNamespace

    base = {
        "azure_tenant_id": "t",
        "azure_client_id": "c",
        "azure_client_secret": "",
        "azure_ad_scope": "",
    }
    assert resolve_azure_ad_config(SimpleNamespace(**base)) is None
    full = {**base, "azure_client_secret": "s"}
    assert resolve_azure_ad_config(SimpleNamespace(**full)) is not None


def test_empty_client_secret_fails_boot() -> None:
    with pytest.raises(EmptyUpstreamKeyError) as exc:
        validate_upstream_keys({"GATEWAY_AZURE_CLIENT_SECRET": ""})
    assert "GATEWAY_AZURE_CLIENT_SECRET" in str(exc.value)


def test_wiring_aad_precedence_enables_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "azure" in app.state.chat_adapters
