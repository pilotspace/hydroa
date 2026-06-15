"""RED suite — embedding-response cache on EmbeddingsUseCase (v19 task 4 §3).

The use case exists; the NEW cache kwargs (response_cache / cache_ttl_seconds /
request_headers) + the 3-tuple return do not. `_run(...)` pytest.fail()s (clean RED)
until BUILD adds them. Billing ($0 on hit), tenant isolation, default-off, and
fail-to-MISS paths are asserted.

Run ONLY this suite:
  cd apps/gateway && uv run pytest tests/cache_controls/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.proxy.application.embeddings_use_case import EmbeddingsUseCase

from .conftest import (
    EMBED_RESPONSE_200,
    TENANT_A,
    FakeCache,
    FakeGovernance,
    FakeProvider,
    FakeRegistry,
    FakeSession,
    SpyRecorder,
    make_payload,
)

try:
    from gateway.proxy.infrastructure.response_cache import (  # noqa: F401
        build_embedding_cache_key,
    )

    _KEY_AVAILABLE = True
except ImportError:
    _KEY_AVAILABLE = False


def _embed_key(tenant: str, payload: dict[str, Any]) -> str:
    if not _KEY_AVAILABLE:
        pytest.fail("RED: build_embedding_cache_key not yet implemented — build pending")
    from gateway.proxy.infrastructure.response_cache import build_embedding_cache_key

    return build_embedding_cache_key(tenant, payload)


async def _settle() -> None:
    # Let fire-and-forget record / cache-set tasks run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _run(
    use_case: EmbeddingsUseCase,
    *,
    body: dict[str, Any],
    registry: Any,
    recorder: SpyRecorder,
    response_cache: Any = None,
    cache_ttl_seconds: int = 300,
    request_headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any], str | None]:
    try:
        return await use_case.execute(
            raw_key="sk-test",
            body=body,
            registry=registry,
            usage_recorder=recorder,
            response_cache=response_cache,
            cache_ttl_seconds=cache_ttl_seconds,
            request_headers=request_headers,
        )
    except TypeError as exc:
        pytest.fail(
            f"RED: EmbeddingsUseCase cache kwargs not yet implemented — build pending ({exc})"
        )


def _uc(gov: FakeGovernance, session: FakeSession) -> EmbeddingsUseCase:
    return EmbeddingsUseCase(governance=gov, session=session)


async def test_miss_forwards_stores_bills_once() -> None:
    provider = FakeProvider(200)
    cache = FakeCache()
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(cache_enabled=True), FakeSession())
    result = await _run(
        uc,
        body=make_payload(),
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result == (200, EMBED_RESPONSE_200, "miss")
    assert provider.call_count == 1
    assert len(cache.set_calls) == 1
    assert cache.set_calls[0][1] == EMBED_RESPONSE_200
    assert rec.call_count == 1
    assert rec.calls[0].get("cached") is not True


async def test_hit_serves_zero_no_upstream() -> None:
    provider = FakeProvider(200)
    cache = FakeCache()
    payload = make_payload()
    cache.seed(_embed_key(str(TENANT_A), payload), EMBED_RESPONSE_200)
    rec = SpyRecorder()
    session = FakeSession()
    uc = _uc(FakeGovernance(tenant_id=TENANT_A, cache_enabled=True), session)
    result = await _run(
        uc,
        body=payload,
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result == (200, EMBED_RESPONSE_200, "hit")
    assert provider.call_count == 0
    assert session.execute_calls == 0  # HIT skips the catalog query
    assert rec.call_count == 1
    assert rec.calls[0].get("cached") is True


async def test_tenant_isolation() -> None:
    from .conftest import TENANT_B

    provider = FakeProvider(200)
    cache = FakeCache()
    payload = make_payload()
    cache.seed(_embed_key(str(TENANT_A), payload), EMBED_RESPONSE_200)
    rec = SpyRecorder()
    # Tenant B issues the identical request → must MISS (distinct key) and call upstream.
    uc = _uc(FakeGovernance(tenant_id=TENANT_B, cache_enabled=True), FakeSession())
    result = await _run(
        uc,
        body=payload,
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result[2] == "miss"
    assert provider.call_count == 1


async def test_non_200_not_cached() -> None:
    from .conftest import EMBED_ERROR_400

    provider = FakeProvider(400, EMBED_ERROR_400)
    cache = FakeCache()
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(cache_enabled=True), FakeSession())
    result = await _run(
        uc,
        body=make_payload(),
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result[0] == 400
    assert cache.set_calls == []  # non-200 is never stored


async def test_no_cache_bypass() -> None:
    provider = FakeProvider(200)
    cache = FakeCache()
    payload = make_payload()
    cache.seed(_embed_key(str(TENANT_A), payload), EMBED_RESPONSE_200)
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(tenant_id=TENANT_A, cache_enabled=True), FakeSession())
    result = await _run(
        uc,
        body=payload,
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={"cache-control": "no-cache"},
    )
    await _settle()
    assert result[2] == "bypass"
    assert provider.call_count == 1  # bypass → upstream called despite seeded entry
    assert cache.set_calls == []  # no-cache → not stored


async def test_disabled_byte_identical() -> None:
    provider = FakeProvider(200)
    cache = FakeCache()
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(cache_enabled=False), FakeSession())
    result = await _run(
        uc,
        body=make_payload(),
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result == (200, EMBED_RESPONSE_200, None)  # no x_cache semantics when disabled
    assert provider.call_count == 1
    assert cache.set_calls == []


async def test_get_failure_degrades_to_miss() -> None:
    provider = FakeProvider(200)
    cache = FakeCache(always_miss=True)  # get() returns None (swallowed-error contract)
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(cache_enabled=True), FakeSession())
    result = await _run(
        uc,
        body=make_payload(),
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        request_headers={},
    )
    await _settle()
    assert result[0] == 200  # request never fails on a cache miss/error
    assert provider.call_count == 1


async def test_store_honors_max_age_ttl() -> None:
    provider = FakeProvider(200)
    cache = FakeCache()
    rec = SpyRecorder()
    uc = _uc(FakeGovernance(cache_enabled=True), FakeSession())
    await _run(
        uc,
        body=make_payload(),
        registry=FakeRegistry(provider),
        recorder=rec,
        response_cache=cache,
        cache_ttl_seconds=60,
        request_headers={},
    )
    await _settle()
    assert len(cache.set_calls) == 1
    assert cache.set_calls[0][2] == 60  # resolved per-request TTL honored on store


def test_settings_cache_max_ttl_default() -> None:
    from gateway.core.config import Settings

    assert getattr(Settings(), "cache_max_ttl_seconds", None) == 86400
