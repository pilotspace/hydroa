"""Failing-first (RED) suite for bandwidth-token-bucket (§3 CONTRACT FROZEN @ v1).

One test per §2 SCENARIO. The bucket is a per-key_id aggregate token-bucket: atomic
Lua refill+consume keyed `bandwidth:bucket:{key_id}` (+ `_ts` companion), bounded-wait
`acquire`, estimate→truth `reconcile`, fail-open on any Redis error, default-OFF.

Right-reason red BEFORE build: the symbols under test do not exist yet —
`gateway.rate_limits.infrastructure.redis_token_bucket` / `.domain.errors.BandwidthExhaustedError`
import → ModuleNotFoundError / ImportError.

Determinism: a controllable clock (epoch-ms, injected via the constructor) drives refill
math, and `asyncio.sleep` is patched to ADVANCE that clock instead of sleeping wall-time,
so the pacing loop is exact and fast. The Lua runs against REAL Redis (db 7) — the now_ms
is passed as an ARGV, so refill is deterministic without real elapsed time.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from gateway.rate_limits.application.passthrough import PassthroughBandwidthBucket
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.infrastructure.redis_token_bucket import RedisTokenBucket

_REDIS_URL = "redis://localhost:6380/7"  # dedicated db index — no collision with other suites
# asyncio_mode=auto (pyproject) runs the async tests without an explicit mark.


class _Clock:
    """Mutable epoch-ms clock; advance() simulates elapsed time deterministically."""

    def __init__(self, start_ms: float = 1_000_000.0) -> None:
        self.now_ms = start_ms

    def __call__(self) -> float:
        return self.now_ms

    def advance_s(self, seconds: float) -> None:
        self.now_ms += seconds * 1000.0


@pytest_asyncio.fixture
async def redis_client() -> AsyncIterator[Any]:
    client = aioredis.from_url(_REDIS_URL, decode_responses=False)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def patch_sleep(monkeypatch: pytest.MonkeyPatch, clock: _Clock):
    """Patch asyncio.sleep so the pacing loop advances the injected clock, not wall-time."""
    import asyncio

    async def _fake_sleep(seconds: float, *a: Any, **k: Any) -> None:
        clock.advance_s(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return clock


def _bucket(redis_client: Any, clock: _Clock, *, rate: float, burst: float) -> RedisTokenBucket:
    return RedisTokenBucket(redis=redis_client, rate=rate, burst=burst, clock=clock)


def _kid() -> uuid.UUID:
    return uuid.uuid4()


# ── grant when capacity ──────────────────────────────────────────────────────
async def test_grant_when_capacity(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    # full bucket: seed level by reconciling a no-op or rely on first-touch = full.
    grant = await b.acquire(kid, estimated_tokens=50, max_wait_s=5.0)
    assert grant.consumed == 50
    assert grant.waited_s == pytest.approx(0.0, abs=1e-6)
    assert await b.level(kid) == 150


# ── refill accrues over elapsed time ─────────────────────────────────────────
async def test_refill_accrues_over_time(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    await b.try_consume(kid, 200)  # drain to 0 (first touch = full 200, consume 200)
    assert await b.level(kid) == 0
    clock.advance_s(1.0)  # +100 tokens
    res = await b.try_consume(kid, 80)
    assert res.granted is True
    assert await b.level(kid) == 20  # 100 refilled - 80 consumed


# ── refill never exceeds burst ───────────────────────────────────────────────
async def test_refill_caps_at_burst(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    await b.try_consume(kid, 0)  # first touch persists level=200, ts=now
    clock.advance_s(10.0)  # would add 1000, but cap holds
    res = await b.try_consume(kid, 0)
    assert res.granted is True
    assert await b.level(kid) == 200


# ── pace then grant within the wait budget ───────────────────────────────────
async def test_pace_then_grant_within_budget(redis_client: Any, patch_sleep: _Clock) -> None:
    clock = patch_sleep
    b = _bucket(redis_client, clock, rate=100, burst=100)
    kid = _kid()
    await b.try_consume(kid, 100)  # empty
    grant = await b.acquire(kid, estimated_tokens=50, max_wait_s=5.0)
    assert grant.consumed == 50
    # needed 50 tokens at 100/s → ~0.5s of (simulated) waiting
    assert grant.waited_s == pytest.approx(0.5, abs=0.1)


# ── bounded wait exhausted → raise (REJECTION) ───────────────────────────────
async def test_bounded_wait_exhausted_raises(redis_client: Any, patch_sleep: _Clock) -> None:
    clock = patch_sleep
    b = _bucket(redis_client, clock, rate=10, burst=10)
    kid = _kid()
    await b.try_consume(kid, 10)  # empty
    assert await b.level(kid) == 0  # precondition: drained
    with pytest.raises(BandwidthExhaustedError) as exc:
        await b.acquire(kid, estimated_tokens=1000, max_wait_s=2.0)
    assert exc.value.retry_after_s == math.ceil((1000 - 0) / 10)  # ~100s
    # a refused acquire performs NO consume — the level is exactly unchanged (still 0)
    assert await b.level(kid) == 0


# ── zero/negative estimate = immediate free grant ────────────────────────────
async def test_zero_estimate_immediate_grant(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    await b.try_consume(kid, 0)  # establish level=200
    grant = await b.acquire(kid, estimated_tokens=0, max_wait_s=5.0)
    assert grant.consumed == 0
    assert grant.waited_s == pytest.approx(0.0, abs=1e-6)
    assert await b.level(kid) == 200


# ── reconcile under-estimate refunds ─────────────────────────────────────────
async def test_reconcile_underestimate_refunds(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    grant = await b.acquire(kid, estimated_tokens=50, max_wait_s=5.0)  # level 150
    await b.reconcile(kid, grant, real_tokens=30)  # refund 20
    assert await b.level(kid) == 170


# ── reconcile over-estimate carries negative debt (floored at −burst) ────────
async def test_reconcile_overestimate_carries_debt(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    await b.try_consume(kid, 190)  # level 10
    grant = await b.acquire(kid, estimated_tokens=0, max_wait_s=5.0)
    grant_consumed_50 = type(grant)(key_id=kid, consumed=50, waited_s=0.0)
    await b.reconcile(kid, grant_consumed_50, real_tokens=80)  # debit extra 30
    assert await b.level(kid) == -20


async def test_reconcile_debt_floored_at_neg_burst(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=200)
    kid = _kid()
    await b.try_consume(kid, 200)  # level 0
    grant = await b.acquire(kid, estimated_tokens=0, max_wait_s=5.0)
    huge = type(grant)(key_id=kid, consumed=0, waited_s=0.0)
    await b.reconcile(kid, huge, real_tokens=10_000)  # would be -10000
    assert await b.level(kid) == -200  # floored at -burst


# ── aggregate atomicity across concurrent callers ────────────────────────────
async def test_aggregate_two_callers_share_key(redis_client: Any, clock: _Clock) -> None:
    import asyncio

    b = _bucket(redis_client, clock, rate=100, burst=100)
    kid = _kid()
    await b.try_consume(kid, 0)  # full 100
    r1, r2 = await asyncio.gather(b.try_consume(kid, 60), b.try_consume(kid, 60))
    granted = [r1.granted, r2.granted]
    assert granted.count(True) == 1  # exactly one wins — atomic, no over-admission
    assert await b.level(kid) == 40  # 100 - 60, never -20


# ── Redis error fails open ───────────────────────────────────────────────────
async def test_redis_error_fails_open(redis_client: Any, clock: _Clock) -> None:
    b = _bucket(redis_client, clock, rate=100, burst=100)
    kid = _kid()

    async def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("redis down")

    # Break the registered Lua script — every method must fail-open (admit).
    b._bucket_script = _boom  # type: ignore[attr-defined]
    grant = await b.acquire(kid, estimated_tokens=50, max_wait_s=5.0)
    assert grant.consumed == 50
    assert grant.waited_s == pytest.approx(0.0, abs=1e-6)


# ── disabled (rate=0) admits without touching Redis ──────────────────────────
async def test_disabled_rate_zero_admits(clock: _Clock) -> None:
    class _ExplodingRedis:
        def __getattr__(self, _name: str) -> Any:
            raise AssertionError("redis must not be touched when rate=0")

        def register_script(self, _src: str) -> Any:
            return lambda *a, **k: (_ for _ in ()).throw(AssertionError("no redis"))

    b = RedisTokenBucket(redis=_ExplodingRedis(), rate=0, burst=0, clock=clock)
    grant = await b.acquire(_kid(), estimated_tokens=999, max_wait_s=5.0)
    assert grant.consumed == 999
    assert grant.waited_s == pytest.approx(0.0, abs=1e-6)


# ── passthrough bucket always admits ─────────────────────────────────────────
async def test_passthrough_bucket_always_admits() -> None:
    b = PassthroughBandwidthBucket()
    grant = await b.acquire(_kid(), estimated_tokens=10_000, max_wait_s=0.0)
    assert grant.consumed == 10_000
    assert grant.waited_s == pytest.approx(0.0, abs=1e-6)


# ── config knobs default-OFF and coerce negatives ────────────────────────────
def test_config_knobs_default_off_and_coerce_negatives() -> None:
    from gateway.core.config import Settings

    s = Settings()
    assert s.bandwidth_tokens_per_sec == 0  # default OFF
    assert s.bandwidth_burst_tokens == 0
    assert s.bandwidth_max_wait_seconds == 0.0

    coerced = Settings(
        bandwidth_tokens_per_sec=-5,
        bandwidth_burst_tokens=-9,
        bandwidth_max_wait_seconds=-1.0,
    )
    assert coerced.bandwidth_tokens_per_sec == 0
    assert coerced.bandwidth_burst_tokens == 0
    assert coerced.bandwidth_max_wait_seconds == 0.0
