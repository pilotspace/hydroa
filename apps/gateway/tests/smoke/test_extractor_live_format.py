"""Red suite for live-upstream-smoke (contract to be FROZEN @ v2).

Reproduces the billing defect found by the 2026-06-10 live OpenRouter smoke:
the SSE usage extractor parsed each NETWORK chunk independently, but live
OpenRouter splits the large final usage frame across chunk boundaries —
json.loads fails per-chunk and the ledger records 0 tokens (evidence: live
run streamed nvidia/nemotron-3-nano-30b-a3b:free, upstream usage said
prompt=24/completion=73, ledger row recorded 0/0).

These tests feed the REAL live frame shape, split exactly the way httpx
delivers it. They must be RED against the v1 extractor and green after the
live-upstream-smoke build.
"""

import json

from gateway.usage.domain.extractor import extract_usage_from_sse

# Verbatim shape captured from the live OpenRouter stream (key fields kept,
# values from the recorded smoke evidence).
LIVE_USAGE_FRAME = (
    b'data: {"id":"gen-live-1","choices":[{"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":24,"completion_tokens":73,"total_tokens":97,'
    b'"cost":0,"is_byok":false,'
    b'"prompt_tokens_details":{"cached_tokens":0,"cache_write_tokens":0,'
    b'"audio_tokens":0,"video_tokens":0},'
    b'"cost_details":{"upstream_inference_cost":0,'
    b'"upstream_inference_prompt_cost":0,'
    b'"upstream_inference_completions_cost":0},'
    b'"completion_tokens_details":{"reasoning_tokens":72,"image_tokens":0,'
    b'"audio_tokens":0}}}\n\n'
)
CONTENT_FRAME = b'data: {"id":"gen-live-1","choices":[{"delta":{"content":"SMOKE OK"}}]}\n\n'
DONE_FRAME = b"data: [DONE]\n\n"


def test_usage_frame_split_across_two_chunks() -> None:
    """The live failure mode: one SSE frame delivered as two network chunks."""
    midpoint = len(LIVE_USAGE_FRAME) // 2
    chunks = [
        CONTENT_FRAME,
        LIVE_USAGE_FRAME[:midpoint],
        LIVE_USAGE_FRAME[midpoint:],
        DONE_FRAME,
    ]

    usage = extract_usage_from_sse(chunks)

    assert usage is not None, "split-frame usage must be extracted (live defect)"
    assert usage["prompt_tokens"] == 24
    assert usage["completion_tokens"] == 73


def test_usage_frame_split_byte_by_byte() -> None:
    """Worst case: every byte its own chunk (no chunking assumption may survive)."""
    stream = CONTENT_FRAME + LIVE_USAGE_FRAME + DONE_FRAME
    chunks = [bytes([b]) for b in stream]

    usage = extract_usage_from_sse(chunks)

    assert usage is not None
    assert usage["prompt_tokens"] == 24
    assert usage["completion_tokens"] == 73


def test_whole_frame_chunks_still_extract() -> None:
    """The v1 fixture format (one frame per chunk) must keep working."""
    chunks = [CONTENT_FRAME, LIVE_USAGE_FRAME, DONE_FRAME]

    usage = extract_usage_from_sse(chunks)

    assert usage is not None
    assert usage["total_tokens"] == 97


def test_last_usage_frame_wins() -> None:
    """When multiple frames carry usage, the FINAL one is authoritative."""
    early = b'data: {"id":"gen-live-1","usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
    chunks = [early, CONTENT_FRAME, LIVE_USAGE_FRAME, DONE_FRAME]

    usage = extract_usage_from_sse(chunks)

    assert usage is not None
    assert usage["prompt_tokens"] == 24


def test_no_usage_anywhere_returns_none() -> None:
    chunks = [CONTENT_FRAME, DONE_FRAME]

    assert extract_usage_from_sse(chunks) is None


def test_live_frame_is_valid_json_when_joined() -> None:
    """Sanity guard on the fixture itself (not the implementation)."""
    payload = LIVE_USAGE_FRAME.decode().removeprefix("data: ").strip()
    parsed = json.loads(payload)

    assert parsed["usage"]["completion_tokens_details"]["reasoning_tokens"] == 72
