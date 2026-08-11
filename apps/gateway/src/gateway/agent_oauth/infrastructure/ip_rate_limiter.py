"""Per-client-IP fixed-window rate limiter for the device-authorization endpoint.

Design for failure (CLAUDE.md IO rule):
- FAIL-OPEN: any ``redis.exceptions.RedisError`` or ``OSError`` is logged as a WARNING
  and the request is allowed through. An authentication/anti-abuse surface that breaks
  under Redis outage is worse than one that is temporarily permissive.
- No exceptions other than ``RateLimitedError`` ever leak to callers.
- Window key uses ``INCR`` + ``EXPIRE`` (set only on first INCR) so the window is a
  fixed 60-second bucket aligned to the UTC minute epoch. On the very first call within
  a bucket the key is created and the 60-second TTL is set atomically enough (two
  round-trips) — a race at key creation would double-count at most one request.

Key format: ``agent_oauth:authz:rl:{ip}:{window_epoch_minute}``
  ``window_epoch_minute`` = ``int(time.time() // 60)`` — one bucket per UTC minute.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised when the per-IP limit is exceeded; carries the seconds-to-window-reset."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class AgentOAuthIpRateLimiter:
    """Fixed 60-second window per-IP limiter backed by Redis INCR + EXPIRE.

    ``check(ip, limit)`` increments the counter for the current 60-second window bucket
    and raises ``RateLimitedError`` when the counter exceeds ``limit``. On any Redis
    error the call returns silently (fail-open) after logging a WARNING.
    """

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

    def _window_key(self, ip: str) -> str:
        """Return the Redis key for the current 60-second window bucket."""
        bucket = int(self._now() // self._WINDOW_SECONDS)
        return f"agent_oauth:authz:rl:{ip}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        """Seconds remaining until the next 60-second window starts (1..60)."""
        now = self._now()
        elapsed = now % self._WINDOW_SECONDS
        remaining = self._WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, ip: str, limit: int) -> None:
        """Increment the per-IP counter and raise ``RateLimitedError`` if limit exceeded.

        Args:
            ip: client IP address string (used as part of the Redis key).
            limit: maximum allowed requests per 60-second window.

        Raises:
            RateLimitedError: when the counter exceeds ``limit``.
                              Carries ``retry_after`` = seconds to next window reset.

        Returns:
            None on success (counter within limit) or on Redis error (fail-open).
        """
        key = self._window_key(ip)
        try:
            count: int = await self._redis.incr(key)  # type: ignore[union-attr]
            if count == 1:
                # First hit in this window: set expiry so the key self-cleans.
                await self._redis.expire(key, self._WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "agent_oauth_ip_rate_limiter_redis_error",
                extra={"ip": ip},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise RateLimitedError(retry_after=self._seconds_to_next_window())
