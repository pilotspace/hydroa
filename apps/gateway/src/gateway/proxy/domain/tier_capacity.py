"""TierCapacityGuard — the admission-hold port for priority/standard service tiers
(service-tiers TASK.md §3 CONTRACT — FROZEN @ v1).

Mirrors `gateway.credits.domain.ports.CreditGuard`'s exact `check_and_hold`/`release`
shape and insertion point (immediately BEFORE the credit hold in both governance
choke points, `CompletionUseCase._enforce_governance` and
`NonChatGovernance.authorize`). Unlike CreditGuard, capacity is a binary
occupied/free slot, not a money amount, so there is no separate settle() — release()
is the only post-admission call.

Zero framework imports (domain layer, CONVENTIONS.md layering) — the concrete
Redis-backed implementation lives in `proxy/infrastructure/tier_capacity_guard.py`.
"""

from __future__ import annotations

import uuid
from typing import Literal, NamedTuple, Protocol, runtime_checkable

ServiceTier = Literal["priority", "standard"]


class TierHold(NamedTuple):
    """What `check_and_hold` returns — the tier ACTUALLY served plus whether the
    decision was made under Redis degradation (§1 M8a).

    tier_served: may differ from the requested tier on overflow (a priority
      request admitted through standard's reserved floor is tier_served="standard")
      OR on Redis degradation (degraded=True forces tier_served="standard"
      unconditionally, regardless of the requested tier).
    degraded: True only when Redis was unreachable at admission time for THIS
      call — the audit trail distinguishing "billed standard because the gate
      genuinely degraded" from "billed standard because that's simply what was
      served" (usage_records.tier_capacity_degraded, M8a/M10).
    """

    tier_served: ServiceTier
    degraded: bool


@runtime_checkable
class TierCapacityGuard(Protocol):
    """Reserve-then-release admission gate for the fleet-wide tier capacity pools.

    check_and_hold: ONE atomic admission decision per applicable pool (§3 M7 order).
    Raises ProblemError(503, "ERR_TIER_CAPACITY_EXHAUSTED") ONLY when Redis is
    reachable and every applicable pool is genuinely full (R4) — NEVER raises for a
    Redis/infra failure itself (that path fails OPEN into a degraded TierHold, M8a).

    release: best-effort, never raises — same idiom as CreditGuard.release. A Redis
    exception here is logged + swallowed; the hold's own TTL (tier_capacity_hold_ttl_s)
    is the passive backstop if the release truly never lands.
    """

    async def check_and_hold(
        self, tenant_id: uuid.UUID, tier: ServiceTier, request_id: uuid.UUID
    ) -> TierHold: ...

    async def release(self, tenant_id: uuid.UUID, request_id: uuid.UUID) -> None: ...


__all__ = ["ServiceTier", "TierCapacityGuard", "TierHold"]
