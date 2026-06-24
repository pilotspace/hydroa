"""RedisTokenBucket — per-key_id aggregate token-bucket with bounded-wait pacing.

Contract FROZEN @ bandwidth-token-bucket TASK.md §3 v1.

Design (mirrors RedisLuaRateLimiter):
  - Atomic REFILL-THEN-CONSUME in ONE Lua EVALSHA (no read-modify-write race across workers).
    Key shapes (FROZEN):
      bandwidth:bucket:{key_id}      FLOAT  level (may be negative down to -burst)
      bandwidth:bucket_ts:{key_id}   FLOAT  last-refill epoch ms
    First touch ⇒ full bucket (level = burst). TTL = ceil(burst/rate) + 60s.
  - acquire(): bounded-wait loop over try_consume — paces with asyncio.sleep(needed/rate)
    until granted or the cumulative wait would exceed max_wait_s (→ BandwidthExhaustedError).
    The wait budget IS the timeout (PROJECT.md IO invariant — never an unbounded hang).
  - reconcile(): apply the signed delta (grant.consumed - real_tokens); refund/debit, clamped
    to [-burst, burst]. Does NOT refill (it is a correction to a prior grant, not a fresh read).
  - FAIL-OPEN: any Redis/Lua error ⇒ ADMIT (granted) + log WARNING with key_id ONLY (never
    secrets). reconcile/level errors swallowed.
  - DISABLED: rate <= 0 ⇒ acquire/try_consume immediate-grant without touching Redis (the wired
    default-OFF path uses PassthroughBandwidthBucket; this is belt-and-suspenders).

Constructor does NOT connect to Redis (register_script only builds a Script object) — safe to
call without lifespan, same as RedisLuaRateLimiter / RedisDeploymentLoadGate.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid as _uuid_mod
from collections.abc import Callable
from typing import Any

from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthGrant, ConsumeResult

_log = logging.getLogger(__name__)


# ── Refill + conditional-consume (atomic) ───────────────────────────────────
# KEYS[1]=level key  KEYS[2]=ts key
# ARGV: now_ms, rate, burst, tokens, ttl_s
# Returns {granted(0|1), level(string float), retry_after_s(int)}.
_BUCKET_LUA = """
local level_key = KEYS[1]
local ts_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local burst = tonumber(ARGV[3])
local tokens = tonumber(ARGV[4])
local ttl_s = tonumber(ARGV[5])

local level = tonumber(redis.call('GET', level_key))
local ts = tonumber(redis.call('GET', ts_key))
if level == nil then level = burst end
if ts == nil then ts = now_ms end

local elapsed = now_ms - ts
if elapsed < 0 then elapsed = 0 end
level = level + (elapsed / 1000.0) * rate
if level > burst then level = burst end

local granted = 0
local retry_after = 0
if level >= tokens then
    level = level - tokens
    granted = 1
else
    local needed = tokens - level
    retry_after = math.ceil(needed / rate)
    if retry_after < 1 then retry_after = 1 end
end

redis.call('SET', level_key, tostring(level))
redis.call('SET', ts_key, tostring(now_ms))
redis.call('EXPIRE', level_key, ttl_s)
redis.call('EXPIRE', ts_key, ttl_s)

return {granted, tostring(level), retry_after}
"""

# ── Reconcile (signed delta correction; no refill) ──────────────────────────
# KEYS[1]=level key
# ARGV: burst, delta (consumed-real), ttl_s
# Returns level (string float).
_RECONCILE_LUA = """
local level_key = KEYS[1]
local burst = tonumber(ARGV[1])
local delta = tonumber(ARGV[2])
local ttl_s = tonumber(ARGV[3])

local level = tonumber(redis.call('GET', level_key))
if level == nil then level = burst end
level = level + delta
if level > burst then level = burst end
if level < -burst then level = -burst end

