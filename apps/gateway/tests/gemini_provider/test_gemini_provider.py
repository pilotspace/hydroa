"""Red suite for gemini-provider (v9 task 3/4) — TASK.md §4.

Tests the Google Gemini provider on BOTH seams:
  - chat: GeminiCompletionUpstream (CompletionUpstream) → generateContent /
    streamGenerateContent, OpenAI⇄Gemini translation incl. terminal usage frame.
  - embeddings: GoogleEmbeddingsProvider (v7 UpstreamProvider) → embedContent /
    batchEmbedContents, order-preserving, with an estimated usage.

Auth is the API key via the x-goog-api-key HEADER (never a ?key= query param).
All HTTP behavior uses httpx.MockTransport — no network, no real key.

Contract: TASK.md §3 (FROZEN @ v1).
"""

from __future__ import annotations

import json
import math
from collections.abc import AsyncIterator

import httpx
import pytest

# RED until BUILD creates the module/symbols.
from gateway.proxy.domain.credential_context import reset_provider_credential, set_provider_credential
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream, UpstreamProvider
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    GoogleEmbeddingsProvider,
    _gemini_embed_to_openai,
    _gemini_to_openai,
    _map_gemini_finish_reason,
    _openai_to_gemini_request,
)
from gateway.usage.domain.extractor import extract_usage_from_sse
from tests import _redis_env

pytestmark = pytest.mark.asyncio

_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Shared test credential — credential-resolution-seam BUILD conversion.
_TEST_GOOGLE_SECRET = "g-key"
_TEST_BEARER_CRED = BearerCredential(secret=_TEST_GOOGLE_SECRET)

_GEMINI_CHAT_200 = {
    "candidates": [
        {
            "content": {"parts": [{"text": "Hello world"}], "role": "model"},
            "finishReason": "STOP",
            "index": 0,
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 8,
        "candidatesTokenCount": 4,
        "totalTokenCount": 12,
    },
}

_GEMINI_SSE = (
    'data: {"candidates":[{"content":{"parts":[{"text":"Hello"}],"role":"model"}}]}\n\n'
    'data: {"candidates":[{"content":{"parts":[{"text":" world"}],"role":"model"}}]}\n\n'
    'data: {"candidates":[{"content":{"parts":[{"text":""}],"role":"model"},"finishReason":"STOP"}],'
    '"usageMetadata":{"promptTokenCount":8,"candidatesTokenCount":4,"totalTokenCount":12}}\n\n'
).encode()


def _chat_adapter(handler: object) -> GeminiCompletionUpstream:
    # Credential-resolution-seam BUILD: api_key removed; credential via contextvar.
    adapter = GeminiCompletionUpstream(base_url=_BASE, default_max_tokens=4096)
    adapter._client = httpx.AsyncClient(base_url=_BASE, transport=httpx.MockTransport(handler))  # type: ignore[attr-defined,arg-type]
    return adapter


def _embed_provider(handler: object) -> GoogleEmbeddingsProvider:
    # Credential-resolution-seam BUILD: api_key removed; credential via contextvar.
    provider = GoogleEmbeddingsProvider(base_url=_BASE)
    provider._client = httpx.AsyncClient(base_url=_BASE, transport=httpx.MockTransport(handler))  # type: ignore[attr-defined,arg-type]
    return provider


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


# ---------------------------------------------------------------------------
# Pure translation helpers — chat
# ---------------------------------------------------------------------------


def test_chat_request_translation() -> None:
    body = _openai_to_gemini_request(
        {
            "model": "gemini-1.5-flash",
            "messages": [
                {"role": "system", "content": "S"},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "prev"},
            ],
            "max_tokens": 64,
            "temperature": 0.5,
        },
        default_max_tokens=4096,
    )
    assert body["systemInstruction"] == {"parts": [{"text": "S"}]}
    assert body["contents"] == [
        {"role": "user", "parts": [{"text": "Hi"}]},
        {"role": "model", "parts": [{"text": "prev"}]},
    ]
    assert body["generationConfig"]["maxOutputTokens"] == 64
    assert body["generationConfig"]["temperature"] == 0.5


def test_chat_max_tokens_defaulted() -> None:
    body = _openai_to_gemini_request(
        {"model": "g", "messages": [{"role": "user", "content": "x"}]},
        default_max_tokens=4096,
    )
    assert body["generationConfig"]["maxOutputTokens"] == 4096


