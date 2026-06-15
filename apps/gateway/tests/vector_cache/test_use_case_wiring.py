"""RED suite — CompletionUseCase vector-cache wiring (v19 task 5 §3, Step 4.5c).

End-to-end at the use-case layer: with the vector_cache collaborator wired and exact
(+ normalization) missing, a vector hit serves the cached body ($0, cached=true, no
upstream call); a vector miss calls upstream once and fires a store; an exact hit
short-circuits the vector layer; the metric increments; default-off (no collaborator)
is byte-identical. Fails RED until the ctor kwarg + Step 4.5c + store hook are built.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.infrastructure.response_cache import build_cache_key

from .conftest import (
    MODEL_1,
    TENANT_A,
    FakeAuthenticator,
    FakeCompletionUpstream,
    FakeModelChecker,
    FakeResponseCache,
    FakeUsageRecorder,
    FakeVectorCache,
    cache_counter,
    make_body,
    make_metrics,
)

HIT_BODY: dict[str, Any] = {
    "id": "vec-hit",
    "choices": [{"message": {"role": "assistant", "content": "cached answer"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8},
}


def _make_use_case(
    *,
    response_cache: Any,
    vector_cache: Any,
) -> CompletionUseCase:
    try:
        return CompletionUseCase(
            FakeAuthenticator(),  # type: ignore[arg-type]
            FakeModelChecker(),  # type: ignore[arg-type]
            response_cache=response_cache,
            vector_cache=vector_cache,
        )
    except TypeError:
        pytest.fail("RED: CompletionUseCase has no vector_cache kwarg — build pending")


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _complete(
    uc: CompletionUseCase, up: FakeCompletionUpstream, rec: FakeUsageRecorder, **kw: Any
) -> Any:
    return await uc.complete(
        raw_key="sk-test",
        body=make_body("the capital of france"),
        upstream=up,  # type: ignore[arg-type]
        usage_recorder=rec,  # type: ignore[arg-type]
        **kw,
    )


async def test_vector_layer_off_is_byte_identical() -> None:
    # No vector_cache collaborator → today's path: exact miss → upstream → x_cache="miss".
    up = FakeCompletionUpstream()
    rec = FakeUsageRecorder()
    uc = _make_use_case(response_cache=FakeResponseCache(), vector_cache=None)
    status, body, x_cache = await _complete(uc, up, rec)
    await _settle()
    assert status == 200
    assert x_cache == "miss"
    assert up.complete_calls == [MODEL_1]  # upstream called (no vector layer)


async def test_exact_miss_then_vector_hit_bills_cached_zero() -> None:
    up = FakeCompletionUpstream()
    rec = FakeUsageRecorder()
    vec = FakeVectorCache(hit_body=HIT_BODY)
    uc = _make_use_case(response_cache=FakeResponseCache(), vector_cache=vec)
    status, body, x_cache = await _complete(uc, up, rec)
    await _settle()
    assert status == 200
    assert x_cache == "vector_hit"
    assert body == HIT_BODY
    assert up.complete_calls == []  # upstream NEVER called on a vector hit (no real bill)
    assert len(vec.lookup_calls) == 1
    assert vec.lookup_calls[0]["model"] == MODEL_1
    assert vec.lookup_calls[0]["tenant_id"] == str(TENANT_A)
    # Single-bill, $0 cached: exactly one usage record, cached=true, status 200, cached tokens.
    assert rec.call_count == 1
    assert rec.calls[0]["cached"] is True
    assert rec.calls[0]["status"] == 200
    assert rec.calls[0]["usage"]["total_tokens"] == 8


async def test_vector_miss_calls_upstream_and_stores() -> None:
    up = FakeCompletionUpstream()
    rec = FakeUsageRecorder()
    vec = FakeVectorCache(hit_body=None)  # miss
    uc = _make_use_case(response_cache=FakeResponseCache(), vector_cache=vec)
    status, body, x_cache = await _complete(uc, up, rec)
    await _settle()
    assert status == 200
    assert x_cache == "miss"
    assert up.complete_calls == [MODEL_1]  # upstream called once (the only bill)
    assert len(vec.lookup_calls) == 1
    assert len(vec.store_calls) == 1  # fire-and-forget store fired on the 200 miss
    assert vec.store_calls[0]["model"] == MODEL_1
    assert vec.store_calls[0]["tenant_id"] == str(TENANT_A)  # correct tenant forwarded
    assert vec.store_calls[0]["body"] == make_body(
        "the capital of france"
    )  # request body forwarded


async def test_exact_hit_short_circuits_vector_layer() -> None:
    # Seed the exact cache so the request hits exact → vector layer NEVER consulted.
    body = make_body("the capital of france")
    exact_key = build_cache_key(str(TENANT_A), body)
    rc = FakeResponseCache(exact={exact_key: HIT_BODY})
    up = FakeCompletionUpstream()
    rec = FakeUsageRecorder()
    vec = FakeVectorCache(hit_body=HIT_BODY)
    uc = _make_use_case(response_cache=rc, vector_cache=vec)
    status, _b, x_cache = await _complete(uc, up, rec)
    await _settle()
    assert status == 200
    assert x_cache == "hit"  # exact hit
    assert vec.lookup_calls == []  # vector layer not consulted on an exact hit
    assert up.complete_calls == []


async def test_vector_hit_metric_increments() -> None:
    up = FakeCompletionUpstream()
    rec = FakeUsageRecorder()
    metrics = make_metrics()
    vec = FakeVectorCache(hit_body=HIT_BODY)
    uc = _make_use_case(response_cache=FakeResponseCache(), vector_cache=vec)
    _s, _b, x_cache = await _complete(uc, up, rec, metrics_registry=metrics)
    await _settle()
    assert x_cache == "vector_hit"
    assert cache_counter(metrics, result="vector_hit") == 1.0
