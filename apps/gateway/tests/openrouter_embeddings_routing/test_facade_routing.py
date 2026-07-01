"""Red suite for openrouter-embeddings-routing — OER1/OER2 (TASK.md §2).

OpenRouterUpstreamFacade.post_json must route "/embeddings" to the upstream's
embed() method, and every other path to complete() — byte-identical to today.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.infrastructure.openrouter_upstream_provider import OpenRouterUpstreamFacade

pytestmark = pytest.mark.asyncio


class _RecordingUpstream:
    """Fake CompletionUpstream (+embed) that records every call it receives."""

    def __init__(
        self, *, complete_result: tuple[int, dict[str, Any]] = (200, {"ok": True})
    ) -> None:
        self.complete_calls: list[dict[str, Any]] = []
        self.embed_calls: list[dict[str, Any]] = []
        self._complete_result = complete_result

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(payload)
        return self._complete_result

    async def embed(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.embed_calls.append(payload)
        return (200, {"data": [{"embedding": [0.1, 0.2], "index": 0, "object": "embedding"}]})

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        raise NotImplementedError("not exercised by these scenarios")


async def test_facade_routes_embeddings_path_to_embed() -> None:
    upstream = _RecordingUpstream()
    facade = OpenRouterUpstreamFacade(upstream=upstream)
    payload = {"model": "google/gemini-embedding-2", "input": "hi"}

    status, body = await facade.post_json("/embeddings", payload)

    assert upstream.embed_calls == [payload]
    assert upstream.complete_calls == []
    assert status == 200
    assert body["data"][0]["embedding"] == [0.1, 0.2]


async def test_facade_routes_other_paths_to_complete() -> None:
    upstream = _RecordingUpstream(complete_result=(200, {"id": "chatcmpl-1"}))
    facade = OpenRouterUpstreamFacade(upstream=upstream)
    payload = {"model": "x", "messages": []}

    result = await facade.post_json("/chat/completions", payload)

    assert upstream.complete_calls == [payload]
    assert upstream.embed_calls == []
    assert result == (200, {"id": "chatcmpl-1"})
