"""Redis SETNX-backed SamlReplayCache — the independent second replay-defense
layer keyed by assertion @ID (M5.6), distinct from SamlRequestStore's
per-request-id consumption (M3).

Design for failure: same fail-CLOSED + bounded-2s-timeout posture as
saml_request_store.py (M12) — this is a correctness/security gate, not an
availability optimization.
"""

from __future__ import annotations

import asyncio

from redis.exceptions import RedisError

from gateway.auth.domain.saml_errors import SamlStoreUnavailableError

_REDIS_TIMEOUT_SECONDS = 2.0
_KEY_PREFIX = "saml:consumed:"


class RedisSamlReplayCache:
    """Redis SETNX-backed single-use cache keyed by SAML assertion @ID."""

    def __init__(self, redis: object) -> None:
        self._redis = redis

    def _key(self, assertion_id: str) -> str:
        return f"{_KEY_PREFIX}{assertion_id}"

    async def mark_consumed_if_new(self, assertion_id: str, *, ttl_seconds: int) -> bool:
        """Returns True iff assertion_id was NOT already present (first use).

        ttl_seconds must already be the caller-computed
        min(NotOnOrAfter - now, 86400) — bounded so a maliciously long
        NotOnOrAfter can't pin a Redis key forever (M5.6).
        """
        # Redis EXPIRE/SET ex= requires a positive integer; a NotOnOrAfter that
        # has already elapsed (but within the M5.5 clock-skew allowance) could
        # compute <= 0 — floor at 1s so the SETNX call itself stays valid.
        bounded_ttl = max(1, int(ttl_seconds))
        try:
            was_set: bool = await asyncio.wait_for(
                self._redis.set(self._key(assertion_id), "1", ex=bounded_ttl, nx=True),  # type: ignore[union-attr]
                timeout=_REDIS_TIMEOUT_SECONDS,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise SamlStoreUnavailableError(
                f"saml replay cache unavailable: {exc}"
            ) from exc

        return bool(was_set)