def test_chat_response_translation() -> None:
    out = _gemini_to_openai(_GEMINI_CHAT_200, model="gemini-1.5-flash")
    assert out["object"] == "chat.completion"
    assert out["model"] == "gemini-1.5-flash"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "Hello world"}
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12}


def test_chat_finish_reason_mapping() -> None:
    assert _map_gemini_finish_reason("STOP") == "stop"
    assert _map_gemini_finish_reason("MAX_TOKENS") == "length"
    assert _map_gemini_finish_reason("SAFETY") == "content_filter"
    assert _map_gemini_finish_reason("RECITATION") == "stop"
    assert _map_gemini_finish_reason(None) == "stop"
    assert _map_gemini_finish_reason("OTHER") == "stop"


# ---------------------------------------------------------------------------
# Chat adapter HTTP behavior
# ---------------------------------------------------------------------------


async def test_auth_header_x_goog_api_key_no_query() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_GEMINI_CHAT_200)

    adapter = _chat_adapter(handler)
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        await adapter.complete({"model": "gemini-1.5-flash", "messages": [{"role": "user", "content": "hi"}]})
    finally:
        reset_provider_credential(token)
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers.get("x-goog-api-key") == _TEST_GOOGLE_SECRET
    assert "key=" not in str(seen["url"])


async def test_chat_complete_translates() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith(":generateContent")
        sent = json.loads(request.content)
        assert sent["systemInstruction"] == {"parts": [{"text": "S"}]}
        return httpx.Response(200, json=_GEMINI_CHAT_200)

    adapter = _chat_adapter(handler)
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, body = await adapter.complete(
            {
                "model": "gemini-1.5-flash",
                "messages": [{"role": "system", "content": "S"}, {"role": "user", "content": "Hi"}],
                "max_tokens": 64,
            }
        )
    finally:
        reset_provider_credential(token)
    assert status == 200
    assert body["choices"][0]["message"]["content"] == "Hello world"
    assert body["usage"]["total_tokens"] == 12


async def test_chat_stream_translation() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert ":streamGenerateContent" in request.url.path
        assert request.url.params.get("alt") == "sse"
        return httpx.Response(200, content=_GEMINI_SSE, headers={"content-type": "text/event-stream"})

    adapter = _chat_adapter(handler)
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        chunks = await _drain(adapter.stream({"model": "gemini-1.5-flash", "messages": [{"role": "user", "content": "hi"}]}))
    finally:
        reset_provider_credential(token)
    joined = b"".join(chunks)
    assert b'"role": "assistant"' in joined or b'"role":"assistant"' in joined
    assert b"Hello" in joined and b" world" in joined
    assert chunks[-1] == b"data: [DONE]\n\n"
    assert extract_usage_from_sse(chunks) == {
        "prompt_tokens": 8,
        "completion_tokens": 4,
        "total_tokens": 12,
    }


