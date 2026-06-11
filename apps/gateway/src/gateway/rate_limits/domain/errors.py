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
