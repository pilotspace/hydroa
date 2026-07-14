"""RedisVectorCache — the embedding-similarity ("vector") response-cache layer (v19 task 5).

The THIRD lookup layer, consulted only after the exact (`resp-cache:`) and the normalized
near-duplicate (`resp-cache-sem:`) layers both miss. It embeds the last user message, scans a
bounded per-(tenant, model) Redis index of previously-cached prompt vectors, and serves the
pointed exact body when the best cosine similarity is at or above a configurable threshold.

NAMING: the codebase's existing "semantic" layer is normalization-only (a BINARY match with no
threshold). This layer is the milestone's "true embedding-similarity" cache and is named
**vector** to avoid colliding with the taken `semantic_*` namespace (see TASK.md §0). The
milestone's "semantic cache hit" maps to `x_cache="vector_hit"`.

DESIGN-FOR-FAILURE: every IO (the embed call, every Redis op) is wrapped; ANY failure degrades
to a MISS (lookup) or a no-op (store). The layer can never fail or error a user request. The
embedding lookup is an INTERNAL cost — never billed to the served request. Keys hold the
embedding vector + a pointer to the exact-cache key (never the raw prompt); the body lives only
in the existing exact key, the same trust boundary as the exact/normalization layers.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any

from gateway.proxy.infrastructure.response_cache import build_cache_key

_log = logging.getLogger(__name__)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. TOTAL — never raises, never NaN/inf.

    Returns 0.0 when the lengths differ, either vector is empty, or either has zero L2 norm
    (a zero vector has no direction → "no similarity", not a division error).
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=True):  # lengths already checked equal above
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _last_user_text(body: dict[str, Any]) -> str | None:
    """Return the content of the LAST message whose role casefolds to 'user', else None.

    The discriminating prompt is the last user turn; embedding the whole transcript would make
    every multi-turn conversation a near-unique vector (no recall). No user message → no-op.
    """
    messages = body.get("messages")
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if isinstance(msg, dict) and str(msg.get("role", "")).casefold() == "user":
            content = msg.get("content")
            return content if isinstance(content, str) else str(content)
    return None


def _namespace(tenant_id: str, model: str) -> str:
    """Per-(tenant, model) key prefix. Model is HASHED so a hit can never cross models, and the
    tenant id scopes the namespace so a hit can never cross tenants."""
    model_hash = hashlib.sha256(model.encode("utf-8")).hexdigest()
    return f"vec-cache:{tenant_id}:{model_hash}"


class RedisVectorCache:
    """Redis-backed embedding-similarity cache (implements the VectorCache port).

    Storage shape (per cached entry):
      index  vec-cache:{tenant}:{sha256(model)}:idx   Redis LIST of entry ids (newest-first)
      entry  vec-cache:{tenant}:{sha256(model)}:{id}  JSON {"v": <vector>, "k": <exact key>}
      body   resp-cache:{tenant}:{sha256(payload)}    the response (owned by the exact layer)
    """

    def __init__(
        self,
        redis: Any,
        *,
        embedder: Callable[[str], Awaitable[list[float] | None]],
        threshold: float,
        max_candidates: int,
    ) -> None:
        self._redis = redis
        self._embedder = embedder
        self._threshold = threshold
        self._max_candidates = max_candidates

    async def lookup(
        self, *, tenant_id: str, model: str, body: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return the cached body for a near-duplicate prompt, or None on any miss/failure."""
        try:
            text = _last_user_text(body)
            if text is None:
                return None  # no user message → no-op (no embed call)
            vec = await self._embedder(text)
            if not vec:
                return None  # embedder unavailable / empty → fail-safe MISS
            namespace = _namespace(tenant_id, model)
            ids = await self._redis.lrange(f"{namespace}:idx", 0, self._max_candidates - 1)
            if not ids:
                return None  # no candidates → MISS (skip the mget round trip entirely)
            entry_ids = [
                raw_id.decode("utf-8") if isinstance(raw_id, bytes) else str(raw_id)
                for raw_id in ids
            ]
            # Fetch every candidate entry in ONE mget instead of an N+1 of per-id gets.
            raw_entries = await self._redis.mget([f"{namespace}:{eid}" for eid in entry_ids])
            best_pointer: str | None = None
            best_sim = -1.0
            for raw_entry in raw_entries:
                if raw_entry is None:
                    continue
                entry = json.loads(raw_entry)
                cand_vec = entry.get("v")
                if not isinstance(cand_vec, list):
                    continue
                sim = cosine_similarity(vec, cand_vec)
                if sim > best_sim:
                    best_sim = sim
                    best_pointer = entry.get("k")
            if best_pointer is None or best_sim < self._threshold:
                return None  # below threshold / no candidates → MISS
            raw_body = await self._redis.get(best_pointer)
            if raw_body is None:
                return None  # dangling pointer (exact body expired) → MISS
            result = json.loads(raw_body)
            return result if isinstance(result, dict) else None
        except Exception as exc:
            _log.warning("vector_cache.lookup failed (swallowed → MISS)", exc_info=exc)
            return None

    async def store(
        self,
        *,
        tenant_id: str,
        model: str,
        body: dict[str, Any],
        response_body: dict[str, Any],
        ttl: int,
    ) -> None:
        """Register the prompt's embedding pointing at the exact-cache key. No-op on any failure."""
        try:
            text = _last_user_text(body)
            if text is None:
                return  # no user message → nothing to index
            vec = await self._embedder(text)
            if not vec:
                return  # embedder unavailable → skip (no-op)
            exact_key = build_cache_key(tenant_id, body)  # the SAME key the exact layer stores
            namespace = _namespace(tenant_id, model)
            entry_id = hashlib.sha256(exact_key.encode("utf-8")).hexdigest()
            entry = json.dumps({"v": vec, "k": exact_key})
            await self._redis.set(f"{namespace}:{entry_id}", entry, ex=ttl)
            idx_key = f"{namespace}:idx"
            await self._redis.lpush(idx_key, entry_id)
            await self._redis.ltrim(idx_key, 0, self._max_candidates - 1)  # cap the candidate set
            await self._redis.expire(idx_key, ttl)
        except Exception as exc:
            _log.warning("vector_cache.store failed (swallowed → no-op)", exc_info=exc)
