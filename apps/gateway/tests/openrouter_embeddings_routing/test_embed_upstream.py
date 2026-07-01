"""Red suite for openrouter-embeddings-routing — OER3/OER4/OER5 (TASK.md §2).

OpenRouterCompletionUpstream.embed() POSTs to /embeddings via the same
auth/retry/circuit-breaker seam as complete() — payload passed through unmodified.

Fast: httpx MockTransport (no network) + a request-scoped BearerCredential.
Mirrors the pattern in tests/openrouter_generation_client/test_openrouter_generation_client.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

pytestmark = pytest.mark.asyncio

_CRED = BearerCredential(secret="sk-or-test-key")  # noqa: S106
_BASE = "https://openrouter.ai/api/v1"

_EMBED_BODY = {
    "data": [{"embedding": [0.1, 0.2, 0.3], "index": 0, "object": "embedding"}],
    "model": "google/gemini-embedding-2",
    "object": "list",
    "usage": {"prompt_tokens": 2, "total_tokens": 2},
}


def _make_adapter(handler: object, *, max_retries: int = 0) -> OpenRouterCompletionUpstream:
    adapter = OpenRouterCompletionUpstream(
        base_url=_BASE, max_retries=max_retries, backoff_base=0.0
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        base_url=_BASE,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )
    return adapter


def _counting_handler(
    responses: list[httpx.Response],
    counter: list[int],
    *,
    seen: list[httpx.Request] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        i = min(counter[0], len(responses) - 1)
        counter[0] += 1
        return responses[i]

    return handler


def _raising_handler(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


async def test_embed_posts_to_embeddings_with_auth_and_retry_seam() -> None:
    seen: list[httpx.Request] = []
    adapter = _make_adapter(
        _counting_handler([httpx.Response(200, json=_EMBED_BODY)], [0], seen=seen)
    )
    payload = {"model": "google/gemini-embedding-2", "input": "hi"}

    token = set_provider_credential(_CRED)
    try:
        status, body = await adapter.embed(payload)
    finally:
        reset_provider_credential(token)

    assert status == 200
    assert body == _EMBED_BODY
    assert len(seen) == 1
    request = seen[0]
    assert str(request.url) == f"{_BASE}/embeddings"
    assert request.headers["authorization"] == "Bearer sk-or-test-key"
    assert json.loads(request.content) == payload


async def test_embed_passes_non_200_through_unchanged() -> None:
    adapter = _make_adapter(
        _counting_handler([httpx.Response(400, json={"error": "bad model"})], [0])
    )
    token = set_provider_credential(_CRED)
    try:
        status, body = await adapter.embed({"model": "bad", "input": "hi"})
    finally:
        reset_provider_credential(token)

    assert status == 400
    assert body == {"error": "bad model"}


async def test_embed_raises_upstream_unavailable_on_network_failure() -> None:
    adapter = _make_adapter(_raising_handler(httpx.ConnectError("boom")))
    token = set_provider_credential(_CRED)
    try:
        with pytest.raises(UpstreamUnavailableError):
            await adapter.embed({"model": "x", "input": "hi"})
    finally:
        reset_provider_credential(token)
    assert adapter._breaker._failure_count == 1  # type: ignore[attr-defined]
