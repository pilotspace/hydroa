"""Per-request partial-usage ContextVar sink for mid-stream disconnect billing.

During a streaming completion, native SSE steppers publish accumulated token counts
into this sink as soon as the wire provides them.  On a client disconnect the handler
reads the sink: if a partial floor is available (and no complete usage frame was
collected), it becomes the disconnect usage → the recorder disconnect-estimate block
stamps provider_cost from the partial, user billed $0.

CONTRACT (FROZEN @ v1 — disconnect-billing-all-providers TASK.md §3):
  - partial_stream_usage: ContextVar[dict[str,int] | None] = ContextVar(...)
  - publish_partial_usage(prompt_tokens, completion_tokens) -> None
      no-op when the ContextVar is None (no sink set = non-stream path)
  - read_partial_usage() -> dict[str,int] | None
      validates non-negative ints; invalid → None (caller WARNs "partial_usage_invalid")

Design-for-failure: publish and read NEVER raise — any error is swallowed gracefully
so the disconnect path invariant (never raise inside GeneratorExit/CancelledError
handling) is preserved.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

_log = logging.getLogger(__name__)

# The per-request sink: set to {} by CompletionUseCase.stream before the async-for
# loop; reset in the finally block.  Default=None means "no sink" (non-stream path).
partial_stream_usage: ContextVar[dict[str, int] | None] = ContextVar(
    "partial_stream_usage", default=None
)


def publish_partial_usage(prompt_tokens: int, completion_tokens: int) -> None:
    """Write (prompt_tokens, completion_tokens) into the per-request sink.

    No-op when no sink is set (non-stream / buffered paths).
    Never raises: a misbehaving call-site must not crash the adapter.
    """
    try:
        d = partial_stream_usage.get()
        if d is None:
            return  # no sink — non-stream path, expected no-op
        d["prompt_tokens"] = prompt_tokens
        d["completion_tokens"] = completion_tokens
    except Exception:  # noqa: S110
        # Swallow: publish failures must never surface into the hot path.
        pass


def read_partial_usage() -> dict[str, int] | None:
    """Read and validate the accumulated partial usage from the sink.

    Returns a dict with prompt_tokens, completion_tokens, total_tokens when
    the sink carries non-negative integer values.
    Returns None and WARNs "partial_usage_invalid" on any malformed/negative values.
    Returns None silently when the sink is empty or unset.

    Never raises.
    """
    try:
        d = partial_stream_usage.get()
        if d is None:
            return None
        if not d:
            return None  # empty dict — no data published yet

        prompt = d.get("prompt_tokens")
        completion = d.get("completion_tokens")

        # Validate: must be non-negative ints (bool excluded — bool is a subclass of int)
        if (
            not isinstance(prompt, int)
            or isinstance(prompt, bool)
            or prompt < 0
            or not isinstance(completion, int)
            or isinstance(completion, bool)
            or completion < 0
        ):
            _log.warning(
                "partial_usage_invalid",
                extra={"prompt_tokens": prompt, "completion_tokens": completion},
            )
            return None

        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        }
    except Exception:  # swallow: read failures must not surface into the disconnect path
        return None
