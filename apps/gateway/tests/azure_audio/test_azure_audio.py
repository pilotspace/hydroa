"""Red suite for azure-audio: AzureOpenAIProvider STT + TTS methods.

Azure OpenAI audio (STT /audio/transcriptions, TTS /audio/speech) uses the
same OpenAI wire shape over the deployment-routed URL. This suite verifies:
  - Correct Azure deployment URL built for STT and TTS
  - Api-key vs AAD Bearer auth modes
  - Deployment map resolution (explicit + identity fallback)
  - 5xx fails closed (UpstreamUnavailableError) + breaker records error
  - Network error secret hygiene (from None → __cause__ is None)
  - STT multipart content-type is NOT forced to application/json
  - Back-compat alias: AzureEmbeddingsProvider is AzureOpenAIProvider

CONTRACT FROZEN: implements TASK.md audio-seam §3
All calls go through httpx.MockTransport — no network required.
"""

from __future__ import annotations

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import AzureCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# PRIMARY IMPORT: will be RED ("unsupported modality" / wrong class name) until built.
from gateway.proxy.infrastructure.azure_embeddings import (
    AzureEmbeddingsProvider,
    AzureOpenAIProvider,
)

# ---------------------------------------------------------------------------
# Shared test fixtures
# ---------------------------------------------------------------------------

_AZ_CRED = AzureCredential(
    mode="api_key",
    endpoint="https://myresource.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={
        "whisper-1": "prod-stt",
        "tts-1": "prod-tts",
        "text-embedding-3-small": "prod-embed",
    },
    api_key="secret-api-key",
)

_AZ_AAD_CRED = AzureCredential(
    mode="aad",
    endpoint="https://myresource.openai.azure.com",
    api_version="2024-10-21",
    deployment_map={
        "whisper-1": "prod-stt",
        "tts-1": "prod-tts",
    },
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
    handler: object,
    *,
    breaker: CircuitBreaker | None = None,
) -> AzureOpenAIProvider:
    adapter = AzureOpenAIProvider(token_provider_cache=None)
    if breaker is not None:
        adapter._breaker = breaker  # type: ignore[attr-defined]
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


# ---------------------------------------------------------------------------
# STT (post_multipart)
# ---------------------------------------------------------------------------


async def test_stt_routes_to_deployment_url() -> None:
    """api-key cred; post_multipart → correct deployment URL, api-key header, no Bearer."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        seen["authorization"] = request.headers.get("authorization")
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "hello world"})

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        status, body = await adapter.post_multipart(
            "/audio/transcriptions",
            files={"file": ("audio.mp3", b"audio-bytes", "audio/mpeg")},
            data={"model": "whisper-1", "language": "en"},
        )
    finally:
        reset_provider_credential(tok)

    assert status == 200
    assert body == {"text": "hello world"}
    assert (
        seen["url"]
        == "https://myresource.openai.azure.com/openai/deployments/prod-stt/audio/transcriptions?api-version=2024-10-21"
    ), f"unexpected URL: {seen['url']}"
    assert seen["api_key"] == "secret-api-key"
    assert seen["authorization"] is None


# ---------------------------------------------------------------------------
# TTS (stream_bytes)
# ---------------------------------------------------------------------------


async def test_tts_routes_to_speech_url() -> None:
    """api-key cred; stream_bytes → correct deployment URL, bytes streamed."""
    seen: dict[str, object] = {}
    chunks = [b"audio-", b"chunk-", b"data"]

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["api_key"] = request.headers.get("api-key")
        seen["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"".join(chunks))

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        collected = b""
        async for chunk in adapter.stream_bytes(
            "/audio/speech",
            {"model": "tts-1", "input": "Hello", "voice": "alloy"},
        ):
            collected += chunk
    finally:
        reset_provider_credential(tok)

    assert collected == b"audio-chunk-data"
    assert (
        seen["url"]
        == "https://myresource.openai.azure.com/openai/deployments/prod-tts/audio/speech?api-version=2024-10-21"
    ), f"unexpected URL: {seen['url']}"
    assert seen["api_key"] == "secret-api-key"
    assert seen["authorization"] is None


# ---------------------------------------------------------------------------
# AAD mode
# ---------------------------------------------------------------------------


async def test_aad_mode_sends_bearer() -> None:
    """AAD cred → Authorization: Bearer <tok>, no api-key header."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("authorization")
        seen["api_key"] = request.headers.get("api-key")
        return httpx.Response(200, json={"text": "hi"})

    adapter = _make_adapter(handler)

    from gateway.proxy.infrastructure.azure_ad import AzureADTokenProvider

    original_get_token = AzureADTokenProvider.get_token

    async def _fake_get_token(self: AzureADTokenProvider) -> str:
        return "tok-aad-audio"

    AzureADTokenProvider.get_token = _fake_get_token  # type: ignore[method-assign]
    try:
        tok = set_provider_credential(_AZ_AAD_CRED)
        try:
            await adapter.post_multipart(
                "/audio/transcriptions",
                files={"file": ("audio.mp3", b"bytes", "audio/mpeg")},
                data={"model": "whisper-1"},
            )
        finally:
            reset_provider_credential(tok)
    finally:
        AzureADTokenProvider.get_token = original_get_token  # type: ignore[method-assign]

    assert seen["authorization"] == "Bearer tok-aad-audio"
    assert seen["api_key"] is None


