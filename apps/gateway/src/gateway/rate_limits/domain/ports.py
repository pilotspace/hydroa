"""Domain ports for rate limiting — zero framework imports."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class BandwidthGrant:
    """A successful bandwidth-bucket acquisition (bandwidth-token-bucket §3 FROZEN @ v1).

    Attributes:
        key_id: the API key whose bucket was charged.
        consumed: the ESTIMATED tokens consumed at acquire-time (reconciled to truth later).
        waited_s: seconds spent pacing inside acquire() before the grant (0.0 = no wait).
    """

    key_id: uuid.UUID
    consumed: int
    waited_s: float


@dataclass(frozen=True, slots=True)
class ConsumeResult:
    """Outcome of one atomic refill+conditional-consume (try_consume).

    Attributes:
        granted: True iff the post-refill level covered the requested tokens (and was consumed).
        available: post-refill token level (after consume when granted; refilled level when not).
        retry_after_s: integer seconds until enough tokens accrue when not granted
            (min 1; 0 when granted).
    """

    granted: bool
    available: int
    retry_after_s: int


@runtime_checkable
class BandwidthBucket(Protocol):
    """Per-key_id aggregate token-bucket pacing primitive (bandwidth-token-bucket §3).

    Distinct from RateLimiter (RPM/TPM sliding windows): a DRAINABLE bucket of estimated-
    then-reconciled tokens, shared across all workers/streams of one key_id via Redis.

    Contract:
      acquire(key_id, estimated_tokens, max_wait_s) — bounded-wait pace-or-raise.
        estimated_tokens <= 0 → immediate 0-consume grant. Raises BandwidthExhaustedError
        iff a grant would need more than max_wait_s of waiting. FAIL-OPEN: any Redis error
        → admit (grant consumed=estimated_tokens, waited_s=0.0).
      try_consume(key_id, tokens) — one atomic refill+conditional-consume round-trip; no sleep.
        FAIL-OPEN → granted=True.
      reconcile(key_id, grant, real_tokens) — apply the signed delta (grant.consumed - real_tokens)
        to the level (refund/debit). Fire-and-forget; never raises.
      level(key_id) — best-effort available tokens after lazy refill; FAIL-OPEN → burst.
    """

    async def acquire(
        self, key_id: uuid.UUID, estimated_tokens: int, max_wait_s: float
    ) -> BandwidthGrant: ...

    async def try_consume(self, key_id: uuid.UUID, tokens: int) -> ConsumeResult: ...

    async def reconcile(
        self, key_id: uuid.UUID, grant: BandwidthGrant, real_tokens: int
    ) -> None: ...

    async def level(self, key_id: uuid.UUID) -> int: ...


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
