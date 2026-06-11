"""RedisResponseCache — infrastructure adapter for the ResponseCache domain port.

Implements exact-match Redis GET/SET for non-streaming completion responses.
All errors are logged and swallowed — cache failures MUST NOT fail requests.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

_log = logging.getLogger(__name__)

# Output-affecting fields used in cache key derivation (§3 CONTRACT)
_CACHE_KEY_FIELDS = frozenset(
    [
        "model",
        "messages",
        "temperature",
        "top_p",
        "max_tokens",
        "stop",
        "n",
        "presence_penalty",
        "frequency_penalty",
        "seed",
    ]
)


def build_cache_key(tenant_id: str, payload: dict[str, Any]) -> str:
    """Derive the Redis key for a completion payload.

    Key format: resp-cache:{tenant_id}:{sha256(canonical_json)}
    canonical_json: sorted-keys compact JSON over ONLY the present output-affecting fields.
    Absent fields are EXCLUDED (not inserted as null) — strict exact-match semantics.
    """
    subset = {k: v for k, v in payload.items() if k in _CACHE_KEY_FIELDS}
    canonical = json.dumps(subset, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return f"resp-cache:{tenant_id}:{digest}"


class RedisResponseCache:
    """Redis-backed response cache implementing the ResponseCache protocol.

    Uses the app-level redis_client (redis.asyncio).
    get(): Redis GET + JSON deserialize; returns None on miss or any error.
    set(): Redis SET EX=ttl_seconds; fire-and-forget (errors logged, swallowed).
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, cache_key: str) -> dict[str, Any] | None:
        """Return cached body or None on miss/error."""
        try:
            raw = await self._redis.get(cache_key)
            if raw is None:
                return None
            return json.loads(raw)  # type: ignore[no-any-return]
        except Exception as exc:
            _log.warning(
                "response_cache.get failed (swallowed)",
                exc_info=exc,
                extra={"cache_key": cache_key},
            )
            return None

    async def set(self, cache_key: str, body: dict[str, Any], ttl_seconds: int) -> None:
        """Store body with TTL. Errors logged and swallowed."""
        try:
            await self._redis.set(cache_key, json.dumps(body), ex=ttl_seconds)
        except Exception as exc:
            _log.warning(
                "response_cache.set failed (swallowed)",
                exc_info=exc,
                extra={"cache_key": cache_key},
            )
