"""Pure SSE usage extractor — zero framework imports.

Contract (FROZEN @ v1):
  extract_usage_from_sse(chunks: list[bytes]) -> dict | None
  — Parses SSE frames for a `"usage"` key in any `data: {...}` event.
  — Returns the usage dict on success, None if no usage frame found.
  — Forwarded bytes are never touched; this function is read-only.
"""

from __future__ import annotations

import json


def extract_usage_from_sse(chunks: list[bytes]) -> dict[str, object] | None:
    """Parse SSE byte chunks and return the `usage` dict from the first frame that contains it.

    Iterates chunks in reverse so the final usage frame wins (OpenAI sends usage
    at the end of the stream).  Returns None when no usage key is found in any
    complete data frame.

    This is a pure function: no I/O, no side-effects.
    """
    for chunk in reversed(chunks):
        text = chunk.decode("utf-8", errors="replace").strip()
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
            if isinstance(parsed, dict) and "usage" in parsed:
                usage = parsed["usage"]
                if isinstance(usage, dict):
                    return usage
    return None
