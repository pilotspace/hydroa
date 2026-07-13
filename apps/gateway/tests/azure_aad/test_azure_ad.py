"""Red suite for azure-aad-auth (v21 task 4): Azure AD client-credentials token auth.

The genuinely-new auth sub-system: AzureADTokenProvider (acquire + cache + refresh +
single-flight + fail-closed) and the async auth-header seam on AzureCompletionUpstream.
HTTP behavior via httpx.MockTransport (token endpoint + chat endpoint); time via an
injected clock. No network.

CONTRACT (FROZEN @ v1): azure-aad-auth TASK.md §3
  - AzureADConfig (frozen; client_secret repr-hidden).
  - AzureADTokenProvider.get_token(): cache/refresh/single-flight/FAIL-CLOSED.
  - AzureCompletionUpstream reads AzureCredential from contextvar (task-3).

v25 task-3 amendment:
  - resolve_azure_ad_config is DELETED (env-secret removal §6); its tests are REMOVED.
  - GATEWAY_AZURE_CLIENT_SECRET boot-guard test is REMOVED (field deleted from Settings).
  - _make_adapter now constructs AzureCompletionUpstream via contextvar (task-3 ctor).
  - test_wiring_aad_precedence_enables_without_api_key updated: asserts unconditional wiring.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.core.egress_policy import AllowAllEgressPolicy
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential

# azure_ad still exists (AzureADConfig + AzureADTokenProvider remain; resolve_azure_ad_config deleted).
from gateway.proxy.infrastructure.azure_ad import (
    AzureADConfig,
    AzureADTokenProvider,
)
from gateway.proxy.infrastructure.azure_upstream import AzureCompletionUpstream
from tests import _redis_env

_AD_CFG = AzureADConfig(
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="top-secret",
)

# v25 task-3: AzureCredential (aad mode) used for contextvar-based adapter tests.
_AZ_AAD_CRED = AzureCredential(
    mode="aad",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
    tenant_id="tenant-1",
    client_id="client-1",
    client_secret="top-secret",
)

_AZ_API_KEY_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://r.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={"gpt-4o": "prod-4o"},
    api_key="api-key-fallback",
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


class _FailingProvider:
    """A provider whose get_token always fails closed (AAD mint unreachable)."""

    async def get_token(self) -> str:
        raise UpstreamUnavailableError("Azure AD token request failed")


class _FakeCache:
    """Stub AzureADTokenProviderCache that returns a fixed provider."""

    def __init__(self, provider: object) -> None:
        self._provider = provider

    def get_or_create(self, config: object, tenant_id: object | None = None) -> object:
        return self._provider


def _make_adapter(
    handler: object,
    *,
    cred: AzureCredential,
    fake_cache: object | None = None,
) -> AzureCompletionUpstream:
    """Construct task-3 adapter (no ctor config/token_provider), swap _client.

    v25 task-3: AzureCompletionUpstream no longer takes config= or token_provider=.
    It gains token_provider_cache= and reads AzureCredential from the contextvar.
    The caller must set/reset the contextvar around .complete() calls.

    RIGHT-REASON RED: existing ctor still requires config= → TypeError until BUILD.
    """
    adapter = AzureCompletionUpstream(  # type: ignore[call-arg]
        token_provider_cache=fake_cache,  # type: ignore[arg-type]
        backoff_base=0.0,
        retry_deadline_s=0.0,
        egress_policy=AllowAllEgressPolicy(),
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


async def test_adapter_uses_bearer_with_token_provider() -> None:
    """AzureCredential (aad mode) in contextvar → Bearer token from cache.

    v25 task-3: token_provider sourced from AzureADTokenProviderCache.get_or_create(),
    not from ctor token_provider= arg.
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    cache = _FakeCache(_FakeProvider("tok-123"))
    adapter = _make_adapter(handler, cred=_AZ_AAD_CRED, fake_cache=cache)
    tok = set_provider_credential(_AZ_AAD_CRED)
    try:
        await adapter.complete({"model": "gpt-4o", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert seen["authorization"] == "Bearer tok-123"
    assert seen["api_key"] is None


async def test_adapter_keeps_api_key_without_provider() -> None:
    """AzureCredential (api_key mode) in contextvar → api-key header, no Bearer.

    v25 task-3: api_key sourced from AzureCredential.api_key in contextvar.
    """
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"id": "x", "usage": {}})

    adapter = _make_adapter(handler, cred=_AZ_API_KEY_CRED, fake_cache=None)
    tok = set_provider_credential(_AZ_API_KEY_CRED)
    try:
        await adapter.complete({"model": "gpt-4o", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert seen["authorization"] is None
    assert seen["api_key"] == "api-key-fallback"


async def test_adapter_aad_mint_failure_fails_closed_no_post() -> None:
    """§2-R3 (chat): an AAD mint failure on complete() fails closed.

    The token mint raises UpstreamUnavailableError → the adapter must propagate it
    and send NO upstream request: no api-key fallback, no unauthenticated/blank-auth
    POST. Mirrors azure_embeddings::test_token_failure_fails_closed_no_post for the
    chat path (AzureCompletionUpstream._auth_headers_for_credential).
    """
    hits = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(200, json={"id": "x", "usage": {}})

    cache = _FakeCache(_FailingProvider())
    adapter = _make_adapter(handler, cred=_AZ_AAD_CRED, fake_cache=cache)
    tok = set_provider_credential(_AZ_AAD_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.complete({"model": "gpt-4o", "messages": []})
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 0  # no POST → no api-key fallback, no unauthenticated request


async def test_adapter_aad_mint_failure_fails_closed_no_post_on_stream() -> None:
    """§2-R3 (chat stream): an AAD mint failure on stream() fails closed.

    stream() resolves the credential + guards the breaker synchronously, then awaits
    the auth header INSIDE the generator — a mint failure must raise on the first
    iteration with NO bytes streamed and NO upstream request sent.
    """
    hits = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        hits[0] += 1
        return httpx.Response(200, content=b"data: {}\n\n")

    cache = _FakeCache(_FailingProvider())
    adapter = _make_adapter(handler, cred=_AZ_AAD_CRED, fake_cache=cache)
    tok = set_provider_credential(_AZ_AAD_CRED)
    try:
        gen = adapter.stream({"model": "gpt-4o", "messages": []})
        with pytest.raises(UpstreamUnavailableError):
            async for _ in gen:
                pass
    finally:
        reset_provider_credential(tok)
    assert hits[0] == 0  # no POST → no api-key fallback, no unauthenticated request


# resolve_azure_ad_config and GATEWAY_AZURE_CLIENT_SECRET boot-guard tests REMOVED —
# v25 task-3 §6: resolve_azure_ad_config is DELETED and azure_client_secret field
# is REMOVED from Settings. These tests are retired per deliverable C.


def test_wiring_azure_unconditional(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app() with NO Azure env creds → 'azure' still in app.state.chat_adapters.

    v25 task-3: azure is registered unconditionally (env-guard removed).

    RIGHT-REASON RED: current wiring gates 'azure' on resolve_azure_config(settings)
    being truthy — absent without env creds → assertion fails.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    for name in (
        "GATEWAY_AZURE_API_KEY",
        "GATEWAY_AZURE_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings(
        database_url=_redis_env.TEST_DATABASE_URL,
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url=_redis_env.TEST_REDIS_URL,
        environment="test",
        # No azure_api_key, no azure_client_secret, no azure_endpoint
    )  # type: ignore[arg-type]
    app = create_app(settings)
    assert "azure" in app.state.chat_adapters, (
        "'azure' must be in app.state.chat_adapters UNCONDITIONALLY after task-3 BUILD. "
        "It is absent — the env-guard is still in place (pre-BUILD state)."
    )
