"""Pure chain-materialization + cap helpers (responses-state-store PLAN.md §3).

IO-free by construction: no session, no upstream, no clock. Given a resolved
previous row's stored context + response, and this turn's freshly-translated
chat messages, produce the materialized upstream context and check the depth /
size caps — all BEFORE the governance dial (the caps are pre-dial rejects).

Materialized order (M5, asserted in-suite):
  prev.context_messages  +  prev output-as-assistant-messages  +  new input messages

Storage is O(chain²) by design (each row carries the full accumulated context);
bounded by MAX_CHAIN_DEPTH + MAX_CONTEXT_BYTES + the retention sweeper.
"""

from __future__ import annotations

import json
from typing import Any

# The 64-turn depth cap: chaining onto a row already at depth >= MAX_CHAIN_DEPTH
# is rejected 400 ERR_RESPONSES_CHAIN_TOO_DEEP (a never-incrementing build would make
# this cap vacuous — DoS-adjacent unbounded O(chain²) storage).
MAX_CHAIN_DEPTH = 64

# 1 MiB serialized cap on the MATERIALIZED context: exceeding it is rejected 413
# ERR_RESPONSES_CONTEXT_TOO_LARGE, pre-dial (never sent upstream, never billed).
MAX_CONTEXT_BYTES = 1024 * 1024


def assistant_messages_from_response(response_body: Any) -> list[dict[str, Any]]:
    """Extract the assistant turn(s) from a stored Response object as chat messages.

    Reads the Response ``output`` items (type ``message``, role ``assistant``) and
    flattens their ``output_text`` parts into a single assistant chat message. A
    Response with no textual output contributes no assistant message (an honest
    empty turn) rather than a fabricated blank one.
    """
    if not isinstance(response_body, dict):
        return []
    output = response_body.get("output")
    if not isinstance(output, list):
        return []
    messages: list[dict[str, Any]] = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role") != "assistant":
            continue
        text_parts: list[str] = []
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "output_text":
                    text_parts.append(str(part.get("text", "")))
        elif isinstance(content, str):
            text_parts.append(content)
        joined = "".join(text_parts)
        if joined:
            messages.append({"role": "assistant", "content": joined})
    return messages


def materialize_context(
    *,
    prev_context_messages: Any,
    prev_response_body: Any,
    new_messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build the upstream message list for a chained turn (M5 order)."""
    prior: list[dict[str, Any]] = []
    if isinstance(prev_context_messages, list):
        prior = [m for m in prev_context_messages if isinstance(m, dict)]
    return [*prior, *assistant_messages_from_response(prev_response_body), *new_messages]


def context_serialized_bytes(messages: list[dict[str, Any]]) -> int:
    """Serialized byte length of the materialized context (size-cap measure)."""
    return len(json.dumps(messages).encode("utf-8"))


def exceeds_size_cap(messages: list[dict[str, Any]]) -> bool:
    return context_serialized_bytes(messages) > MAX_CONTEXT_BYTES


def exceeds_depth_cap(prev_chain_depth: int) -> bool:
    """True iff chaining onto a row at ``prev_chain_depth`` would exceed the cap."""
    return prev_chain_depth >= MAX_CHAIN_DEPTH
