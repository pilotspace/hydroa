"""RedisLuaRateLimiter — atomic sliding-window RPM/TPM enforcement.

Design (§3 CONTRACT + §1 SPECIFY):
  RPM: ZSET sliding window — atomic Lua: evict + count + conditional record.
       Key: ratelimit:rpm:{key_id}  TTL=61s
  TPM: ZSET (member="{tokens}:{uuid}") + companion sum key.
       Key: ratelimit:tpm:{key_id}  TTL=61s
       Sum: ratelimit:tpm_sum:{key_id}  TTL=61s
  Window: 60 000 ms (60 seconds)
  Fail-open: any Redis/Lua exception → log warning + admit.
"""

from __future__ import annotations

import logging
import math
import uuid as _uuid_mod
from typing import Any

from gateway.rate_limits.domain.errors import RateLimitExceededError

_log = logging.getLogger(__name__)

_WINDOW_MS = 60_000  # 60-second window
_TTL_S = 61  # TTL = window + 1 s buffer


# ── RPM Lua script ──────────────────────────────────────────────────────────
# Atomic: evict expired entries + count + conditional record in one round-trip.
# Returns [1, 0] on admit; [0, oldest_score_ms] on deny.
#
# KEYS[1] = ratelimit:rpm:{key_id}
# ARGV[1] = now_ms
# ARGV[2] = window_ms
# ARGV[3] = limit
# ARGV[4] = member (uuid4 hex)

_RPM_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

-- Evict entries older than the window
redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - window_ms)

-- Count entries in current window
local count = redis.call('ZCARD', key)

if count >= limit then
    -- Denied: return oldest entry score for Retry-After
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local oldest_score = 0
    if oldest and #oldest >= 2 then
        oldest_score = tonumber(oldest[2])
    end
    return {0, oldest_score}
end

-- Admit: record this request
redis.call('ZADD', key, now_ms, member)
redis.call('EXPIRE', key, tonumber(ARGV[5]))

return {1, 0}
"""

# ── TPM pre-flight Lua script (check only — token_count=0) ─────────────────
# Evicts expired entries + corrects sum + checks admission.
# Returns [1, 0] on admit; [0, oldest_score_ms] on deny.
#
# KEYS[1] = ratelimit:tpm:{key_id}
# KEYS[2] = ratelimit:tpm_sum:{key_id}
# ARGV[1] = now_ms
# ARGV[2] = window_ms
# ARGV[3] = limit
# ARGV[4] = ttl_s

_TPM_CHECK_LUA = """
local zset_key = KEYS[1]
local sum_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local ttl_s = tonumber(ARGV[4])

-- Evict expired entries and subtract their token counts from the sum
local evicted = redis.call('ZRANGEBYSCORE', zset_key, 0, now_ms - window_ms)
local evict_sum = 0
for _, member in ipairs(evicted) do
    -- member format: "{token_count}:{uuid}"
    local tok_str = string.match(member, "^(%d+):")
    if tok_str then
        evict_sum = evict_sum + tonumber(tok_str)
    end
end
redis.call('ZREMRANGEBYSCORE', zset_key, 0, now_ms - window_ms)

-- Correct the sum key
if evict_sum > 0 then
    local new_sum = redis.call('INCRBYFLOAT', sum_key, -evict_sum)
    if tonumber(new_sum) < 0 then
        redis.call('SET', sum_key, '0')
    end
end
-- Refresh TTL
if redis.call('EXISTS', zset_key) == 1 then
    redis.call('EXPIRE', zset_key, ttl_s)
end
if redis.call('EXISTS', sum_key) == 1 then
    redis.call('EXPIRE', sum_key, ttl_s)
end

-- Read current sum
local raw_sum = redis.call('GET', sum_key)
local current_sum = 0
if raw_sum then
    current_sum = tonumber(raw_sum) or 0
end

if current_sum >= limit then
    -- Denied: return oldest entry score for Retry-After
    local oldest = redis.call('ZRANGE', zset_key, 0, 0, 'WITHSCORES')
    local oldest_score = now_ms  -- fallback: 1-second retry
    if oldest and #oldest >= 2 then
        oldest_score = tonumber(oldest[2])
    end
    return {0, oldest_score}
end

return {1, 0}
"""

# ── TPM record Lua script (post-stream accounting) ─────────────────────────
# Always records (never denies). Evicts stale + updates ZSET + sum.
#
# KEYS[1] = ratelimit:tpm:{key_id}
# KEYS[2] = ratelimit:tpm_sum:{key_id}
# ARGV[1] = now_ms
# ARGV[2] = window_ms
# ARGV[3] = token_count
# ARGV[4] = member ("{token_count}:{uuid}")
# ARGV[5] = ttl_s

_TPM_RECORD_LUA = """
local zset_key = KEYS[1]
local sum_key = KEYS[2]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local token_count = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_s = tonumber(ARGV[5])

-- Evict expired entries and subtract from sum
local evicted = redis.call('ZRANGEBYSCORE', zset_key, 0, now_ms - window_ms)
local evict_sum = 0
for _, m in ipairs(evicted) do
    local tok_str = string.match(m, "^(%d+):")
    if tok_str then
        evict_sum = evict_sum + tonumber(tok_str)
    end