# ---------------------------------------------------------------------------
# Deployment map resolution
# ---------------------------------------------------------------------------


async def test_deployment_map_resolves() -> None:
    """deployment_map maps model → deployment in URL; unmapped model falls back to identity."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"text": "ok"})

    # Mapped: whisper-1 → prod-stt
    adapter = _make_adapter(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        await adapter.post_multipart(
            "/audio/transcriptions",
            files={"file": ("a.mp3", b"b", "audio/mpeg")},
            data={"model": "whisper-1"},
        )
    finally:
        reset_provider_credential(tok)

    assert "prod-stt" in seen_urls[0], f"mapped deployment not in URL: {seen_urls[0]}"
    assert "whisper-1" not in seen_urls[0], f"raw model in URL (should be mapped): {seen_urls[0]}"

    # Unmapped: 'custom-stt' → identity fallback → 'custom-stt' in URL
    cred_no_map = AzureCredential(
        mode="api_key",
        endpoint="https://myresource.openai.azure.com",
        api_version="2024-10-21",
        deployment_map={},
        api_key="k",
    )
    adapter2 = _make_adapter(handler)
    tok2 = set_provider_credential(cred_no_map)
    try:
        await adapter2.post_multipart(
            "/audio/transcriptions",
            files={"file": ("a.mp3", b"b", "audio/mpeg")},
            data={"model": "custom-stt"},
        )
    finally:
        reset_provider_credential(tok2)

    assert "custom-stt" in seen_urls[1], f"identity fallback not in URL: {seen_urls[1]}"


# ---------------------------------------------------------------------------
# 5xx failure + breaker + embeddings regression
# ---------------------------------------------------------------------------


async def test_stt_5xx_fails_closed() -> None:
    """STT 500 → UpstreamUnavailableError; breaker records error.
    Followed by embeddings 200 to assert regression-free (post_json still works).
    """
    spy = _SpyBreaker()
    adapter = _make_adapter(lambda r: httpx.Response(500, json={"error": "boom"}), breaker=spy)

    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.post_multipart(
                "/audio/transcriptions",
                files={"file": ("a.mp3", b"b", "audio/mpeg")},
                data={"model": "whisper-1"},
            )
    finally:
        reset_provider_credential(tok)

    assert spy.errors == 1, "breaker must record error on 5xx"
    assert spy.successes == 0

    # Regression: embeddings still works after a 5xx audio failure on a fresh adapter.
    spy2 = _SpyBreaker()
    embed_adapter = _make_adapter(
        lambda r: httpx.Response(
            200,
            json={
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        ),
        breaker=spy2,
    )
    tok2 = set_provider_credential(_AZ_CRED)
    try:
        status, body = await embed_adapter.post_json(
            "/embeddings", {"model": "text-embedding-3-small", "input": "hello"}
        )
    finally:
        reset_provider_credential(tok2)

    assert status == 200
    assert spy2.successes == 1
    assert spy2.errors == 0


# ---------------------------------------------------------------------------
# Network error + secret hygiene
# ---------------------------------------------------------------------------


async def test_network_error_secret_hygiene() -> None:
    """NetworkError → UpstreamUnavailableError; __cause__ is None (secret hygiene)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.NetworkError("connection refused")

    spy = _SpyBreaker()
    adapter = _make_adapter(handler, breaker=spy)
    tok = set_provider_credential(_AZ_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError) as exc_info:
            await adapter.post_multipart(
                "/audio/transcriptions",
                files={"file": ("a.mp3", b"b", "audio/mpeg")},
                data={"model": "whisper-1"},
            )
    finally:
        reset_provider_credential(tok)

    assert exc_info.value.__cause__ is None, "from None must suppress exception chain (secret hygiene)"
    assert "secret-api-key" not in str(exc_info.value), "api-key must not appear in error message"
    assert spy.errors == 1


# ---------------------------------------------------------------------------
# Multipart content-type check (no forced JSON)
# ---------------------------------------------------------------------------


async def test_multipart_no_forced_json_content_type() -> None:
    """STT request must use multipart/form-data, NOT application/json."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers.get("content-type", "")
        return httpx.Response(200, json={"text": "ok"})

    adapter = _make_adapter(handler)
    tok = set_provider_credential(_AZ_CRED)
    try:
        await adapter.post_multipart(
            "/audio/transcriptions",
            files={"file": ("audio.mp3", b"bytes", "audio/mpeg")},
            data={"model": "whisper-1"},
        )
    finally:
        reset_provider_credential(tok)

    ct = seen["content_type"]
    assert ct.startswith("multipart/form-data"), (
        f"Expected multipart/form-data, got: {ct!r}"
    )
    assert "application/json" not in ct


# ---------------------------------------------------------------------------
# Back-compat alias
# ---------------------------------------------------------------------------


def test_back_compat_alias() -> None:
    """AzureEmbeddingsProvider must be the same object as AzureOpenAIProvider."""
    assert AzureEmbeddingsProvider is AzureOpenAIProvider, (
        "AzureEmbeddingsProvider must be a module-level alias pointing to AzureOpenAIProvider"
    )
