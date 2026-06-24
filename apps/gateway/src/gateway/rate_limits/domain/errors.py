"""Domain errors for rate limiting."""

from __future__ import annotations


class RateLimitExceededError(Exception):
    """Raised when a rate limit (RPM or TPM) is exceeded.

    Attributes:
        limit_type: "RPM" or "TPM"
        limit: the configured limit value
        key_id: the key_id that was rate-limited
        retry_after_s: integer seconds until the window slot frees (min 1)
    """

    def __init__(
        self,
        limit_type: str,
        limit: int,
        key_id: str,
        retry_after_s: int,
    ) -> None:
        super().__init__(f"{limit_type} limit {limit} exceeded for key {key_id}")
        self.limit_type = limit_type
        self.limit = limit
        self.key_id = key_id
        self.retry_after_s = retry_after_s


class BandwidthExhaustedError(Exception):
    """Raised by BandwidthBucket.acquire when the bounded-wait budget is spent before a grant.

    Mirrors RateLimitExceededError's shape (carries retry_after_s for the eventual 503 +
    Retry-After mapping at the HTTP seam). Raised ONLY when a grant would need more than
    max_wait_s of waiting — an empty-but-within-budget bucket paces instead of raising.

    Attributes:
        key_id: the key_id whose bandwidth bucket was exhausted
        requested: the (estimated) token count the caller tried to acquire
        retry_after_s: integer seconds until enough tokens accrue (min 1)
    """

    def __init__(self, key_id: str, requested: int, retry_after_s: int) -> None:
        super().__init__(
            f"bandwidth exhausted for key {key_id}: {requested} tokens, "
            f"retry after {retry_after_s}s"
        )
        self.key_id = key_id
        self.requested = requested
        self.retry_after_s = retry_after_s
