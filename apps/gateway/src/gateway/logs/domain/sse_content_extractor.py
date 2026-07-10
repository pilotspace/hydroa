"""Pure SSE assistant-content extractor — zero framework imports.

Contract (payload-capture-store TASK.md §3, FROZEN @ v1):
  extract_content_from_sse(chunks: list[bytes]) -> str | None
  — Parses SSE frames for `choices[*].delta.content` (streaming) or
    `choices[*].message.content` (non-delta) fields in any `data: {...}` event, in
    frame order, and concatenates them into the assembled assistant text.
  — Returns the assembled text on success, None if no content fragments were found.
  — Forwarded bytes are never touched; this function is read-only.

Mirrors usage/domain/extractor.py:extract_usage_from_sse's location/shape/contract
style (pure, total, joined-byte-stream parsing so a frame split across network chunks
still decodes) — the "final vs partial" completeness signal for a capture row is the
SAME stream_usage_is_complete() check the billing path already uses, not a separate
completeness heuristic here.
"""

from __future__ import annotations

import json


def extract_content_from_sse(chunks: list[bytes]) -> str | None:
    """Return the assembled assistant text from all SSE data frames, or None.

    Concatenates `choices[*].delta.content` (streaming delta shape) or
    `choices[*].message.content` (non-delta shape) from EVERY `data: {...}` frame, in
    order, across the whole joined byte stream (so a frame split across network chunks
    still parses). Returns None when no frame contributed any content text.

    This is a pure function: no I/O, no side-effects, never raises.
    """
    text = b"".join(chunks).decode("utf-8", errors="replace")
    parts: list[str] = []
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
        if not isinstance(parsed, dict):
            continue
        choices = parsed.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
                    continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content:
                    parts.append(content)
    if not parts:
        return None
    return "".join(parts)
