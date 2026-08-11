"""Per-scim_token_id fixed-window rate limiter for /scim/v2/* writes (scim-provisioning
TASK.md §3, M12).

Mirrors tenants/infrastructure/invite_public_rate_limiter.py's InvitePublicRateLimiter
shape EXACTLY, keyed by scim_token_id instead of client IP (SCIM callers are already
authenticated by the time this runs, so a per-token key is more precise than per-IP).

Design for failure (CLAUDE.md IO rule):
- FAIL-OPEN: any redis.exceptions.RedisError or OSError is logged as a WARNING and the
  request is allowed through — same availability-over-strict-limiting posture already
  accepted for the sibling public-invite surface (TASK.md M12).
- No exception other than ScimRateLimitedError ever leaks to callers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis.exceptions import RedisError

from gateway.scim.domain.errors import ScimRateLimitedError

logger = logging.getLogger(__name__)


class ScimTokenRateLimiter:
    """Fixed 60-second window per-scim_token_id limiter backed by Redis INCR + EXPIRE."""

    _WINDOW_SECONDS = 60

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

    def _window_key(self, *, scim_token_id: str) -> str:
        bucket = int(self._now() // self._WINDOW_SECONDS)
        return f"scim:rl:{scim_token_id}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        now = self._now()
        elapsed = now % self._WINDOW_SECONDS
        remaining = self._WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, *, scim_token_id: str, limit: int) -> None:
        """Increment the per-token counter; raise ScimRateLimitedError if over limit.

        Returns None on success (within limit) or on Redis error (fail-open).
        """
        window_key = self._window_key(scim_token_id=scim_token_id)
        try:
            count: int = await self._redis.incr(window_key)  # type: ignore[union-attr]
            if count == 1:
                await self._redis.expire(window_key, self._WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "scim_rate_limiter_redis_error",
                extra={"scim_token_id": scim_token_id},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise ScimRateLimitedError(retry_after=self._seconds_to_next_window())
