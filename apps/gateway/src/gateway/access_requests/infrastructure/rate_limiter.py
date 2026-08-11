"""Per-client-IP fixed-window rate limiter for the public access-requests endpoint
(signup-refusal-router TASK.md §3 M7 — FROZEN @ v1, SECURITY task).

Mirrors tenants/infrastructure/invite_public_rate_limiter.py's ``InvitePublicRateLimiter``
shape EXACTLY (§3: "new, mirrors InvitePublicRateLimiter EXACTLY") — a dedicated class
with its OWN key namespace (``access_requests:rl:...``, never sharing a counter with the
invite limiter's ``invite:public:rl:...`` namespace).

Design for failure (CLAUDE.md IO rule):
- FAIL-OPEN: any ``redis.exceptions.RedisError`` or ``OSError`` is logged as a WARNING and
  the request is allowed through (M7) — a public, unauthenticated anti-abuse surface that
  breaks under Redis outage is worse than one that is briefly permissive.
- No exception other than ``AccessRequestRateLimitedError`` ever leaks to callers.
- Window key uses ``INCR`` + ``EXPIRE`` (set only on the first INCR) so the window is a
  fixed 60-second bucket; a race at key creation double-counts at most one request.

Key format: ``access_requests:rl:{ip}:{window_epoch_minute}`` (§3, verbatim).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class AccessRequestRateLimitedError(Exception):
    """Raised when the per-IP access-request rate limit is exceeded; carries retry_after."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"access request rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class AccessRequestIpRateLimiter:
    """Fixed 60-second window per-IP limiter backed by Redis ``INCR`` + ``EXPIRE``.

    ``check(key, limit)`` increments the counter for the current 60-second window bucket
    and raises ``AccessRequestRateLimitedError`` when the counter exceeds ``limit``. On any
    Redis error the call returns silently (fail-open) after logging a WARNING.
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

    def _window_key(self, *, key: str) -> str:
        """Return the Redis key for the current 60-second window bucket."""
        bucket = int(self._now() // self._WINDOW_SECONDS)
        return f"access_requests:rl:{key}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        """Seconds remaining until the next 60-second window starts (1..60)."""
        now = self._now()
        elapsed = now % self._WINDOW_SECONDS
        remaining = self._WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, *, key: str, limit: int) -> None:
        """Increment the per-key counter and raise ``AccessRequestRateLimitedError`` if
        over limit.

        Args:
            key: the caller's client IP (part of the Redis key).
            limit: maximum allowed requests per 60-second window.

        Raises:
            AccessRequestRateLimitedError: when the counter exceeds ``limit``; carries
                ``retry_after`` = seconds to the next window reset.

        Returns:
            None on success (counter within limit) or on Redis error (fail-open).
        """
        window_key = self._window_key(key=key)
        try:
            count: int = await self._redis.incr(window_key)  # type: ignore[union-attr]
            if count == 1:
                # First hit in this window: set expiry so the key self-cleans.
                await self._redis.expire(window_key, self._WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "access_request_rate_limiter_redis_error",
                extra={"key": key},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise AccessRequestRateLimitedError(retry_after=self._seconds_to_next_window())