end
redis.call('ZREMRANGEBYSCORE', zset_key, 0, now_ms - window_ms)

if evict_sum > 0 then
    local new_sum = redis.call('INCRBYFLOAT', sum_key, -evict_sum)
    if tonumber(new_sum) < 0 then
        redis.call('SET', sum_key, '0')
    end
end

-- Record the new entry
redis.call('ZADD', zset_key, now_ms, member)
redis.call('INCRBYFLOAT', sum_key, token_count)
redis.call('EXPIRE', zset_key, ttl_s)
redis.call('EXPIRE', sum_key, ttl_s)

return 1
"""


def _retry_after(oldest_ms: int, now_ms: int, window_ms: int = _WINDOW_MS) -> int:
    """Compute Retry-After seconds from oldest window entry timestamp.

    Formula (§3): ceil((oldest_ms + window_ms - now_ms) / 1000), min 1.
    """
    delta_ms = oldest_ms + window_ms - now_ms
    return max(1, math.ceil(delta_ms / 1000))


class RedisLuaRateLimiter:
    """Concrete RateLimiter using Redis Lua sliding-window scripts.

    Constructor registers all Lua scripts once. The scripts are cached by
    redis-py's register_script() — no manual EVALSHA management needed.

    Fail-open: any Exception from Redis/Lua → log warning + admit (never propagate).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis
        # Register scripts once — redis-py caches SHA and uses EVALSHA automatically
        self._rpm_script = redis.register_script(_RPM_LUA)
        self._tpm_check_script = redis.register_script(_TPM_CHECK_LUA)
        self._tpm_record_script = redis.register_script(_TPM_RECORD_LUA)

    async def check_rpm(self, key_id: _uuid_mod.UUID, limit: int) -> None:
        """Atomic RPM sliding-window check-and-record.

        Raises RateLimitExceededError if window count >= limit.
        Fails open on any Redis error.
        """
        import time

        now_ms = int(time.time() * 1000)
        member = _uuid_mod.uuid4().hex
        redis_key = f"ratelimit:rpm:{key_id}"

        try:
            result = await self._rpm_script(
                keys=[redis_key],
                args=[str(now_ms), str(_WINDOW_MS), str(limit), member, str(_TTL_S)],
            )
        except Exception as exc:
            _log.warning(
                "rate_limiter.check_rpm: Redis error (fail-open)",
                exc_info=exc,
                extra={"key_id": str(key_id)},
            )
            return  # fail-open

        admitted = result[0]
        if admitted == 0:
            oldest_ms = int(result[1]) if result[1] else now_ms
            retry_after = _retry_after(oldest_ms, now_ms)
            raise RateLimitExceededError(
                limit_type="RPM",
                limit=limit,
                key_id=str(key_id),
                retry_after_s=retry_after,
            )

    async def check_tpm(self, key_id: _uuid_mod.UUID, limit: int) -> None:
        """Pre-flight TPM admission check.

        Raises RateLimitExceededError if accumulated token sum >= limit.
        Fails open on any Redis error.
        """
        import time

        now_ms = int(time.time() * 1000)
        zset_key = f"ratelimit:tpm:{key_id}"
        sum_key = f"ratelimit:tpm_sum:{key_id}"

        try:
            result = await self._tpm_check_script(
                keys=[zset_key, sum_key],
                args=[str(now_ms), str(_WINDOW_MS), str(limit), str(_TTL_S)],
            )
        except Exception as exc:
            _log.warning(
                "rate_limiter.check_tpm: Redis error (fail-open)",
                exc_info=exc,
                extra={"key_id": str(key_id)},
            )
            return  # fail-open

        admitted = result[0]
        if admitted == 0:
            oldest_ms = int(result[1]) if result[1] else now_ms
            retry_after = _retry_after(oldest_ms, now_ms)
            raise RateLimitExceededError(
                limit_type="TPM",
                limit=limit,
                key_id=str(key_id),
                retry_after_s=retry_after,
            )

    async def record_tpm(self, key_id: _uuid_mod.UUID, tokens: int) -> None:
        """Post-stream TPM accounting — records actual token count.

        Never raises. Fire-and-forget safe.
        """
        if tokens <= 0:
            return

        import time

        now_ms = int(time.time() * 1000)
        member = f"{tokens}:{_uuid_mod.uuid4().hex}"
        zset_key = f"ratelimit:tpm:{key_id}"
        sum_key = f"ratelimit:tpm_sum:{key_id}"

        try:
            await self._tpm_record_script(
                keys=[zset_key, sum_key],
                args=[str(now_ms), str(_WINDOW_MS), str(tokens), member, str(_TTL_S)],
            )
        except Exception as exc:
            _log.warning(
                "rate_limiter.record_tpm: Redis error (swallowed)",
                exc_info=exc,
                extra={"key_id": str(key_id), "tokens": tokens},
            )
            # Never re-raise — post-stream accounting must never fail the request
