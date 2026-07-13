"""PassthroughTierCapacityGuard / RedisTierCapacityGuard — the two
TierCapacityGuard adapters (service-tiers TASK.md §3 CONTRACT — FROZEN @ v1).

RedisTierCapacityGuard mirrors `rate_limits/infrastructure/redis_lua_limiter.py`'s
/ `redis_token_bucket.py`'s ONE-Lua-script-per-decision idiom: THREE sorted-set
pools (`tier:pool:priority_floor` / `tier:pool:standard_floor` / `tier:pool:shared`),
each admitted via the SAME atomic Lua script (prune-then-ZCARD-then-conditional-ZADD),
registered ONCE at construction (`register_script` — no Redis round trip, safe
without lifespan, same precedent as RedisLuaRateLimiter/RedisDeploymentLoadGate).

Fail-open is explicit and total (mirrors RedisLuaRateLimiter's docstring verbatim):
any Exception from Redis/Lua on check_and_hold -> log WARNING + degrade to
TierHold("standard", degraded=True) — NEVER raise, NEVER propagate. release() is
best-effort — a Redis exception there is logged + swallowed, the hold's own TTL is
the backstop (M8a).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from gateway.core.error_catalog import TIER_CAPACITY_EXHAUSTED
from gateway.proxy.domain.tier_capacity import ServiceTier, TierHold

_log = logging.getLogger(__name__)

_POOL_PRIORITY_FLOOR = "tier:pool:priority_floor"
_POOL_STANDARD_FLOOR = "tier:pool:standard_floor"
_POOL_SHARED = "tier:pool:shared"

# KEYS[1] = pool zset key
# ARGV[1] = now_ms
# ARGV[2] = ttl_ms   (passive-expiry window, §0 issue #7 — DECIDED 600s)
# ARGV[3] = cap
# ARGV[4] = member (hex request_id)
# ARGV[5] = ttl_s    (Redis key EXPIRE — 2x the hold TTL, so the key itself never
#                     outlives its own members by more than one prune cycle)
#
# Atomic: evict stale members + count + conditional record, ONE round trip —
# mirrors RedisLuaRateLimiter's prune-then-count-then-admit shape exactly (§0).
_POOL_ADMIT_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local ttl_ms = tonumber(ARGV[2])
local cap = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_s = tonumber(ARGV[5])

redis.call('ZREMRANGEBYSCORE', key, 0, now_ms - ttl_ms)

local n = redis.call('ZCARD', key)
if n < cap then
    redis.call('ZADD', key, now_ms, member)
    redis.call('EXPIRE', key, ttl_s)
    return 1
end
return 0
"""


class PassthroughTierCapacityGuard:
    """No-op default — always admits the requested tier unchanged, release is a no-op.

    Wired when tiering is not configured (tier_capacity_cluster_cap<=0, the default)
    — mirrors PassthroughCreditGuard exactly. Byte-identical to today until an
    operator wires RedisTierCapacityGuard AND sets a positive cluster cap (M4).
    """

    async def check_and_hold(
        self, tenant_id: uuid.UUID, tier: ServiceTier, request_id: uuid.UUID
    ) -> TierHold:
        return TierHold(tier, False)

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        return


