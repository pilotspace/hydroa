"""Per-client-IP fixed-window rate limiter for the public invite preview/accept endpoints
(member-invite-acceptance TASK.md §3).

Mirrors keys/infrastructure/mint_rate_limiter.py's ``PlaygroundMintRateLimiter`` shape
EXACTLY — every existing bounded context that needed this shape (agent_oauth's own
``RateLimitedError``, keys/playground's ``MintRateLimitedError``) wrote its OWN small
class; no shared generic version exists to reuse today (TASK.md §1 Framings weighed).

Design for failure (CLAUDE.md IO rule):
- FAIL-OPEN: any ``redis.exceptions.RedisError`` or ``OSError`` is logged as a WARNING and
  the request is allowed through. A public, unauthenticated anti-abuse surface that breaks
  under Redis outage is worse than one that is briefly permissive.
- No exception other than ``InviteRateLimitedError`` ever leaks to callers.
- Window key uses ``INCR`` + ``EXPIRE`` (set only on the first INCR) so the window is a
  fixed 60-second bucket; a race at key creation double-counts at most one request.

Key format: ``invite:public:rl:{action}:{key}:{window_epoch_minute}``
  ``window_epoch_minute`` = ``int(time.time() // 60)`` — one bucket per UTC minute.
  ``key`` is the caller's client IP (both GET preview and POST accept key by IP, M7).
  ``action`` discriminates preview from accept — the two endpoints have independently
  configured limits (``invite_preview_rpm`` / ``invite_accept_rpm``); without this segment
  both endpoints would silently share one counter and compete for whichever limit is lower.
"""

from __future__ import annotations

import logging
import time

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class InviteRateLimitedError(Exception):
    """Raised when the per-IP invite rate limit is exceeded; carries retry_after seconds."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"invite rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class InvitePublicRateLimiter:
    """Fixed 60-second window per-key limiter backed by Redis ``INCR`` + ``EXPIRE``.

    ``check(key, limit)`` increments the counter for the current 60-second window bucket
    and raises ``InviteRateLimitedError`` when the counter exceeds ``limit``. On any Redis
    error the call returns silently (fail-open) after logging a WARNING.
    """

    _WINDOW_SECONDS = 60

    def __init__(self, redis: object) -> None:
        self._redis = redis

    def _window_key(self, *, action: str, key: str) -> str:
        """Return the Redis key for the current 60-second window bucket."""
        bucket = int(time.time() // self._WINDOW_SECONDS)
        return f"invite:public:rl:{action}:{key}:{bucket}"

    def _seconds_to_next_window(self) -> int:
        """Seconds remaining until the next 60-second window starts (1..60)."""
        now = time.time()
        elapsed = now % self._WINDOW_SECONDS
        remaining = self._WINDOW_SECONDS - elapsed
        return max(1, int(remaining) + 1)

    async def check(self, *, action: str, key: str, limit: int) -> None:
        """Increment the per-key counter and raise ``InviteRateLimitedError`` if over limit.

        Args:
            action: discriminates the endpoint being limited (e.g. ``"preview"`` /
                ``"accept"``) so two differently-configured limits never share one counter.
            key: the caller's client IP (part of the Redis key).
            limit: maximum allowed requests per 60-second window.

        Raises:
            InviteRateLimitedError: when the counter exceeds ``limit``; carries
                ``retry_after`` = seconds to the next window reset.

        Returns:
            None on success (counter within limit) or on Redis error (fail-open).
        """
        window_key = self._window_key(action=action, key=key)
        try:
            count: int = await self._redis.incr(window_key)  # type: ignore[union-attr]
            if count == 1:
                # First hit in this window: set expiry so the key self-cleans.
                await self._redis.expire(window_key, self._WINDOW_SECONDS)  # type: ignore[union-attr]
        except (RedisError, OSError):
            logger.warning(
                "invite_public_rate_limiter_redis_error",
                extra={"key": key},
                exc_info=True,
            )
            return  # FAIL-OPEN

        if count > limit:
            raise InviteRateLimitedError(retry_after=self._seconds_to_next_window())