redis.call('SET', level_key, tostring(level))
redis.call('EXPIRE', level_key, ttl_s)
return tostring(level)
"""

_PFX_LEVEL = "bandwidth:bucket:"
_PFX_TS = "bandwidth:bucket_ts:"
# Pacing granularity floor — never sleep less than this per slice (avoids a busy-loop on
# sub-millisecond waits while staying well under any realistic pacing budget).
_MIN_SLEEP_S = 0.001


class RedisTokenBucket:
    """Concrete BandwidthBucket backed by Redis Lua scripts.

    Args:
        redis: redis.asyncio client (typed Any — redis-py ships no strict-compatible stubs).
        rate: refill tokens/second (the per-key ceiling). rate <= 0 ⇒ disabled (immediate grant).
        burst: bucket capacity (max level). When rate > 0 and burst <= 0, defaults to rate.
        clock: epoch-MS callable (injected for deterministic tests); defaults to time.time()*1000.
        ttl_s: key TTL; defaults to ceil(burst/rate) + 60s when omitted.
    """

    def __init__(
        self,
        *,
        redis: Any,
        rate: float,
        burst: float,
        clock: Callable[[], float] | None = None,
        ttl_s: int | None = None,
    ) -> None:
        self._redis: Any = redis
        self._rate = float(rate)
        if self._rate > 0 and burst <= 0:
            burst = rate
        self._burst = float(burst)
        self._clock: Callable[[], float] = clock or (lambda: time.time() * 1000.0)
        if ttl_s is not None:
            self._ttl_s = ttl_s
        elif self._rate > 0:
            self._ttl_s = math.ceil(self._burst / self._rate) + 60
        else:
            self._ttl_s = 60
        # register_script only builds a Script object — no Redis round-trip (safe without lifespan).
        # Skipped when disabled so a rate=0 bucket never touches the client at all.
        self._bucket_script = redis.register_script(_BUCKET_LUA) if self._rate > 0 else None
        self._reconcile_script = redis.register_script(_RECONCILE_LUA) if self._rate > 0 else None

    # ------------------------------------------------------------------ acquire
    async def acquire(
        self, key_id: _uuid_mod.UUID, estimated_tokens: int, max_wait_s: float
    ) -> BandwidthGrant:
        consumed = estimated_tokens if estimated_tokens > 0 else 0
        # Disabled bucket or nothing to consume ⇒ immediate free grant (no Redis touch).
        if self._rate <= 0 or consumed == 0:
            return BandwidthGrant(key_id=key_id, consumed=consumed, waited_s=0.0)

        waited = 0.0
        while True:
            res = await self.try_consume(key_id, consumed)
            if res.granted:
                return BandwidthGrant(key_id=key_id, consumed=consumed, waited_s=waited)
            needed = consumed - res.available
            # Sleep the ACTUAL slice (floored at _MIN_SLEEP_S to avoid a busy-loop on
            # sub-millisecond waits) and budget against that real duration — accumulating the
            # theoretical `needed/rate` instead would let a high rate with tiny per-slice waits
            # overshoot max_wait_s many-fold (the bounded-wait budget IS the timeout).
            slice_s = max(needed / self._rate, _MIN_SLEEP_S)
            if waited + slice_s > max_wait_s:
                raise BandwidthExhaustedError(
                    key_id=str(key_id),
                    requested=consumed,
                    retry_after_s=res.retry_after_s,
                )
            await asyncio.sleep(slice_s)
            waited += slice_s

    # -------------------------------------------------------------- try_consume
    async def try_consume(self, key_id: _uuid_mod.UUID, tokens: int) -> ConsumeResult:
        if self._rate <= 0:
            return ConsumeResult(granted=True, available=max(0, tokens), retry_after_s=0)
        now_ms = self._clock()
        try:
            assert self._bucket_script is not None
            result = await self._bucket_script(
                keys=[_PFX_LEVEL + str(key_id), _PFX_TS + str(key_id)],
                args=[
                    str(now_ms),
                    str(self._rate),
                    str(self._burst),
                    str(tokens),
                    str(self._ttl_s),
                ],
            )
        except Exception as exc:
            _log.warning(
                "bandwidth_bucket.try_consume: Redis error (fail-open)",
                exc_info=exc,
                extra={"key_id": str(key_id)},
            )
            # Fail-open: admit. available=tokens so a caller's pacing loop terminates.
            return ConsumeResult(granted=True, available=max(0, tokens), retry_after_s=0)

        granted = int(result[0]) == 1
        level = float(result[1])
        retry_after = int(result[2])
        return ConsumeResult(
            granted=granted, available=math.floor(level), retry_after_s=retry_after
        )

    # ---------------------------------------------------------------- reconcile
    async def reconcile(
        self, key_id: _uuid_mod.UUID, grant: BandwidthGrant, real_tokens: int
    ) -> None:
        if self._rate <= 0:
            return
        delta = grant.consumed - real_tokens
        if delta == 0:
            return
        try:
            assert self._reconcile_script is not None
            await self._reconcile_script(
                keys=[_PFX_LEVEL + str(key_id)],
                args=[str(self._burst), str(delta), str(self._ttl_s)],
            )
        except Exception as exc:
            _log.warning(
                "bandwidth_bucket.reconcile: Redis error (swallowed)",
                exc_info=exc,
                extra={"key_id": str(key_id)},
            )
            # Never re-raise — reconciliation is fire-and-forget.

    # -------------------------------------------------------------------- level
    async def level(self, key_id: _uuid_mod.UUID) -> int:
        if self._rate <= 0:
            return int(self._burst)
        # A zero-token refill read: level unchanged, returns the post-refill available level.
        try:
            res = await self.try_consume(key_id, 0)
            return res.available
        except Exception as exc:
            _log.warning(
                "bandwidth_bucket.level: Redis error (fail-open)",
                exc_info=exc,
                extra={"key_id": str(key_id)},
            )
            return int(self._burst)
