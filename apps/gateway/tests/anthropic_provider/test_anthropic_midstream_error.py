"""Tests for Anthropic mid-stream error event handling.

RED: The _AnthropicSSEStepper was silently dropping event_name="error" events,
leaving clients hanging when Anthropic emits an error on an otherwise-200 stream
(e.g. overloaded_error while HTTP connection stays open).
"""

from __future__ import annotations

import json

from gateway.proxy.infrastructure.anthropic_upstream import (
    _translate_anthropic_sse,
)


def _parse_sse(chunks: list[bytes]) -> list[dict]:
    """Parse all non-[DONE] SSE frames into dicts."""
    parsed = []
    for chunk in chunks:
        if chunk == b"data: [DONE]\n\n":
            continue
        line = chunk.decode().removeprefix("data: ").strip()
        if line:
            parsed.append(json.loads(line))
    return parsed


def test_midstream_error_emits_terminal_error_frame_and_done() -> None:
    """An Anthropic mid-stream error event must surface as a terminal error frame + [DONE].

    Anthropic emits:
        event: error
        data: {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}
    while HTTP stays 200/open. The stepper must detect this, emit the converted
    error object, then [DONE], and mark terminal as emitted.
    """
    events = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_err_test",
                    "model": "claude-3-5-sonnet-20241022",
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                },
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "Hello"},
            },
        ),
        (
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        ),
        # message_stop MUST NOT be processed (terminal already emitted)
    ]

    chunks = list(_translate_anthropic_sse(events))

    # Terminal [DONE] must be present
    assert b"data: [DONE]\n\n" in chunks, (
        f"Expected data: [DONE] after error event; got chunks={chunks!r}"
    )

    # There must be exactly one [DONE] (finish() must not double-emit)
    done_count = sum(1 for c in chunks if c == b"data: [DONE]\n\n")
    assert done_count == 1, f"Expected exactly 1 [DONE]; got {done_count}. Chunks: {chunks!r}"

    # Find the error frame
    error_frames = [c for c in chunks if b'"error"' in c and c != b"data: [DONE]\n\n"]
    assert error_frames, f"No error frame found; chunks={chunks!r}"

    error_payload = json.loads(error_frames[0].decode().removeprefix("data: ").strip())
    assert "error" in error_payload, f"Error frame must have 'error' key; got {error_payload}"
    err = error_payload["error"]
    assert err.get("message") == "Overloaded", f"Unexpected message in error frame: {err}"
    # type is passed through (known Anthropic type)
    assert "type" in err, f"Error frame must have 'type'; got {err}"


def test_midstream_error_precedes_done() -> None:
    """The error frame must come before [DONE] in the stream."""
    events = [
        (
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        ),
    ]
    chunks = list(_translate_anthropic_sse(events))
    error_idx = next(
        (i for i, c in enumerate(chunks) if b'"error"' in c and c != b"data: [DONE]\n\n"),
        None,
    )
    done_idx = next((i for i, c in enumerate(chunks) if c == b"data: [DONE]\n\n"), None)
    assert error_idx is not None, f"No error frame found; chunks={chunks!r}"
    assert done_idx is not None, f"No [DONE] found; chunks={chunks!r}"
    assert error_idx < done_idx, (
        f"Error frame (idx={error_idx}) must precede [DONE] (idx={done_idx})"
    )


def test_midstream_error_no_double_done_when_followed_by_message_stop() -> None:
    """If message_stop follows the error event, finish() must not emit a second [DONE]."""
    events = [
        (
            "error",
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded"},
            },
        ),
        # Even if message_stop arrives after error, terminal must not double-emit
        ("message_stop", {"type": "message_stop"}),
    ]
    chunks = list(_translate_anthropic_sse(events))
    done_count = sum(1 for c in chunks if c == b"data: [DONE]\n\n")
    assert done_count == 1, (
        f"Expected exactly 1 [DONE] even with trailing message_stop; got {done_count}. "
        f"Chunks: {chunks!r}"
    )
