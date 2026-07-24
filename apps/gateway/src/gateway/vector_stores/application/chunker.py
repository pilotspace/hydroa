"""Pure fixed-window chunker — vector-store-files PLAN.md §3 (FROZEN @ v1).

Fixed 2400-char windows, 200-char overlap (build-set constants, contract-pinned).
No I/O, no tenant/model awareness — a pure function so it is trivially unit-testable
and reusable by both the worker and (later) any offline re-index tooling.
"""

from __future__ import annotations

_CHUNK_SIZE = 2400
_CHUNK_OVERLAP = 200

#: Secondary failure mode (prose-covered, not gated): a file whose chunk count
#: exceeds this cap fails closed with last_error.code "file_too_large", checked
#: BEFORE the embed call — diagnosable, distinct from a provider outage.
MAX_CHUNKS_PER_FILE = 2048


def chunk_text(text: str, *, size: int = _CHUNK_SIZE, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    """Split ``text`` into ``size``-char windows with ``overlap``-char stride overlap.

    Empty/whitespace-only text yields an empty list (the worker's caller treats
    zero chunks as the "file_empty" failure mode — never an embed call with no
    input). A ``text`` shorter than ``size`` yields exactly one chunk (itself).
    """
    if not text or not text.strip():
        return []
    stride = max(1, size - overlap)
    chunks: list[str] = []
    pos = 0
    n = len(text)
    while pos < n:
        chunks.append(text[pos : pos + size])
        if pos + size >= n:
            break
        pos += stride
    return chunks
