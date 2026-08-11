"""Per-user fixed-window rate limiter for playground-token minting.

Closes finding F1 of the playground-token security review: an authenticated member who
calls ``POST /admin/keys/playground-token`` directly — bypassing the dashboard BFF's
in-memory token cache — could otherwise mint unbounded short-lived keys, causing DB-row
sprawl, audit-log dilution, and a transient spend-amplification window. A fixed 60-second
window per user bounds the mint rate.

Design for failure (CLAUDE.md IO rule):
- FAIL-OPEN: any ``redis.exceptions.RedisError`` or ``OSError`` is logged as a WARNING and
  the mint is allowed through. A per-user anti-abuse cap that hard-blocks under a Redis
  outage is worse than one that is briefly permissive (the short TTL + per-key spend cap
  still bound the blast radius).
- No exception other than ``MintRateLimitedError`` ever leaks to callers.
- Window key uses ``INCR`` + ``EXPIRE`` (set only on the first INCR) so the window is a
  fixed 60-second bucket; a race at key creation double-counts at most one request.

Key format: ``playground:mint:rl:{user_id}:{window_epoch_minute}``
  ``window_epoch_minute`` = ``int(time.time() // 60)`` — one bucket per UTC minute.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class MintRateLimitedError(Exception):
    """Raised when the per-user mint limit is exceeded; carries the seconds-to-window-reset."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"playground mint rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class PlaygroundMintRateLimiter:
    """Fixed 60-second window per-user limiter backed by Redis ``INCR`` + ``EXPIRE``.

    ``check(user_id, limit)`` increments the counter for the current 60-second window
    bucket and raises ``MintRateLimitedError`` when the counter exceeds ``limit``. On any
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

    def _window_key(self, user_id: str) -> str:
        """Return the Redis key for the current 60-second window bucket."""
        bucket = int(self._now() // self._WINDOW_SECONDS)
        return f"playground:mint:rl:{user_id}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        """Seconds remaining until the next 60-second window starts (1..60)."""
        now = self._now()
        elapsed = now % self._WINDOW_SECONDS
        remaining = self._WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, user_id: str, limit: int) -> None:
        """Increment the per-user counter and raise ``MintRateLimitedError`` if over ``limit``.

        Args:
            user_id: the authenticated user's id (part of the Redis key).
            limit: maximum allowed mints per 60-second window.

        Raises:
            MintRateLimitedError: when the counter exceeds ``limit``; carries
                ``retry_after`` = seconds to the next window reset.

        Returns:
            None on success (counter within limit) or on Redis error (fail-open).
        """
        key = self._window_key(user_id)
        try:
            count: int = await self._redis.incr(key)  # type: ignore[union-attr]
            if count == 1:
                # First hit in this window: set expiry so the key self-cleans.
                await self._redis.expire(key, self._WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "playground_mint_rate_limiter_redis_error",
                extra={"user_id": user_id},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise MintRateLimitedError(retry_after=self._seconds_to_next_window())
