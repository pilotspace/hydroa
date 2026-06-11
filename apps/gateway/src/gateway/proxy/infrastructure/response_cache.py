"""RedisResponseCache — infrastructure adapter for the ResponseCache domain port.

Implements exact-match Redis GET/SET for non-streaming completion responses.
All errors are logged and swallowed — cache failures MUST NOT fail requests.

Also provides semantic-cache helpers: build_semantic_cache_key() for the
normalized near-duplicate cache layer (v5), and get_semantic_pointer() /
set_semantic_pointer() for the pointer-based Redis storage pattern.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
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


def _normalize_message_content(content: str, *, is_last_user: bool) -> str:
    """Normalize a single message content string per §3 CONTRACT normalization algorithm.

    Steps applied (PINNED — deviating from this creates false hits):
      1. NFKC unicode normalization
      2. casefold (unicode-aware lowercasing)
      3. whitespace collapse: re.sub(r'\\s+', ' ', s).strip()
      4. trailing-punctuation strip ON LAST USER MESSAGE ONLY:
         re.sub(r'[.!?,;:]+$', '', s).rstrip()
    """
    normalized = unicodedata.normalize("NFKC", content)
    normalized = normalized.casefold()
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if is_last_user:
        normalized = re.sub(r"[.!?,;:]+$", "", normalized).rstrip()
    return normalized


def build_semantic_cache_key(tenant_id: str, payload: dict[str, Any]) -> str:
    """Derive the Redis semantic key for a completion payload.

    Key format: resp-cache-sem:{tenant_id}:{sha256(canonical_sem_json)}
    The key prefix is DISTINCT from the exact-match cache (resp-cache:) so both
    layers coexist without accidentally reading each other's entries.

    Normalization algorithm (§3 CONTRACT — PINNED):
      Step 1: per-message normalize (NFKC + casefold + whitespace-collapse +
              trailing-punct strip on last user message only).
      Step 2: model included VERBATIM (NOT normalized — case-sensitive).
      Step 3: parameter fields included verbatim, absent fields excluded.
      Step 4: canonical JSON (sort_keys=True, compact separators).
      Step 5: sha256 hex digest.
    """
    messages = payload.get("messages", [])

    # Identify the last user message index for trailing-punct strip
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and str(msg.get("role", "")).casefold() == "user":
            last_user_idx = i

    normalized_messages = []
    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            normalized_messages.append(msg)
            continue
        norm_msg: dict[str, Any] = {}
        # Normalize role: casefold
        raw_role = str(msg.get("role", ""))
        norm_msg["role"] = raw_role.casefold()
        # Normalize content
        raw_content = str(msg.get("content", ""))
        is_last_user = i == last_user_idx
        norm_msg["content"] = _normalize_message_content(raw_content, is_last_user=is_last_user)
        # Normalize name if present: NFKC + casefold
        if "name" in msg:
            raw_name = str(msg["name"])
            norm_msg["name"] = unicodedata.normalize("NFKC", raw_name).casefold()
        normalized_messages.append(norm_msg)

    # Build the normalized subset — model is verbatim (NOT normalized)
    normalized_subset: dict[str, Any] = {
        "model": payload["model"],  # verbatim — NOT normalized
        "messages": normalized_messages,
    }
    # Include parameter fields verbatim; absent fields excluded (exact-cache convention)
    _PARAM_FIELDS = frozenset(
        [
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
    for k in _PARAM_FIELDS:
        if k in payload:
            normalized_subset[k] = payload[k]

    canonical = json.dumps(normalized_subset, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"resp-cache-sem:{tenant_id}:{digest}"


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

    async def get_pointer(self, sem_key: str) -> str | None:
        """Return the exact-cache key string stored at sem_key, or None on miss/error.

        Semantic pointer storage: the semantic key holds the exact-cache key string
        (plain UTF-8, not JSON) so we can dereference it in a second GET.
        """
        try:
            raw = await self._redis.get(sem_key)
            if raw is None:
                return None
            if isinstance(raw, bytes):
                return raw.decode("utf-8")
            return str(raw)
        except Exception as exc:
            _log.warning(
                "response_cache.get_pointer failed (swallowed)",
                exc_info=exc,
                extra={"sem_key": sem_key},
            )
            return None

    async def set_pointer(self, sem_key: str, exact_key: str, ttl_seconds: int) -> None:
        """Store exact_key string at sem_key with TTL. Errors logged and swallowed.

        The value is the plain exact-cache key string (UTF-8), NOT JSON-encoded.
        TTL must match the exact key's TTL so both expire together (pointer parity).
        """
        try:
            await self._redis.set(sem_key, exact_key.encode("utf-8"), ex=ttl_seconds)
        except Exception as exc:
            _log.warning(
                "response_cache.set_pointer failed (swallowed)",
                exc_info=exc,
                extra={"sem_key": sem_key},
            )
