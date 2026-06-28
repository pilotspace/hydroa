"""Unit tests for PlaygroundMintRateLimiter (security review F1).

Locks the two non-obvious behaviours of the limiter:
  - over the per-user/window limit → raises MintRateLimitedError + a positive retry_after;
  - any Redis error → FAIL-OPEN (returns None, never blocks a real session).
"""

import pytest
from redis.exceptions import RedisError

from gateway.keys.infrastructure.mint_rate_limiter import (
    MintRateLimitedError,
    PlaygroundMintRateLimiter,
)


class _CountingRedis:
    """Minimal async Redis double: INCR returns a monotonically rising count."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expired: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expired.append((key, seconds))


class _ExplodingRedis:
    """Async Redis double whose INCR always raises — exercises the fail-open path."""

    async def incr(self, key: str) -> int:
        raise RedisError("redis down")

    async def expire(self, key: str, seconds: int) -> None:  # pragma: no cover - never reached
        raise RedisError("redis down")


async def test_within_limit_does_not_raise_and_sets_expiry() -> None:
    limiter = PlaygroundMintRateLimiter(redis=_CountingRedis())
    # limit=2 → first two calls in the window are allowed.
    await limiter.check(user_id="u1", limit=2)
    await limiter.check(user_id="u1", limit=2)


async def test_over_limit_raises_with_positive_retry_after() -> None:
    redis = _CountingRedis()
    limiter = PlaygroundMintRateLimiter(redis=redis)
    await limiter.check(user_id="u1", limit=1)  # count=1, allowed
    with pytest.raises(MintRateLimitedError) as ei:
        await limiter.check(user_id="u1", limit=1)  # count=2 > 1 → blocked
    assert ei.value.retry_after > 0
    # Expiry set exactly once — on the first INCR of the window.
    assert len(redis.expired) == 1


async def test_fail_open_on_redis_error() -> None:
    limiter = PlaygroundMintRateLimiter(redis=_ExplodingRedis())
    # A Redis outage must NOT hard-block a legitimate mint (returns None, no raise).
    assert await limiter.check(user_id="u1", limit=1) is None


async def test_per_user_isolation() -> None:
    """One user exhausting their window does not affect another user."""
    limiter = PlaygroundMintRateLimiter(redis=_CountingRedis())
    await limiter.check(user_id="u1", limit=1)
    with pytest.raises(MintRateLimitedError):
        await limiter.check(user_id="u1", limit=1)
    # Different user, fresh bucket — allowed.
    await limiter.check(user_id="u2", limit=1)
