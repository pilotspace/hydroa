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
from collections.abc import Callable

from redis.exceptions import RedisError

from gateway.domain_capture.domain.errors import DomainClaimRateLimitedError

logger = logging.getLogger(__name__)

_WINDOW_SECONDS = 60


class DomainClaimRateLimiter:
    # `now` is injectable so a test can PIN the window (todo #111). The bucket is
    # `floor(now / WINDOW)`, so a test that fires N requests and expects the (N+1)th to be
    # rejected is silently assuming no window boundary falls between them. Nothing measured
    # that assumption, and it fails a few percent of the time under load — which reads as a
    # limiter regression, not as a test artifact. Both clock reads below MUST come from this
    # one source: a bucket and a retry_after taken from separate `time.time()` calls can
    # straddle a boundary and disagree with each other.
    def __init__(self, redis: object, *, now: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._now = now

    def _window_key(self, *, action: str, tenant_id: uuid.UUID) -> str:
        bucket = int(self._now() // _WINDOW_SECONDS)
        return f"domain_claims:rl:{action}:{tenant_id}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        now = self._now()
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
