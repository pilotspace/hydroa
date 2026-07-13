"""Failing-first (RED) suite for RedisTierCapacityGuard / PassthroughTierCapacityGuard
pool mechanics (service-tiers TASK.md §4, contract FROZEN @ v1).

Covers §2 scenarios: priority-own-floor, priority-overflow-shared, priority-overflow-
standard-last-resort, standard-never-starved, cross-worker-race, all-pools-exhausted,
slot-held-until-release, later-rejection-reverses-hold, redis-blip-idempotent-release,
redis-unavailable-admission-degrades, redis-unavailable-release-swallowed, disabled-
passthrough.

Direct-guard tests exercise `TierCapacityGuard.check_and_hold`/`.release` — the public
port contract, not internals — against REAL Redis (db 9, mirrors credits_ledger's own
`redis_client` direct-call idiom for `PostgresCreditGuard`).

RED reason before BUILD: `gateway.proxy.domain.tier_capacity` /
`gateway.proxy.infrastructure.tier_capacity_guard` do not exist -> ImportError.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from gateway.core.errors import ProblemError


def _guard(
    redis_client: Any,
    *,
    cluster_cap: int,
    priority_pct: float = 0.20,
    standard_pct: float = 0.20,
    hold_ttl_s: int = 600,
) -> Any:
    from gateway.proxy.infrastructure.tier_capacity_guard import RedisTierCapacityGuard

    return RedisTierCapacityGuard(
        redis=redis_client,
        cluster_cap=cluster_cap,
        priority_reserved_pct=priority_pct,
        standard_reserved_pct=standard_pct,
        hold_ttl_s=hold_ttl_s,
    )


# ---------------------------------------------------------------------------
# Scenario — priority admitted via its own reserved floor under contention (M3,M5,M7)
# ---------------------------------------------------------------------------


async def test_priority_admitted_via_own_reserved_floor(redis_client: Any) -> None:
    """cluster_cap=10, 20/20/60 -> priority_floor cap=2. First priority admission lands
    in priority_floor; shared/standard_floor pools are untouched."""
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    hold = await guard.check_and_hold(tenant_id, "priority", request_id)

    assert hold.tier_served == "priority"
    assert hold.degraded is False
    assert await redis_client.zcard("tier:pool:priority_floor") == 1
    assert await redis_client.zcard("tier:pool:shared") == 0
    assert await redis_client.zcard("tier:pool:standard_floor") == 0


# ---------------------------------------------------------------------------
# Scenario — priority overflows into the shared pool once its own floor is full,
# still billed priority (M3,M7,M10)
# ---------------------------------------------------------------------------


async def test_priority_overflows_into_shared_still_billed_priority(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=10)  # priority_floor cap=2
    tenant_id = uuid.uuid4()

    # Fill priority_floor (cap=2).
    await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 2

    # A 3rd priority request must overflow into shared, still tier_served="priority".
    hold = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())

    assert hold.tier_served == "priority"
    assert hold.degraded is False
    assert await redis_client.zcard("tier:pool:shared") == 1
    assert await redis_client.zcard("tier:pool:priority_floor") == 2, (
        "priority_floor must not be touched once already full"
    )


# ---------------------------------------------------------------------------
# Scenario — priority overflows all the way into standard_floor as a last resort,
# billed as standard (M3,M7,M10, milestone overflow rule)
# ---------------------------------------------------------------------------


async def test_priority_overflows_into_standard_floor_billed_standard(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=10)  # priority_floor=2, shared=6, standard_floor=2
    tenant_id = uuid.uuid4()

    for _ in range(2):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    for _ in range(6):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 2
    assert await redis_client.zcard("tier:pool:shared") == 6

    hold = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())

    assert hold.tier_served == "standard", "overflow into standard's own floor bills standard"
    assert hold.degraded is False
    assert await redis_client.zcard("tier:pool:standard_floor") == 1


# ---------------------------------------------------------------------------
# Scenario — standard is never starved: its reserved floor is priority-proof (M5,M7)
# ---------------------------------------------------------------------------


async def test_standard_never_starved_by_priority_load(redis_client: Any) -> None:
    """Even with priority_floor AND shared BOTH saturated, a fresh standard request
    admits via standard_floor on its FIRST attempt (priority only reaches
    standard_floor as ITS OWN last resort, never displacing a standard request)."""
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()

    for _ in range(2):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    for _ in range(6):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 2
    assert await redis_client.zcard("tier:pool:shared") == 6
    assert await redis_client.zcard("tier:pool:standard_floor") == 0

    hold = await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())

    assert hold.tier_served == "standard"
    assert hold.degraded is False
    assert await redis_client.zcard("tier:pool:standard_floor") == 1


async def test_standard_request_never_draws_from_priority_floor(redis_client: Any) -> None:
    """A standard request must NEVER land in priority_floor, even when priority_floor
    has free capacity and standard_floor+shared are both full."""
    guard = _guard(redis_client, cluster_cap=10)  # standard_floor=2, shared=6
    tenant_id = uuid.uuid4()

    for _ in range(2):
        await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())
    for _ in range(6):
        await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:standard_floor") == 2
    assert await redis_client.zcard("tier:pool:shared") == 6

    with pytest.raises(ProblemError) as exc_info:
        await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())

    assert exc_info.value.status == 503
    assert exc_info.value.code == "ERR_TIER_CAPACITY_EXHAUSTED"
    assert await redis_client.zcard("tier:pool:priority_floor") == 0, (
        "standard must never draw from priority_floor, even though it has room"
    )


# ---------------------------------------------------------------------------
# Scenario — cross-worker contention for the last slot: exactly one wins, atomically
# ---------------------------------------------------------------------------


async def test_cross_worker_race_for_last_slot_exactly_one_wins(redis_client: Any) -> None:
    """Two independent guard instances (simulating two workers) race for the LAST
    priority_floor slot against the SAME Redis — exactly one admits via priority_floor,
    the other falls through to shared (not shed outright)."""
    guard_a = _guard(redis_client, cluster_cap=10)  # priority_floor cap=2
    guard_b = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()

    # Fill priority_floor to cap-1 (1 free slot left).
    await guard_a.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 1

    results = await asyncio.gather(
        guard_a.check_and_hold(tenant_id, "priority", uuid.uuid4()),
        guard_b.check_and_hold(tenant_id, "priority", uuid.uuid4()),
    )

    # priority_floor cap=2: exactly one more member is admitted into it; the loser
    # falls through to shared (M7 ordered attempts — never shed outright at this point).
    assert await redis_client.zcard("tier:pool:priority_floor") == 2
    assert await redis_client.zcard("tier:pool:shared") == 1
    assert all(h.tier_served == "priority" for h in results), (
        "both must be admitted priority (one via floor, one via shared overflow)"
    )


# ---------------------------------------------------------------------------
# Scenario — all applicable pools exhausted sheds with the tier-specific code (M8,R4)
# ---------------------------------------------------------------------------


async def test_all_pools_exhausted_sheds_tier_capacity_exhausted(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()

    # Saturate priority_floor(2) + shared(6) + standard_floor(2) = 10.
    for _ in range(2):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    for _ in range(6):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    for _ in range(2):
        await guard.check_and_hold(
            tenant_id, "priority", uuid.uuid4()
        )  # overflows to standard_floor
    assert await redis_client.zcard("tier:pool:standard_floor") == 2

    with pytest.raises(ProblemError) as exc_info:
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert exc_info.value.status == 503
    assert exc_info.value.code == "ERR_TIER_CAPACITY_EXHAUSTED"
    assert "Retry-After" in (exc_info.value.headers or {})

    with pytest.raises(ProblemError) as exc_info2:
        await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())
    assert exc_info2.value.status == 503
    assert exc_info2.value.code == "ERR_TIER_CAPACITY_EXHAUSTED"


# ---------------------------------------------------------------------------
# Scenario — hold held until release; release ZREMs and drops ZCARD (M9)
# ---------------------------------------------------------------------------


async def test_release_removes_the_held_member(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    await guard.check_and_hold(tenant_id, "priority", request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 1

    await guard.release(tenant_id, request_id)

    assert await redis_client.zcard("tier:pool:priority_floor") == 0


async def test_release_is_idempotent_never_double_decrements(redis_client: Any) -> None:
    """A double-release (or release of an absent/expired member) is a harmless no-op —
    occupancy is always ZCARD-derived, never a separately-mutated counter, so ZCARD can
    never go negative or be decremented twice for the same member."""
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    await guard.check_and_hold(tenant_id, "priority", request_id)
    await guard.release(tenant_id, request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 0

    # Second release of the SAME (already-released) request_id — must not raise, must
    # not push ZCARD negative.
    await guard.release(tenant_id, request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 0

    # Release of a request_id this guard instance never tracked at all — also a no-op.
    await guard.release(tenant_id, uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 0


async def test_release_of_ttl_expired_member_is_a_noop(redis_client: Any) -> None:
    """A member whose TTL window has already elapsed (pruned by ZREMRANGEBYSCORE on some
    OTHER script invocation) is already gone by the time release() runs — ZREM on an
    absent member is a no-op, never a double-release, never a negative count (mirrors
    the "Redis blip mid-hold" scenario's guarantee)."""
    guard = _guard(redis_client, cluster_cap=10, hold_ttl_s=1)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    await guard.check_and_hold(tenant_id, "priority", request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 1

    # Simulate the TTL window having already elapsed: force the member's score far into
    # the past, then trigger ANOTHER script invocation (a second admission) which prunes
    # it via ZREMRANGEBYSCORE at the start of ITS OWN run.
    await redis_client.zadd("tier:pool:priority_floor", {request_id.hex: 0})
    await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 1, (
        "the stale member must have been pruned; only the new admission's member remains"
    )

    # release() against the now-pruned member is a harmless no-op.
    await guard.release(tenant_id, request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 1


# ---------------------------------------------------------------------------
# Scenario — Redis unavailable at admission time: fail-open, honestly degraded (M8a,R7)
# ---------------------------------------------------------------------------


class _BrokenRedis:
    """A redis-like double whose register_script returns a callable that always raises."""

    def register_script(self, script: str) -> Any:
        async def _raiser(*, keys: Any, args: Any) -> Any:
            raise ConnectionError("simulated Redis outage")

        return _raiser

    async def zrem(self, *_a: Any, **_k: Any) -> None:
        raise ConnectionError("simulated Redis outage")


async def test_redis_unavailable_at_admission_fails_open_degraded() -> None:
    guard = _guard(_BrokenRedis(), cluster_cap=10)
    tenant_id = uuid.uuid4()

    hold = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())

    assert hold.tier_served == "standard", "a degraded admission NEVER bills the priority rate"
    assert hold.degraded is True


async def test_redis_unavailable_at_release_swallowed_never_raises() -> None:
    guard = _guard(_BrokenRedis(), cluster_cap=10)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()
    # Force bookkeeping so release() actually attempts a ZREM (not a no-tracked-hold no-op).
    guard._holds[request_id] = "tier:pool:priority_floor"  # pyright: ignore[reportPrivateUsage]

    # Must not raise — logged + swallowed (M8a).
    await guard.release(tenant_id, request_id)


# ---------------------------------------------------------------------------
# Scenario — disabled tiering (cluster_cap=0) is byte-identical (M4,M6)
# ---------------------------------------------------------------------------


async def test_disabled_cluster_cap_zero_is_passthrough(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=0)
    tenant_id = uuid.uuid4()

    hold_p = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    hold_s = await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())

    assert hold_p.tier_served == "priority"
    assert hold_p.degraded is False
    assert hold_s.tier_served == "standard"
    # No Redis pool touched at all.
    assert await redis_client.zcard("tier:pool:priority_floor") == 0
    assert await redis_client.zcard("tier:pool:shared") == 0
    assert await redis_client.zcard("tier:pool:standard_floor") == 0


async def test_passthrough_guard_always_admits_requested_tier_unchanged() -> None:
    from gateway.proxy.infrastructure.tier_capacity_guard import PassthroughTierCapacityGuard

    guard = PassthroughTierCapacityGuard()
    tenant_id = uuid.uuid4()

    hold_p = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    hold_s = await guard.check_and_hold(tenant_id, "standard", uuid.uuid4())

    assert hold_p == ("priority", False)
    assert hold_s == ("standard", False)
    # release is a no-op, never raises.
    await guard.release(tenant_id, uuid.uuid4())


# ---------------------------------------------------------------------------
# Scenario — superadmin split takes effect live via reconfigure(), no restart (M6,M13)
# ---------------------------------------------------------------------------


async def test_reconfigure_changes_pool_caps_live_without_restart(redis_client: Any) -> None:
    guard = _guard(redis_client, cluster_cap=100, priority_pct=0.20, standard_pct=0.20)
    tenant_id = uuid.uuid4()

    # Fill priority_floor to its OLD cap (20).
    for _ in range(20):
        await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 20

    # A 21st priority request overflows to shared under the OLD split.
    hold_before = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:shared") == 1
    assert hold_before.tier_served == "priority"

    # Superadmin widens priority to 30% -> new cap=30, room reopens in priority_floor.
    guard.reconfigure(cluster_cap=100, priority_reserved_pct=0.30, standard_reserved_pct=0.10)

    hold_after = await guard.check_and_hold(tenant_id, "priority", uuid.uuid4())
    assert await redis_client.zcard("tier:pool:priority_floor") == 21, (
        "the new, wider priority_floor cap must apply to the VERY NEXT admission decision"
    )
    assert hold_after.tier_served == "priority"


async def test_reconfigure_leaves_inflight_holds_unaffected(redis_client: Any) -> None:
    """In-flight holds already placed under the OLD split are unaffected — their ZSET
    membership doesn't change; release() still finds and ZREMs the correct pool."""
    guard = _guard(redis_client, cluster_cap=10)
    tenant_id = uuid.uuid4()
    request_id = uuid.uuid4()

    await guard.check_and_hold(tenant_id, "priority", request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 1

    guard.reconfigure(cluster_cap=50, priority_reserved_pct=0.5, standard_reserved_pct=0.1)

    # The pre-reconfigure hold is still correctly tracked and releasable.
    await guard.release(tenant_id, request_id)
    assert await redis_client.zcard("tier:pool:priority_floor") == 0
