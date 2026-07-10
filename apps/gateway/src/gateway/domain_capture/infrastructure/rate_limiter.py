"""DomainClaimRateLimiter — per-tenant fixed-window rate limiter for the domain-claims
create/verify endpoints (domain-capture TASK.md §3 M14 — FROZEN @ v1).

Mirrors tenants/infrastructure/invite_public_rate_limiter.py's InvitePublicRateLimiter
shape EXACTLY (§0 Honors precedent: "no shared generic version exists to reuse today" —
each bounded context that needs this shape writes its own small class), diverging only on
the key (tenant_id, since domain-claims callers are authenticated OWNERs, not anonymous
per-IP callers like the public invite endpoints).

Design for failure (CLAUDE.md IO rule): FAIL-OPEN on any Redis error/outage — a security-
admin-only surface that breaks under a Redis outage is worse than one that is briefly
permissive; matches the invite limiter's own posture exactly.
"""

from __future__ import annotations

import logging
import time
import uuid

from redis.exceptions import RedisError

from gateway.domain_capture.domain.errors import DomainClaimRateLimitedError

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


class DomainClaimRateLimiter:
    def __init__(self, redis: object) -> None:
        self._redis = redis

    def _window_key(self, *, action: str, tenant_id: uuid.UUID) -> str:
        bucket = int(time.time() // _WINDOW_SECONDS)
        return f"domain_claims:rl:{action}:{tenant_id}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        now = time.time()
        elapsed = now % _WINDOW_SECONDS
        remaining = _WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, *, action: str, tenant_id: uuid.UUID, limit: int) -> None:
        window_key = self._window_key(action=action, tenant_id=tenant_id)
        try:
            count: int = await self._redis.incr(window_key)  # type: ignore[union-attr]
            if count == 1:
                await self._redis.expire(window_key, _WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "domain_claim_rate_limiter_redis_error",
                extra={"tenant_id": str(tenant_id)},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise DomainClaimRateLimitedError(retry_after=self._seconds_to_next_window())
