"""Domain ports for rate limiting — zero framework imports."""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable


@runtime_checkable
class RateLimiter(Protocol):
    """Check and record rate limit windows for an API key.

    Contract (§3 TASK.md):
      check_rpm(key_id, limit) — atomic ZSET sliding-window RPM check.
        Raises RateLimitExceededError when window count >= limit.
        Fail-open on Redis error (log + admit).

      check_tpm(key_id, limit) — pre-flight TPM admission check.
        Raises RateLimitExceededError when accumulated token sum >= limit.
        Fail-open on Redis error.

      record_tpm(key_id, tokens) — post-stream TPM accounting.
        Records actual token count into the TPM window.
        Never raises — swallows all errors (fire-and-forget safe).
    """

    async def check_rpm(self, key_id: uuid.UUID, limit: int) -> None:
        """Atomic RPM sliding-window check-and-record.

        Raises RateLimitExceededError if window is full.
        Fails open on any Redis error.
        """
        ...

    async def check_tpm(self, key_id: uuid.UUID, limit: int) -> None:
        """Pre-flight TPM admission check against accumulated token sum.

        Raises RateLimitExceededError if accumulated sum >= limit.
        Fails open on any Redis error.
        """
        ...

    async def record_tpm(self, key_id: uuid.UUID, tokens: int) -> None:
        """Post-stream TPM accounting — records actual token count.

        Never raises. Swallows all errors (fire-and-forget safe).
        """
        ...
