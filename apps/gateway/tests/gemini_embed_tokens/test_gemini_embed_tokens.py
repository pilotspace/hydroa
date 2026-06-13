"""Red suite for gemini-embed-tokens (v12): exact Gemini-embed token billing.

Proves the GoogleEmbeddingsProvider replaces the chars/4 ESTIMATE with a real
Gemini :countTokens count, with a fail-SAFE fallback to the estimate when the
count leg fails. All HTTP behavior uses httpx.MockTransport — no network, no real key.

Contract: gemini-embed-tokens TASK.md §3 (FROZEN @ v1).
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.gemini_upstream import (
    GoogleEmbeddingsProvider,
    _gemini_embed_to_openai,
)

pytestmark = pytest.mark.anyio

_BASE = "https://gemini.test/v1beta"
_LOGGER_NAME = "gateway.proxy.infrastructure.gemini_upstream"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _embed_provider(handler: Any) -> GoogleEmbeddingsProvider:
    provider = GoogleEmbeddingsProvider(api_key="g-key", base_url=_BASE)
    provider._client = httpx.AsyncClient(base_url=_BASE, transport=httpx.MockTransport(handler))  # type: ignore[attr-defined,arg-type]
    return provider


def _count_response(total: int) -> httpx.Response:
    return httpx.Response(200, json={"totalTokens": total})


# ---------------------------------------------------------------------------
# Pure helper — exact_tokens overrides the estimate; None keeps the fallback
# ---------------------------------------------------------------------------


def test_helper_exact_tokens_overrides_estimate() -> None:
    out = _gemini_embed_to_openai(
        {"embedding": {"values": [0.1]}}, "m", "hello", exact_tokens=42
    )
    assert out["usage"] == {"prompt_tokens": 42, "total_tokens": 42}


def test_helper_none_falls_back_to_estimate() -> None:
    # 8 chars total → ceil(8/4) == 2 (the documented fallback path)
    out = _gemini_embed_to_openai(
        {"embeddings": [{"values": [1.0]}, {"values": [2.0]}]}, "m", ["abcd", "efgh"], exact_tokens=None
    )
    assert out["usage"] == {"prompt_tokens": 2, "total_tokens": 2}


# ---------------------------------------------------------------------------
# Provider — exact count via :countTokens
# ---------------------------------------------------------------------------


async def test_single_input_exact_count() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            return _count_response(3)
        assert request.url.path.endswith(":embedContent")
        return httpx.Response(200, json={"embedding": {"values": [0.1, 0.2]}})

    provider = _embed_provider(handler)
    status, body = await provider.post_json("/embeddings", {"model": "text-embedding-004", "input": "hello"})
    assert status == 200
    # exact count 3, NOT ceil(len("hello")/4) == 2
    assert body["usage"] == {"prompt_tokens": 3, "total_tokens": 3}
    assert body["data"] == [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}]
    assert body["model"] == "text-embedding-004"


async def test_batch_input_exact_count_aggregate() -> None:
    count_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count_calls
        if request.url.path.endswith(":countTokens"):
            count_calls += 1
            return _count_response(7)
        assert request.url.path.endswith(":batchEmbedContents")
        return httpx.Response(200, json={"embeddings": [{"values": [1.0]}, {"values": [2.0]}]})

    provider = _embed_provider(handler)
    status, body = await provider.post_json("/embeddings", {"model": "text-embedding-004", "input": ["a", "bb"]})
    assert status == 200
    assert body["usage"]["prompt_tokens"] == 7
    assert body["data"] == [
        {"object": "embedding", "index": 0, "embedding": [1.0]},
        {"object": "embedding", "index": 1, "embedding": [2.0]},
    ]
    # exactly ONE extra countTokens round-trip (aggregate, not one-per-input)
    assert count_calls == 1


# ---------------------------------------------------------------------------
# Fail-safe fallback to estimate
# ---------------------------------------------------------------------------


async def test_counttokens_5xx_falls_back_to_estimate(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(503, json={"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})
        return httpx.Response(200, json={"embedding": {"values": [0.1]}})

    provider = _embed_provider(handler)
    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        status, body = await provider.post_json("/embeddings", {"model": "m", "input": "hello"})
    assert status == 200
    # fall back to estimate ceil(5/4) == 2; request NOT failed
    assert body["usage"] == {"prompt_tokens": max(1, math.ceil(len("hello") / 4)), "total_tokens": max(1, math.ceil(len("hello") / 4))}
    assert any("estimate" in r.message.lower() for r in caplog.records)


async def test_counttokens_missing_totaltokens_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            return httpx.Response(200, json={})  # no totalTokens
        return httpx.Response(200, json={"embedding": {"values": [0.1]}})

    provider = _embed_provider(handler)
    status, body = await provider.post_json("/embeddings", {"model": "m", "input": "hello"})
    assert status == 200
    assert body["usage"]["prompt_tokens"] == max(1, math.ceil(len("hello") / 4))


async def test_counttokens_timeout_falls_back() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            raise httpx.ConnectTimeout("count timed out")
        return httpx.Response(200, json={"embedding": {"values": [0.1]}})

    provider = _embed_provider(handler)
    status, body = await provider.post_json("/embeddings", {"model": "m", "input": "hi"})
    assert status == 200
    assert body["usage"]["prompt_tokens"] == max(1, math.ceil(len("hi") / 4))


# ---------------------------------------------------------------------------
# Security + ordering invariants
# ---------------------------------------------------------------------------


async def test_counttokens_uses_x_goog_api_key_no_query() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(":countTokens"):
            seen["headers"] = dict(request.headers)
            seen["url"] = str(request.url)
            return _count_response(5)
        return httpx.Response(200, json={"embedding": {"values": [0.1]}})

    provider = _embed_provider(handler)
    await provider.post_json("/embeddings", {"model": "m", "input": "x"})
    assert seen["headers"].get("x-goog-api-key") == "g-key"
    assert "key=" not in seen["url"]
    assert "g-key" not in seen["url"]


async def test_embed_5xx_raises_and_skips_count_leg() -> None:
    count_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal count_calls
        if request.url.path.endswith(":countTokens"):
            count_calls += 1
            return _count_response(3)
        return httpx.Response(503, json={"error": {"code": 503, "message": "x", "status": "UNAVAILABLE"}})

    provider = _embed_provider(handler)
    with pytest.raises(UpstreamUnavailableError):
        await provider.post_json("/embeddings", {"model": "m", "input": "x"})
    # count leg runs ONLY after a successful embed
    assert count_calls == 0
