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


def extract_generation_id_from_sse(chunks: list[bytes]) -> str | None:
    """Return the provider generation id (`id`) from the SSE data frames, or None.

    Scans the joined byte stream for the FIRST `data: {...}` frame carrying a
    non-empty string `id` (OpenAI-compatible shape — OpenRouter emits e.g.
    "gen-..."). Operates on the joined stream so an id frame split across network
    chunks still parses. Pure + total: never raises (returns None on any absence
    or garbage). This is the rail disconnect cost-recovery (v30 t6) looks up later.
    """
    text = b"".join(chunks).decode("utf-8", errors="replace")
    for line in text.splitlines():
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
        if isinstance(parsed, dict):
            gen_id = parsed.get("id")
            if isinstance(gen_id, str) and gen_id:
                return gen_id
    return None


def stream_usage_is_complete(usage: object) -> bool:
    """Return True iff `usage` is a billable terminal stream frame.

    Complete iff `usage` is a dict carrying at least one strictly-positive int
    among `prompt_tokens` / `completion_tokens` / `total_tokens`. Anything else —
    None, non-dict, an empty dict, or an all-zero/no-positive-count "partial"
    frame — is incomplete (the stream-usage-completeness fallback case).

    Pure + total: never raises (stream-usage-completeness TASK.md §3).
    """
    if not isinstance(usage, dict):
        return False
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return True
    return False