class RedisTierCapacityGuard:
    """Redis-backed cross-worker atomic tier-capacity admission gate.

    Partitions `cluster_cap` into three pools by `priority_reserved_pct` /
    `standard_reserved_pct` (DECIDED default 20%/20%, 60% shared, §1 M6):
      priority_floor cap = round(cluster_cap * priority_reserved_pct)  — priority-exclusive
      standard_floor cap = round(cluster_cap * standard_reserved_pct)  — standard-exclusive
      shared cap          = cluster_cap - priority_floor cap - standard_floor cap

    cluster_cap<=0 -> pass-through mode (no Redis touched, no scarcity to prefer
    against) — mirrors PassthroughTierCapacityGuard's own no-op shape so a caller
    never needs to branch on which concrete guard is wired.

    release() tracks which pool a hold actually landed in via an in-memory
    dict[request_id -> pool_name] scoped to THIS guard instance — same bookkeeping
    need the original per-worker design had, just remembering a Redis key name
    instead of a Semaphore object (§3 CONTRACT).
    """

    def __init__(
        self,
        *,
        redis: Any,
        cluster_cap: int,
        priority_reserved_pct: float,
        standard_reserved_pct: float,
        hold_ttl_s: int = 600,
    ) -> None:
        self._redis = redis
        self._cluster_cap = cluster_cap
        self._hold_ttl_s = hold_ttl_s
        self._priority_cap = round(cluster_cap * priority_reserved_pct) if cluster_cap > 0 else 0
        self._standard_cap = round(cluster_cap * standard_reserved_pct) if cluster_cap > 0 else 0
        self._shared_cap = (
            max(cluster_cap - self._priority_cap - self._standard_cap, 0) if cluster_cap > 0 else 0
        )
        # register_script only — no Redis round trip at construction (safe without
        # lifespan, mirrors RedisLuaRateLimiter's own constructor precedent).
        self._admit_script = redis.register_script(_POOL_ADMIT_LUA)
        # Per-instance bookkeeping: request_id -> pool key it was admitted into.
        # Bounded by in-flight-request count (entries removed on release); a crashed
        # worker's dict entries are simply lost with the process — harmless, because
        # occupancy is always live ZCARD, never trusted from this side-table (the
        # ZSET's own TTL is the passive backstop for an orphaned hold, §1 M8a note).
        self._holds: dict[uuid.UUID, str] = {}

    async def check_and_hold(
        self, tenant_id: uuid.UUID, tier: ServiceTier, request_id: uuid.UUID
    ) -> TierHold:
        if self._cluster_cap <= 0:
            return TierHold(tier, False)

        pools_to_try: list[tuple[str, int, ServiceTier]]
        if tier == "priority":
            pools_to_try = [
                (_POOL_PRIORITY_FLOOR, self._priority_cap, "priority"),
                (_POOL_SHARED, self._shared_cap, "priority"),
                (_POOL_STANDARD_FLOOR, self._standard_cap, "standard"),
            ]
        else:
            pools_to_try = [
                (_POOL_STANDARD_FLOOR, self._standard_cap, "standard"),
                (_POOL_SHARED, self._shared_cap, "standard"),
            ]

        now_ms = int(time.time() * 1000)
        ttl_ms = self._hold_ttl_s * 1000
        member = request_id.hex
        # Redis key EXPIRE at 2x the hold TTL — the key itself must outlive any single
        # member by at least one full prune cycle, else an idle pool's key could expire
        # mid-window and silently drop live members before ZREMRANGEBYSCORE ever runs.
        key_ttl_s = self._hold_ttl_s * 2

        for pool_key, cap, served in pools_to_try:
            if cap <= 0:
                continue
            try:
                admitted = await self._admit_script(
                    keys=[pool_key],
                    args=[str(now_ms), str(ttl_ms), str(cap), member, str(key_ttl_s)],
                )
            except Exception as exc:
                # Total fail-open (§1 M8a) — mirrors RedisLuaRateLimiter's own
                # "any Exception -> admit" idiom, except here the safe admission is
                # ALWAYS "standard", never the requested tier, and is flagged degraded
                # so billing never silently charges a priority markup for a promise
                # the outage made it impossible to keep.
                _log.warning(
                    "tier_capacity_guard.check_and_hold: Redis error (fail-open, degraded)",
                    exc_info=exc,
                    extra={"tenant_id": str(tenant_id), "request_id": str(request_id)},
                )
                return TierHold("standard", True)

            if admitted:
                self._holds[request_id] = pool_key
                return TierHold(served, False)

        # Every applicable pool observed itself full — genuinely exhausted (Redis
        # reachable throughout), never a fail-open case (R4/M8).
        raise TIER_CAPACITY_EXHAUSTED.exc(headers={"Retry-After": "1"})

    def reconfigure(
        self,
        *,
        cluster_cap: int,
        priority_reserved_pct: float,
        standard_reserved_pct: float,
    ) -> None:
        """Live-mutate the pool caps in place — no worker restart (platform-route §3).

        Called by PUT /admin/platform/service-tiers against the SAME app.state-boot
        singleton every request already shares, so the new caps apply to the very next
        admission decision. In-flight holds are UNAFFECTED — self._holds only tracks
        which pool KEY a hold landed in, never a cap value, so an already-admitted
        request's release() still finds and ZREMs the correct pool regardless of a
        cap change mid-flight (§3 "in-flight holds are unaffected").
        """
        self._cluster_cap = cluster_cap
        self._priority_cap = round(cluster_cap * priority_reserved_pct) if cluster_cap > 0 else 0
        self._standard_cap = round(cluster_cap * standard_reserved_pct) if cluster_cap > 0 else 0
        self._shared_cap = (
            max(cluster_cap - self._priority_cap - self._standard_cap, 0) if cluster_cap > 0 else 0
        )

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None:
        pool_key = self._holds.pop(request_id, None)
        if pool_key is None:
            # No known hold for this request (PassthroughTierCapacityGuard-style no-op,
            # a shed request that never held a slot, or a hold this instance never
            # tracked) — nothing to release.
            return
        try:
            await self._redis.zrem(pool_key, request_id.hex)
        except Exception as exc:
            # Best-effort, never raises — swallowed + logged (M8a). The hold's own
            # TTL (ZREMRANGEBYSCORE on the NEXT admission touching this key) reclaims
            # it passively if this release truly never lands.
            _log.warning(
                "tier_capacity_guard.release: Redis error (swallowed)",
                exc_info=exc,
                extra={"tenant_id": str(tenant_id), "request_id": str(request_id)},
            )


__all__ = ["PassthroughTierCapacityGuard", "RedisTierCapacityGuard"]
