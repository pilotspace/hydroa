"""RED suite — RedisVectorCache.lookup / .store (v19 task 5 §3).

The embedding-similarity layer: embed the last user message, scan a bounded
per-(tenant,model) Redis index, serve the pointed exact body when the best cosine
is >= threshold. Tenant + model isolated; fail-safe (embed/redis failure -> MISS /
no-op); dangling pointer -> MISS; index capped at max_candidates. Round-trips are
driven through store()+lookup() (behavior, not internal key formats). Fails RED
until proxy/infrastructure/vector_cache.py is built.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from gateway.proxy.infrastructure.response_cache import build_cache_key

from .conftest import (
    MODEL_1,
    MODEL_2,
    TENANT_A,
    TENANT_B,
    V_A,
    V_A_NEAR,
    V_B,
    FakeEmbedder,
    FakeRedis,
    make_body,
)

try:
    from gateway.proxy.infrastructure.vector_cache import RedisVectorCache

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

CACHED_BODY: dict[str, Any] = {
    "id": "cached-1",
    "choices": [{"message": {"role": "assistant", "content": "Paris"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
}


def _make_cache(redis: FakeRedis, embedder: FakeEmbedder, **kw: Any) -> Any:
    if not _AVAILABLE:
        pytest.fail("RED: RedisVectorCache not yet implemented — build pending")
    params: dict[str, Any] = {"threshold": 0.95, "max_candidates": 100}
    params.update(kw)
    return RedisVectorCache(redis, embedder=embedder, **params)


def _index_key(tenant: str, model: str) -> str:
    return f"vec-cache:{tenant}:{hashlib.sha256(model.encode()).hexdigest()}:idx"


async def _seed_entry(
    redis: FakeRedis,
    cache: Any,
    *,
    tenant: str,
    model: str,
    text: str,
    body: dict[str, Any] = CACHED_BODY,
) -> None:
    """Seed one cached entry: the exact body (as use_cases would) + the vector pointer."""
    base = make_body(text, model=model)
    exact_key = build_cache_key(tenant, base)
    await redis.set(exact_key, json.dumps(body), ex=300)  # the body use_cases stores
    await cache.store(tenant_id=tenant, model=model, body=base, response_body=body, ttl=300)


async def test_above_threshold_returns_pointed_body() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"capital of france": V_A, "the capital of france": V_A_NEAR})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="capital of france")

    hit = await cache.lookup(
        tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("the capital of france")
    )
    assert hit == CACHED_BODY  # cosine(V_A, V_A_NEAR) ≈ 0.98 >= 0.95 → HIT


async def test_below_threshold_returns_none() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"capital of france": V_A, "weather tomorrow": V_B})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="capital of france")

    miss = await cache.lookup(
        tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("weather tomorrow")
    )
    assert miss is None  # cosine 0.0 < 0.95


async def test_tenant_isolation_misses() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="q")

    miss = await cache.lookup(tenant_id=str(TENANT_B), model=MODEL_1, body=make_body("q"))
    assert miss is None  # identical vector, different tenant namespace


async def test_model_isolation_misses() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="q")

    miss = await cache.lookup(
        tenant_id=str(TENANT_A), model=MODEL_2, body=make_body("q", model=MODEL_2)
    )
    assert miss is None  # identical vector, different model namespace


async def test_embedder_none_returns_none_no_scan() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="q")

    emb.fail = True  # embedder now returns None
    miss = await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("q"))
    assert miss is None  # fail-safe: no embedding → MISS


async def test_redis_error_returns_none() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text="q")

    redis.fail = True  # every redis op now raises
    miss = await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("q"))
    assert miss is None  # fail-safe: swallow redis error → MISS


async def test_dangling_pointer_returns_none() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    base = make_body("q")
    exact_key = build_cache_key(str(TENANT_A), base)
    await redis.set(exact_key, json.dumps(CACHED_BODY), ex=300)
    await cache.store(
        tenant_id=str(TENANT_A), model=MODEL_1, body=base, response_body=CACHED_BODY, ttl=300
    )
    # Simulate the exact body expiring (LRU eviction) while the vector entry remains.
    redis.kv.pop(exact_key.encode("utf-8"), None)

    miss = await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("q"))
    assert miss is None  # candidate >= threshold but pointer dangles → MISS


async def test_no_user_message_is_noop_no_embed_call() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({"q": V_A})
    cache = _make_cache(redis, emb)
    body = {"model": MODEL_1, "messages": [{"role": "system", "content": "be terse"}]}

    miss = await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=body)
    assert miss is None
    assert emb.calls == []  # no user message → no embed call at all (no-op)


async def test_store_embedder_none_is_noop() -> None:
    redis = FakeRedis()
    emb = FakeEmbedder({}, fail=True)
    cache = _make_cache(redis, emb)
    await cache.store(
        tenant_id=str(TENANT_A),
        model=MODEL_1,
        body=make_body("q"),
        response_body=CACHED_BODY,
        ttl=300,
    )
    # No vector entry registered when the embedder yields nothing.
    assert await redis.lrange(_index_key(str(TENANT_A), MODEL_1), 0, -1) == []


async def test_store_registers_and_caps_index_at_max_candidates() -> None:
    redis = FakeRedis()
    vectors = {
        "p0": [1.0, 0.0, 0.0, 0.0],
        "p1": [0.0, 1.0, 0.0, 0.0],
        "p2": [0.0, 0.0, 1.0, 0.0],
        "p3": [0.0, 0.0, 0.0, 1.0],
    }
    emb = FakeEmbedder(vectors)
    cache = _make_cache(redis, emb, max_candidates=3)
    for text in ("p0", "p1", "p2", "p3"):  # 4 distinct prompts, cap is 3
        await _seed_entry(redis, cache, tenant=str(TENANT_A), model=MODEL_1, text=text)

    ids = await redis.lrange(_index_key(str(TENANT_A), MODEL_1), 0, -1)
    assert len(ids) == 3  # oldest (p0) trimmed

    # Behavioral: the oldest prompt is no longer retrievable, the newest is.
    assert await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("p0")) is None
    assert (
        await cache.lookup(tenant_id=str(TENANT_A), model=MODEL_1, body=make_body("p3"))
        == CACHED_BODY
    )
