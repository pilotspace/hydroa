"""RedisVectorCache.lookup fetches all candidates in ONE mget, not N per-candidate gets.

hydroa-envoy-top3 #3 — the lookup loop issued one `redis.get()` per candidate id (up to
max_candidates=100), an N+1 of sequential Redis round trips on the semantic-cache hot path.
It now issues a single `mget` for the whole candidate batch. This unit test pins the
round-trip contract with a call-counting fake; end-to-end hit/miss correctness against a
real Redis is covered by tests/semantic_cache/.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gateway.proxy.infrastructure.vector_cache import RedisVectorCache, _namespace

_TENANT = "t1"
_MODEL = "gpt-x"
_NS = _namespace(_TENANT, _MODEL)


class _CountingRedis:
    """Minimal async Redis stub that counts get vs mget round trips."""

    def __init__(self, ids: list[bytes], entries: dict[str, bytes], bodies: dict[str, bytes]) -> None:
        self._ids = ids
        self._entries = entries
        self._bodies = bodies
        self.get_calls = 0
        self.mget_calls = 0
        self.mget_batch_sizes: list[int] = []

    async def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        return self._ids[start : stop + 1]

    async def mget(self, keys: list[str]) -> list[bytes | None]:
        self.mget_calls += 1
        self.mget_batch_sizes.append(len(keys))
        return [self._entries.get(k) for k in keys]

    async def get(self, key: str) -> bytes | None:
        self.get_calls += 1
        return self._bodies.get(key)


def _entry(vec: list[float], pointer: str) -> bytes:
    return json.dumps({"v": vec, "k": pointer}).encode()


@pytest.mark.asyncio
async def test_lookup_uses_single_mget_not_per_candidate_get() -> None:
    ids = [b"a", b"b", b"c"]
    entries = {
        f"{_NS}:a": _entry([1.0, 0.0], "body:a"),  # identical to query → best
        f"{_NS}:b": _entry([0.0, 1.0], "body:b"),
        f"{_NS}:c": _entry([0.5, 0.5], "body:c"),
    }
    bodies = {"body:a": json.dumps({"hit": True}).encode()}
    redis = _CountingRedis(ids, entries, bodies)

    async def _embedder(_text: str) -> list[float]:
        return [1.0, 0.0]

    cache = RedisVectorCache(redis, embedder=_embedder, threshold=0.8, max_candidates=100)
    result = await cache.lookup(
        tenant_id=_TENANT, model=_MODEL, body={"messages": [{"role": "user", "content": "hi"}]}
    )

    assert result == {"hit": True}
    # Candidates fetched in ONE mget of the whole batch...
    assert redis.mget_calls == 1
    assert redis.mget_batch_sizes == [3]
    # ...and get() is used only for the single final body pointer, never per-candidate.
    assert redis.get_calls == 1


@pytest.mark.asyncio
async def test_lookup_no_candidates_is_a_clean_miss_without_mget() -> None:
    redis = _CountingRedis([], {}, {})

    async def _embedder(_text: str) -> list[float]:
        return [1.0, 0.0]

    cache = RedisVectorCache(redis, embedder=_embedder, threshold=0.8, max_candidates=100)
    result = await cache.lookup(
        tenant_id=_TENANT, model=_MODEL, body={"messages": [{"role": "user", "content": "hi"}]}
    )
    assert result is None
    assert redis.get_calls == 0  # nothing to fetch