async def test_chat_5xx_raises() -> None:
    adapter = _chat_adapter(lambda req: httpx.Response(503, json={"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}}))
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.complete({"model": "g", "messages": [{"role": "user", "content": "x"}]})
    finally:
        reset_provider_credential(token)


async def test_chat_4xx_error_passthrough() -> None:
    adapter = _chat_adapter(
        lambda req: httpx.Response(400, json={"error": {"code": 400, "message": "bad", "status": "INVALID_ARGUMENT"}})
    )
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, body = await adapter.complete({"model": "g", "messages": [{"role": "user", "content": "x"}]})
    finally:
        reset_provider_credential(token)
    assert status == 400
    assert body == {"error": {"message": "bad", "type": "invalid_argument", "code": "invalid_argument"}}


async def test_chat_satisfies_protocol() -> None:
    adapter = _chat_adapter(lambda req: httpx.Response(200, json=_GEMINI_CHAT_200))
    assert isinstance(adapter, CompletionUpstream)


# ---------------------------------------------------------------------------
# Embeddings provider
# ---------------------------------------------------------------------------


async def test_embeddings_single_embedcontent() -> None:
    # v12 supersession: billing is now the EXACT :countTokens count, not chars/4.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 3})
        assert request.url.path.endswith(":embedContent")
        sent = json.loads(request.content)
        assert sent == {"content": {"parts": [{"text": "hello"}]}}
        assert request.headers.get("x-goog-api-key") == _TEST_GOOGLE_SECRET
        return httpx.Response(200, json={"embedding": {"values": [0.1, 0.2]}})

    provider = _embed_provider(handler)
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, body = await provider.post_json("/embeddings", {"model": "text-embedding-004", "input": "hello"})
    finally:
        reset_provider_credential(token)
    assert status == 200
    assert body["object"] == "list"
    assert body["model"] == "text-embedding-004"
    assert body["data"] == [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]
    assert "usage" in body
    assert body["usage"]["prompt_tokens"] == 3  # exact count (v12), not ceil(5/4)


async def test_embeddings_batch_preserves_order() -> None:
    # v12 supersession: a :countTokens leg now runs after a successful batch embed.
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={"totalTokens": 4})
        assert request.url.path.endswith(":batchEmbedContents")
        sent = json.loads(request.content)
        assert [r["content"]["parts"][0]["text"] for r in sent["requests"]] == ["a", "bb"]
        return httpx.Response(200, json={"embeddings": [{"values": [1.0]}, {"values": [2.0]}]})

    provider = _embed_provider(handler)
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, body = await provider.post_json("/embeddings", {"model": "text-embedding-004", "input": ["a", "bb"]})
    finally:
        reset_provider_credential(token)
    assert status == 200
    assert body["data"] == [
        {"object": "embedding", "index": 0, "embedding": [1.0]},
        {"object": "embedding", "index": 1, "embedding": [2.0]},
    ]
    assert body["usage"]["prompt_tokens"] == 4  # exact aggregate count (v12)


def test_embeddings_usage_estimate_helper() -> None:
    out = _gemini_embed_to_openai({"embeddings": [{"values": [1.0]}, {"values": [2.0]}]}, "m", ["abcd", "efgh"])
    # 8 chars total → ceil(8/4) == 2
    assert out["usage"] == {"prompt_tokens": 2, "total_tokens": 2}


async def test_embeddings_5xx_raises() -> None:
    provider = _embed_provider(lambda req: httpx.Response(503, json={"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}}))
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await provider.post_json("/embeddings", {"model": "m", "input": "x"})
    finally:
        reset_provider_credential(token)


async def test_embeddings_4xx_error_passthrough() -> None:
    provider = _embed_provider(
        lambda req: httpx.Response(403, json={"error": {"code": 403, "message": "no", "status": "PERMISSION_DENIED"}})
    )
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        status, body = await provider.post_json("/embeddings", {"model": "m", "input": "x"})
    finally:
        reset_provider_credential(token)
    assert status == 403
    assert body == {"error": {"message": "no", "type": "permission_denied", "code": "permission_denied"}}


async def test_embeddings_unsupported_modalities_raise() -> None:
    provider = _embed_provider(lambda req: httpx.Response(200, json={}))
    token = set_provider_credential(_TEST_BEARER_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await provider.post_multipart("/audio/transcriptions", {}, {})
        with pytest.raises(UpstreamUnavailableError):
            await _drain(provider.stream_bytes("/audio/speech", {}))
    finally:
        reset_provider_credential(token)


async def test_embeddings_provider_satisfies_protocol() -> None:
    provider = _embed_provider(lambda req: httpx.Response(200, json={}))
    assert isinstance(provider, UpstreamProvider)


# ---------------------------------------------------------------------------
# Composition-root wiring
# ---------------------------------------------------------------------------


def _make_settings(**kwargs: object):  # type: ignore[no-untyped-def]
    from gateway.core.config import Settings

    defaults: dict[str, object] = {
        "database_url": _redis_env.TEST_DATABASE_URL,
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": _redis_env.TEST_REDIS_URL,
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_wiring_google_present_when_key_set() -> None:
    # Credential-resolution-seam BUILD: google adapter is UNCONDITIONAL —
    # no api_key at boot; Settings no longer has google_api_key field.
    from gateway.main import create_app

    app = create_app(_make_settings())
    assert isinstance(app.state.chat_adapters["google"], GeminiCompletionUpstream)
    assert isinstance(app.state.provider_registry.get("google"), GoogleEmbeddingsProvider)


def test_wiring_google_absent_when_key_empty() -> None:
    # Credential-resolution-seam BUILD: registration is UNCONDITIONAL; adapters are
    # always present regardless of env keys (per-tenant key gating at resolve time).
    # Converted: assert both ARE present (not absent).
    from gateway.main import create_app

    app = create_app(_make_settings())
    assert "google" in app.state.chat_adapters, (
        "google chat adapter must be registered unconditionally after credential-resolution-seam BUILD"
    )
    assert app.state.provider_registry.get("google") is not None, (
        "google embeddings provider must be registered unconditionally after credential-resolution-seam BUILD"
    )
