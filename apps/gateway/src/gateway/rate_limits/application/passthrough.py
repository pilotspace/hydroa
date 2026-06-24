"""Passthrough (no-op) limiter/bucket implementations for the default-OFF / unwired paths."""

from __future__ import annotations

import uuid

from gateway.rate_limits.domain.ports import BandwidthGrant, ConsumeResult


class PassthroughRateLimiter:
    """No-op RateLimiter — always admits; used when rate_limiter not wired."""

    async def check_rpm(self, key_id: uuid.UUID, limit: int) -> None:
        return

    async def check_tpm(self, key_id: uuid.UUID, limit: int) -> None:
        return

    async def record_tpm(self, key_id: uuid.UUID, tokens: int) -> None:
        return


class PassthroughBandwidthBucket:
    """No-op BandwidthBucket — always grants immediately with zero wait.

    Wired when bandwidth pacing is disabled (bandwidth_tokens_per_sec == 0) so the stream
    path is byte-identical to today: every acquire grants the full estimate, no Redis call,
    no pacing. Mirrors PassthroughRateLimiter.
    """

    async def acquire(
        self, key_id: uuid.UUID, estimated_tokens: int, max_wait_s: float
    ) -> BandwidthGrant:
        return BandwidthGrant(key_id=key_id, consumed=max(0, estimated_tokens), waited_s=0.0)

    async def try_consume(self, key_id: uuid.UUID, tokens: int) -> ConsumeResult:
        return ConsumeResult(granted=True, available=max(0, tokens), retry_after_s=0)

    async def reconcile(self, key_id: uuid.UUID, grant: BandwidthGrant, real_tokens: int) -> None:
        return

    async def level(self, key_id: uuid.UUID) -> int:
        return 0
