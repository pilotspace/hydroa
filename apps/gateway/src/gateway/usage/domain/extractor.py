"""Pure SSE usage extractor — zero framework imports.

Contract (FROZEN @ v1, amended @ v2 by live-upstream-smoke):
  extract_usage_from_sse(chunks: list[bytes]) -> dict | None
  — Parses SSE frames for a `"usage"` key in any `data: {...}` event.
  — Returns the usage dict on success, None if no usage frame found.
  — Forwarded bytes are never touched; this function is read-only.

v2 amendment: the chunks are JOINED before parsing. Network chunk boundaries
carry no meaning — the live OpenRouter stream splits its (large) final usage
frame across chunks, which the per-chunk v1 parser could never decode
(evidence: 2026-06-10 live smoke recorded 0/0 tokens against upstream 24/73).
"""

from __future__ import annotations

import json


def extract_usage_from_sse(chunks: list[bytes]) -> dict[str, object] | None:
    """Return the `usage` dict from the LAST SSE data frame that carries one.

    Operates on the joined byte stream so frames split across network chunks
    parse correctly. Returns None when no complete frame contains a usage dict.

    This is a pure function: no I/O, no side-effects.
    """
    text = b"".join(chunks).decode("utf-8", errors="replace")
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if payload in ("[DONE]", ""):
            continue
        try:
            parsed = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and "usage" in parsed:
            usage = parsed["usage"]
            if isinstance(usage, dict):
                return usage
    return None
