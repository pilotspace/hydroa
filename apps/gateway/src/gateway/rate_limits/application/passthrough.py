"""PassthroughRateLimiter — no-op implementation for use when no limits are configured."""

from __future__ import annotations

import uuid


class PassthroughRateLimiter:
    """No-op RateLimiter — always admits; used when rate_limiter not wired."""

    async def check_rpm(self, key_id: uuid.UUID, limit: int) -> None:
        return

    async def check_tpm(self, key_id: uuid.UUID, limit: int) -> None:
        return

    async def record_tpm(self, key_id: uuid.UUID, tokens: int) -> None:
        return
