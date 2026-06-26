"""Pure in-Python cosine search for the memory domain.

No pgvector — embeddings are stored as DOUBLE PRECISION[] and ranked here.

Functions:
  cosine_similarity(a, b) -> float
  rank(query_vec, rows, top_k, query_text) -> list[tuple[MemoryRow, float | None]]
"""

from __future__ import annotations

import math

from gateway.memory.infrastructure.orm import MemoryRow


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors.

    Returns 0.0 when either vector is empty/None or lengths differ.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def rank(
    query_vec: list[float] | None,
    rows: list[MemoryRow],
    top_k: int,
    query_text: str,
) -> list[tuple[MemoryRow, float | None]]:
    """Rank memory rows against the query.

    Two modes:
      1. Cosine mode (query_vec is not None):
         Score each row that HAS an embedding of the same dimension.
         Rows without an embedding (or mismatched dim) are excluded from results.
         Sort descending by score, take top_k.

      2. Substring fallback (query_vec is None):
         Include rows whose content contains query_text (case-insensitive).
         Score = None. Rows are already newest-first from the repository.
         Take top_k.
    """
    if query_vec is not None:
        scored: list[tuple[MemoryRow, float]] = []
        for row in rows:
            emb = row.embedding
            if emb is None or len(emb) != len(query_vec):
                # no embedding or mismatched dimension — skip from cosine ranking
                continue
            score = cosine_similarity(query_vec, emb)
            scored.append((row, score))
        scored.sort(key=lambda t: t[1], reverse=True)
        return [(row, score) for row, score in scored[:top_k]]
    else:
        # Substring fallback: case-insensitive containment, newest first, score=None
        needle = query_text.lower()
        matches = [row for row in rows if needle in row.content.lower()]
        return [(row, None) for row in matches[:top_k]]
