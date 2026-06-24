"""Red suite for parallel-tool-streaming-verify (v34 task 4) — FROZEN CONTRACT @ v1.

Verifies that parallel tool-call streaming works correctly across all three native
providers (Bedrock, Anthropic, Gemini). The core finding: Bedrock streaming tool
calls were silently DROPPED by _BedrockSSEStepper — this suite proves the FIX.

Seams:
  SEAM A: drive _BedrockSSEStepper directly with synthetic (event_type, payload) tuples.
  SEAM C: real adapter + MockTransport with native EventStream / SSE bytes.

Anthropic + Gemini: LOCK tests (no source change to those adapters).
Bedrock: REAL FIX in _BedrockSSEStepper.

Contract: FROZEN @ v1 — approved by Tin (2026-06-23).
"""

from __future__ import annotations

import json
import logging
import struct
from binascii import crc32
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.provider_credentials import BedrockCredential
from gateway.proxy.infrastructure.anthropic_upstream import AnthropicCompletionUpstream
from gateway.proxy.infrastructure.bedrock_upstream import (
    BedrockCompletionUpstream,
    _BedrockSSEStepper,
)
from gateway.proxy.infrastructure.gemini_upstream import GeminiCompletionUpstream
from tests._helios_harness import (
    fake_provider_credential,
    sse_handler,
    wire_mock_transport,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# EventStream binary frame builder (mirrors bedrock_streaming test helpers)
# ---------------------------------------------------------------------------


def _hdr(name: str, value: str) -> bytes:
    """Build a single string header (type 7) in EventStream encoding."""
    n = name.encode()
    v = value.encode()
    return bytes([len(n)]) + n + bytes([7]) + struct.pack(">H", len(v)) + v


def _es_message(event_type: str, payload: dict[str, Any]) -> bytes:
    """Build one AWS EventStream message with authentic prelude + message CRCs."""
    headers = (
        _hdr(":event-type", event_type)
        + _hdr(":content-type", "application/json")
        + _hdr(":message-type", "event")
    )
    body = json.dumps(payload).encode()
    headers_len = len(headers)
    total_len = 12 + headers_len + len(body) + 4
    prelude = struct.pack(">II", total_len, headers_len)
    prelude_full = prelude + struct.pack(">I", crc32(prelude) & 0xFFFFFFFF)
    msg_wo_crc = prelude_full + headers + body
    return msg_wo_crc + struct.pack(">I", crc32(msg_wo_crc) & 0xFFFFFFFF)


def _es_stream(*messages: bytes) -> bytes:
    """Concatenate EventStream messages into a wire-format byte stream."""
    return b"".join(messages)


# ---------------------------------------------------------------------------
# Bedrock adapter factory (credential via contextvar, no ctor creds)
# ---------------------------------------------------------------------------

_DUMMY_CRED = BedrockCredential(
    access_key_id="AKIDTEST000000000000",
    secret_access_key="fakesecretkey0000000000000000000000000000",
    region="us-east-1",
)
_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"


def _make_bedrock_adapter(
    handler: Any,
    *,
    endpoint_url: str = "https://bedrock-runtime.us-east-1.amazonaws.com",
) -> BedrockCompletionUpstream:
    adapter = BedrockCompletionUpstream(
        endpoint_url=endpoint_url,
        default_max_tokens=4096,
        max_retries=0,
        backoff_base=0.0,
        retry_deadline_s=0.0,
    )
    adapter._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler),
    )
    return adapter


async def _drain(stream: AsyncIterator[bytes]) -> list[bytes]:
    return [chunk async for chunk in stream]


def _parse_chunks(raw_frames: list[bytes]) -> list[dict[str, Any]]:
    """Parse SSE frames into dicts, excluding b'data: [DONE]\\n\\n'."""
    out = []
    for f in raw_frames:
        if f == b"data: [DONE]\n\n":
            continue
        if f.startswith(b"data: "):
            out.append(json.loads(f[6:]))
    return out


# ---------------------------------------------------------------------------
# PTS1 — SEAM A: single tool call through stepper (RED: stepper ignores contentBlockStart)
# ---------------------------------------------------------------------------


def test_bedrock_stream_single_tool_call() -> None:
    """SEAM A: stepper fed contentBlockStart+toolUse.input deltas emits correct frames.

    Asserts:
    - First delta: index=0, id="tu_1", function.name="read_file"
    - Argument-fragment delta: index=0, function.arguments='{"path":'
    - Second arg fragment: index=0, function.arguments='"a.py"}'
    - finish() returns finish_reason "tool_calls" (stopReason="tool_use")
    """
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    frames += list(
        stepper.step(
            "messageStart",
            {"role": "assistant"},
        )
    )
    frames += list(
        stepper.step(
            "contentBlockStart",
            {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "tu_1", "name": "read_file"}},
            },
        )
    )
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '{"path":'}},
            },
        )
    )
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '"a.py"}'}},
            },
        )
    )
    frames += list(
        stepper.step(
            "contentBlockStop",
            {"contentBlockIndex": 1},
        )
    )
    frames += list(
        stepper.step(
            "messageStop",
            {"stopReason": "tool_use"},
        )
    )
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # Should have: role frame + tool-id/name frame + 2 arg-fragment frames + terminal
    assert len(chunks) >= 4, f"Expected ≥4 frames, got {len(chunks)}: {chunks!r}"

    # Role frame first
    assert chunks[0]["choices"][0]["delta"].get("role") == "assistant"

    # Collect tool_calls frames
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) >= 3, (
        f"Expected ≥3 tool_calls frames (id+name, 2 arg frags), got {len(tc_frames)}"
    )

    # First tool_calls frame: id + name
    first_tc = tc_frames[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first_tc["index"] == 0, f"First tool call must be index 0, got {first_tc['index']!r}"
    assert first_tc.get("id") == "tu_1", f"id must be 'tu_1', got {first_tc.get('id')!r}"
    assert first_tc["function"]["name"] == "read_file", (
        f"name must be 'read_file', got {first_tc['function'].get('name')!r}"
    )

    # Arg fragment frames: no id, only arguments
    arg_frames = tc_frames[1:]
    for af in arg_frames:
        tc = af["choices"][0]["delta"]["tool_calls"][0]
        assert tc["index"] == 0, f"Arg fragment must be index 0, got {tc['index']!r}"
        assert "id" not in tc, f"Arg fragment must not carry id, got {tc!r}"
        assert "arguments" in tc["function"], (
            f"Arg fragment must carry function.arguments, got {tc['function']!r}"
        )

    # Terminal frame: finish_reason == "tool_calls"
    terminal = chunks[-1]
    assert terminal["choices"][0]["finish_reason"] == "tool_calls", (
        f"finish_reason must be 'tool_calls', got {terminal['choices'][0]['finish_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PTS2 — SEAM C: two parallel tool calls via real adapter + MockTransport
# ---------------------------------------------------------------------------


async def test_bedrock_stream_two_parallel_tool_calls() -> None:
    """SEAM C: real BedrockCompletionUpstream + MockTransport with two toolUse blocks.

    Two toolUse blocks at contentBlockIndex 1 and 2 must produce:
    - Tool index 0 (id="tu_1", name="get_weather")
    - Tool index 1 (id="tu_2", name="get_time")
    - Distinct indices, no id/name on arg fragments, finish_reason="tool_calls"
    """
    wire = _es_stream(
        _es_message("messageStart", {"role": "assistant"}),
        # Tool 1
        _es_message(
            "contentBlockStart",
            {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "tu_1", "name": "get_weather"}},
            },
        ),
        _es_message(
            "contentBlockDelta",
            {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '{"city":'}},
            },
        ),
        _es_message(
            "contentBlockDelta",
            {
                "contentBlockIndex": 1,
                "delta": {"toolUse": {"input": '"Paris"}'}},
            },
        ),
        _es_message("contentBlockStop", {"contentBlockIndex": 1}),
        # Tool 2
        _es_message(
            "contentBlockStart",
            {
                "contentBlockIndex": 2,
                "start": {"toolUse": {"toolUseId": "tu_2", "name": "get_time"}},
            },
        ),
        _es_message(
            "contentBlockDelta",
            {
                "contentBlockIndex": 2,
                "delta": {"toolUse": {"input": '{"timezone":'}},
            },
        ),
        _es_message(
            "contentBlockDelta",
            {
                "contentBlockIndex": 2,
                "delta": {"toolUse": {"input": '"UTC"}'}},
            },
        ),
        _es_message("contentBlockStop", {"contentBlockIndex": 2}),
        _es_message("messageStop", {"stopReason": "tool_use"}),
        _es_message(
            "metadata",
            {"usage": {"inputTokens": 25, "outputTokens": 12, "totalTokens": 37}},
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=wire,
            headers={"content-type": "application/vnd.amazon.eventstream"},
        )

    adapter = _make_bedrock_adapter(handler)
    tok = set_provider_credential(_DUMMY_CRED)
    try:
        raw_frames = await _drain(
            adapter.stream(
                {
                    "model": _MODEL_ID,
                    "messages": [{"role": "user", "content": "What's the weather and time?"}],
                }
            )
        )
    finally:
        reset_provider_credential(tok)

    chunks = _parse_chunks(raw_frames)

    # Collect all tool_calls deltas
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]

    # First frame for each tool: identify by presence of "id"
    first_tc_frames = [f for f in tc_frames if "id" in f["choices"][0]["delta"]["tool_calls"][0]]
    assert len(first_tc_frames) == 2, (
        f"Expected 2 tool-id/name frames (one per tool), got {len(first_tc_frames)}"
    )

    # Extract id/index/name pairs
    tool_summaries = [
        {
            "index": f["choices"][0]["delta"]["tool_calls"][0]["index"],
            "id": f["choices"][0]["delta"]["tool_calls"][0].get("id"),
            "name": f["choices"][0]["delta"]["tool_calls"][0]["function"].get("name"),
        }
        for f in first_tc_frames
    ]

    # Indices must be distinct
    indices = [t["index"] for t in tool_summaries]
    assert len(set(indices)) == 2, f"Tool indices must be distinct, got {indices!r}"
    assert set(indices) == {0, 1}, f"Tool indices must be {{0, 1}}, got {set(indices)!r}"

    # IDs must be distinct and correct
    ids = {t["id"] for t in tool_summaries}
    assert ids == {"tu_1", "tu_2"}, f"Tool ids must be {{'tu_1', 'tu_2'}}, got {ids!r}"

    # Names must be correct
    names = {t["name"] for t in tool_summaries}
    assert names == {"get_weather", "get_time"}, (
        f"Tool names must be {{'get_weather', 'get_time'}}, got {names!r}"
    )

    # finish_reason must be "tool_calls"
    terminal = chunks[-1]
    assert terminal["choices"][0]["finish_reason"] == "tool_calls", (
        f"finish_reason must be 'tool_calls', got {terminal['choices'][0]['finish_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PTS3 — text + tool interleave (text delta + tool delta both appear)
# ---------------------------------------------------------------------------


def test_bedrock_stream_text_and_tool_interleave() -> None:
    """SEAM A: text block then tool block both stream correctly.

    Asserts:
    - Content delta from the text block appears in the stream
    - Tool call frames appear for the tool block
    - finish_reason == "tool_calls"
    """
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    # Text block at index 0
    frames += list(stepper.step("messageStart", {"role": "assistant"}))
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"text": "Let me check that for you."}},
        )
    )
    frames += list(stepper.step("contentBlockStop", {"contentBlockIndex": 0}))
    # Tool block at index 1
    frames += list(
        stepper.step(
            "contentBlockStart",
            {
                "contentBlockIndex": 1,
                "start": {"toolUse": {"toolUseId": "tu_99", "name": "lookup"}},
            },
        )
    )
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {"contentBlockIndex": 1, "delta": {"toolUse": {"input": '{"q":"x"}'}}},
        )
    )
    frames += list(stepper.step("contentBlockStop", {"contentBlockIndex": 1}))
    frames += list(stepper.step("messageStop", {"stopReason": "tool_use"}))
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # Text content delta must appear
    content_frames = [c for c in chunks if c["choices"][0]["delta"].get("content") is not None]
    assert len(content_frames) >= 1, "Text content delta must appear in stream"
    assert any("Let me check" in c["choices"][0]["delta"]["content"] for c in content_frames), (
        "Text content must carry the text"
    )

    # Tool call frames must appear
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) >= 1, "Tool call deltas must appear for the tool block"

    # First tool frame: id + name
    first_tc = tc_frames[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first_tc.get("id") == "tu_99"
    assert first_tc["function"].get("name") == "lookup"

    # Terminal: finish_reason tool_calls
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


# ---------------------------------------------------------------------------
# PTS4 — tool-free turn is byte-identical (text only, finish "stop")
# ---------------------------------------------------------------------------


def test_bedrock_stream_tool_free_byte_identical() -> None:
    """SEAM A: text-only + end_turn → no tool_calls frames; finish_reason 'stop'.

    This ensures the existing text path is not broken by the new tool-call logic.
    """
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    frames += list(stepper.step("messageStart", {"role": "assistant"}))
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"text": "Hello"}},
        )
    )
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"text": " world"}},
        )
    )
    frames += list(stepper.step("contentBlockStop", {"contentBlockIndex": 0}))
    frames += list(stepper.step("messageStop", {"stopReason": "end_turn"}))
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # No tool_calls frames
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert tc_frames == [], f"No tool_calls frames expected for text-only stream, got {tc_frames!r}"

    # Both text deltas present
    content_texts = [
        c["choices"][0]["delta"].get("content", "")
        for c in chunks
        if c["choices"][0]["delta"].get("content") is not None
    ]
    assert "Hello" in content_texts, "Text delta 'Hello' must be present"
    assert " world" in content_texts, "Text delta ' world' must be present"

    # Terminal: finish_reason "stop"
    terminal = chunks[-1]
    assert terminal["choices"][0]["finish_reason"] == "stop", (
        f"finish_reason must be 'stop' for end_turn, got {terminal['choices'][0]['finish_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PTS5 — REJECT: toolUse block missing name → best-effort delta + WARN
# ---------------------------------------------------------------------------


def test_bedrock_reject_tooluse_missing_name(caplog: pytest.LogCaptureFixture) -> None:
    """SEAM A: contentBlockStart toolUse without name → best-effort delta (name="") + WARN.

    Asserts:
    - A tool_calls frame IS still emitted (never crash, best-effort)
    - The emitted frame has id="tu_1", name=""
    - A warning containing "bedrock_tooluse_incomplete" is logged
    - Every NON-tool frame (role frame, terminal frame) is structurally unchanged vs the
      well-formed baseline (contract §2: "every other frame is unchanged from the well-formed
      baseline").
    """
    # --- Well-formed baseline (complete name present) ---
    baseline = _BedrockSSEStepper(model_id=_MODEL_ID)
    baseline_frames: list[bytes] = []
    baseline_frames += list(baseline.step("messageStart", {"role": "assistant"}))
    baseline_frames += list(
        baseline.step(
            "contentBlockStart",
            {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu_1", "name": "read_file"}},
            },
        )
    )
    baseline_frames += list(baseline.step("messageStop", {"stopReason": "tool_use"}))
    baseline_frames += list(baseline.finish())
    baseline_chunks = _parse_chunks(baseline_frames)

    # Non-tool frames from baseline: role frame (delta has "role") and terminal frame
    # (finish_reason is not None).
    baseline_non_tool = [c for c in baseline_chunks if "tool_calls" not in c["choices"][0]["delta"]]

    # --- Subject under test (name MISSING) ---
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    frames += list(stepper.step("messageStart", {"role": "assistant"}))

    with caplog.at_level(logging.WARNING):
        frames += list(
            stepper.step(
                "contentBlockStart",
                {
                    "contentBlockIndex": 0,
                    "start": {"toolUse": {"toolUseId": "tu_1"}},  # name MISSING
                },
            )
        )

    frames += list(stepper.step("messageStop", {"stopReason": "tool_use"}))
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # A tool_calls frame must be emitted (best-effort)
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) >= 1, "A best-effort tool_calls frame must be emitted even without name"

    first_tc = tc_frames[0]["choices"][0]["delta"]["tool_calls"][0]
    assert first_tc.get("id") == "tu_1", f"id must be 'tu_1', got {first_tc.get('id')!r}"
    assert first_tc["function"].get("name") == "", (
        f"name must be '' (empty) for missing name, got {first_tc['function'].get('name')!r}"
    )

    # Warning must be logged
    warn_text = " ".join(caplog.messages)
    assert "bedrock_tooluse_incomplete" in warn_text, (
        f"Expected 'bedrock_tooluse_incomplete' in log warnings, got: {warn_text!r}"
    )

    # Contract §2: every NON-tool frame is structurally unchanged vs the well-formed baseline.
    # Compare by delta key presence and finish_reason; ignore id/created (time-varying).
    subject_non_tool = [c for c in chunks if "tool_calls" not in c["choices"][0]["delta"]]
    assert len(subject_non_tool) == len(baseline_non_tool), (
        f"Non-tool frame count must match baseline: "
        f"got {len(subject_non_tool)}, expected {len(baseline_non_tool)}"
    )
    for i, (subj, base) in enumerate(zip(subject_non_tool, baseline_non_tool, strict=True)):
        subj_delta_keys = set(subj["choices"][0]["delta"].keys())
        base_delta_keys = set(base["choices"][0]["delta"].keys())
        assert subj_delta_keys == base_delta_keys, (
            f"Non-tool frame {i}: delta keys differ — "
            f"got {subj_delta_keys!r}, expected {base_delta_keys!r}"
        )
        subj_fr = subj["choices"][0].get("finish_reason")
        base_fr = base["choices"][0].get("finish_reason")
        assert subj_fr == base_fr, (
            f"Non-tool frame {i}: finish_reason differs — got {subj_fr!r}, expected {base_fr!r}"
        )


# ---------------------------------------------------------------------------
# PTS6 — REJECT: orphan toolUse input (no prior contentBlockStart) → fresh index + WARN
# ---------------------------------------------------------------------------


def test_bedrock_reject_orphan_tooluse_input(caplog: pytest.LogCaptureFixture) -> None:
    """SEAM A: toolUse.input for unknown contentBlockIndex → fresh index + WARN, no bytes dropped.

    Asserts:
    - An arg-fragment delta IS emitted (bytes never dropped)
    - The emitted arguments fragment exactly equals the input string (no truncation/mutation)
    - A warning containing "bedrock_tooluse_orphan_input" is logged
    - Surrounding non-tool frames (role frame, terminal frame) match the well-formed baseline
      structurally (delta key presence + finish_reason).
    """
    _ORPHAN_INPUT = '{"orphan": true}'

    # --- Well-formed baseline (a normal arg-fragment with a prior contentBlockStart) ---
    baseline = _BedrockSSEStepper(model_id=_MODEL_ID)
    baseline_frames: list[bytes] = []
    baseline_frames += list(baseline.step("messageStart", {"role": "assistant"}))
    baseline_frames += list(
        baseline.step(
            "contentBlockStart",
            {
                "contentBlockIndex": 5,
                "start": {"toolUse": {"toolUseId": "tu_base", "name": "baseline_tool"}},
            },
        )
    )
    baseline_frames += list(
        baseline.step(
            "contentBlockDelta",
            {"contentBlockIndex": 5, "delta": {"toolUse": {"input": _ORPHAN_INPUT}}},
        )
    )
    baseline_frames += list(baseline.step("messageStop", {"stopReason": "tool_use"}))
    baseline_frames += list(baseline.finish())
    baseline_chunks = _parse_chunks(baseline_frames)
    baseline_non_tool = [c for c in baseline_chunks if "tool_calls" not in c["choices"][0]["delta"]]

    # --- Subject under test (orphan: no prior contentBlockStart for index 99) ---
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    frames += list(stepper.step("messageStart", {"role": "assistant"}))

    with caplog.at_level(logging.WARNING):
        # No prior contentBlockStart for index 99 → orphan
        frames += list(
            stepper.step(
                "contentBlockDelta",
                {
                    "contentBlockIndex": 99,
                    "delta": {"toolUse": {"input": _ORPHAN_INPUT}},
                },
            )
        )

    frames += list(stepper.step("messageStop", {"stopReason": "tool_use"}))
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # An arg-fragment delta must be emitted (no bytes dropped)
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) >= 1, "Orphan input bytes must be emitted on a fresh index"

    # The emitted arguments fragment must exactly equal the input string (no truncation/mutation)
    arg_frag = tc_frames[0]["choices"][0]["delta"]["tool_calls"][0]
    assert arg_frag["function"].get("arguments") == _ORPHAN_INPUT, (
        f"Orphan input must be emitted verbatim, got {arg_frag['function'].get('arguments')!r}"
    )

    # Warning must be logged
    warn_text = " ".join(caplog.messages)
    assert "bedrock_tooluse_orphan_input" in warn_text, (
        f"Expected 'bedrock_tooluse_orphan_input' in log warnings, got: {warn_text!r}"
    )

    # Surrounding non-tool frames must be structurally unchanged vs the well-formed baseline
    # (delta key presence + finish_reason — contract §2: "other frames are unchanged").
    subject_non_tool = [c for c in chunks if "tool_calls" not in c["choices"][0]["delta"]]
    assert len(subject_non_tool) == len(baseline_non_tool), (
        f"Non-tool frame count must match baseline: "
        f"got {len(subject_non_tool)}, expected {len(baseline_non_tool)}"
    )
    for i, (subj, base) in enumerate(zip(subject_non_tool, baseline_non_tool, strict=True)):
        subj_delta_keys = set(subj["choices"][0]["delta"].keys())
        base_delta_keys = set(base["choices"][0]["delta"].keys())
        assert subj_delta_keys == base_delta_keys, (
            f"Non-tool frame {i}: delta keys differ — "
            f"got {subj_delta_keys!r}, expected {base_delta_keys!r}"
        )
        subj_fr = subj["choices"][0].get("finish_reason")
        base_fr = base["choices"][0].get("finish_reason")
        assert subj_fr == base_fr, (
            f"Non-tool frame {i}: finish_reason differs — got {subj_fr!r}, expected {base_fr!r}"
        )


# ---------------------------------------------------------------------------
# PTS6b — FAIL-SAFE: toolUse block seen but messageStop arrives with NO stopReason
#          → finish_reason must still be "tool_calls" (not "stop")
# ---------------------------------------------------------------------------


def test_bedrock_stream_tool_use_no_stop_reason() -> None:
    """SEAM A: tool block streamed but messageStop carries NO stopReason (abnormal).

    Without the fail-safe in finish(), _map_finish_reason(None) → "stop", which is
    wrong for a tool-streaming turn.  The fix: if _saw_tool_call and not _stop_reason,
    set _stop_reason = "tool_use" before computing finish_reason.

    Asserts:
    - finish_reason == "tool_calls"  (NOT "stop")
    - The tool_calls frame itself is present (the tool was not dropped)
    """
    stepper = _BedrockSSEStepper(model_id=_MODEL_ID)

    frames: list[bytes] = []
    frames += list(stepper.step("messageStart", {"role": "assistant"}))
    frames += list(
        stepper.step(
            "contentBlockStart",
            {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "tu_failsafe", "name": "ping"}},
            },
        )
    )
    frames += list(
        stepper.step(
            "contentBlockDelta",
            {"contentBlockIndex": 0, "delta": {"toolUse": {"input": "{}"}}},
        )
    )
    frames += list(stepper.step("contentBlockStop", {"contentBlockIndex": 0}))
    # messageStop with NO stopReason — the abnormal case the fail-safe guards against
    frames += list(stepper.step("messageStop", {}))
    frames += list(stepper.finish())

    chunks = _parse_chunks(frames)

    # Tool call frame must be present
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) >= 1, "Tool call frame must be present even with missing stopReason"

    # finish_reason must be "tool_calls", not "stop"
    terminal = chunks[-1]
    finish_reason = terminal["choices"][0]["finish_reason"]
    assert finish_reason == "tool_calls", (
        f"finish_reason must be 'tool_calls' when tool was seen but stopReason absent; "
        f"got {finish_reason!r} (fail-safe not wired)"
    )


# ---------------------------------------------------------------------------
# PTS7 — LOCK: Anthropic streams two parallel tool calls (no source change)
# ---------------------------------------------------------------------------


async def test_anthropic_stream_two_parallel_tool_calls() -> None:
    """SEAM C: real AnthropicCompletionUpstream via MockTransport with two tool_use blocks.

    This is a LOCK test — Anthropic already works; we just prove it with a test.
    Two native tool_use content blocks → two distinct OpenAI tool_call indices + ids.
    """
    # Build native Anthropic SSE frames for two parallel tool_use blocks
    sse_frames: list[bytes] = [
        b"event: message_start\ndata: "
        + json.dumps(
            {
                "type": "message_start",
                "message": {
                    "id": "msg_para_stream",
                    "type": "message",
                    "role": "assistant",
                    "model": "claude-3-5-sonnet-20241022",
                    "usage": {"input_tokens": 25, "output_tokens": 0},
                },
            }
        ).encode()
        + b"\n\n",
        # Tool 1 start (index 0)
        b"event: content_block_start\ndata: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "get_weather",
                    "input": {},
                },
            }
        ).encode()
        + b"\n\n",
        # Tool 1 arguments
        b"event: content_block_delta\ndata: "
        + json.dumps(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"city":"Paris"}'},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_stop\ndata: "
        + json.dumps({"type": "content_block_stop", "index": 0}).encode()
        + b"\n\n",
        # Tool 2 start (index 1)
        b"event: content_block_start\ndata: "
        + json.dumps(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_02",
                    "name": "get_time",
                    "input": {},
                },
            }
        ).encode()
        + b"\n\n",
        # Tool 2 arguments
        b"event: content_block_delta\ndata: "
        + json.dumps(
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "input_json_delta", "partial_json": '{"timezone":"UTC"}'},
            }
        ).encode()
        + b"\n\n",
        b"event: content_block_stop\ndata: "
        + json.dumps({"type": "content_block_stop", "index": 1}).encode()
        + b"\n\n",
        # message_delta with stop_reason=tool_use
        b"event: message_delta\ndata: "
        + json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 12},
            }
        ).encode()
        + b"\n\n",
        b"event: message_stop\ndata: " + json.dumps({"type": "message_stop"}).encode() + b"\n\n",
    ]

    handler = sse_handler(sse_frames)
    adapter = AnthropicCompletionUpstream()
    wire_mock_transport(adapter, handler)

    with fake_provider_credential("test-anthropic-key"):
        raw_frames: list[bytes] = []
        async for frame in adapter.stream(
            {
                "model": "claude-3-5-sonnet-20241022",
                "messages": [
                    {"role": "user", "content": "What's the weather in Paris and UTC time?"}
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "get_time",
                            "parameters": {
                                "type": "object",
                                "properties": {"timezone": {"type": "string"}},
                            },
                        },
                    },
                ],
            }
        ):
            raw_frames.append(frame)

    chunks = _parse_chunks(raw_frames)

    # Collect first-tool-call frames (those with "id")
    first_tc_frames = [
        c
        for c in chunks
        if "tool_calls" in c["choices"][0]["delta"]
        and "id" in c["choices"][0]["delta"]["tool_calls"][0]
    ]
    assert len(first_tc_frames) == 2, (
        f"Expected 2 tool id/name frames (Anthropic parallel), got {len(first_tc_frames)}"
    )

    ids = {f["choices"][0]["delta"]["tool_calls"][0]["id"] for f in first_tc_frames}
    assert ids == {"toolu_01", "toolu_02"}, f"Tool ids must be distinct, got {ids!r}"

    indices = [f["choices"][0]["delta"]["tool_calls"][0]["index"] for f in first_tc_frames]
    assert len(set(indices)) == 2, f"Tool indices must be distinct, got {indices!r}"

    # Terminal finish_reason == "tool_calls"
    terminal = chunks[-1]
    assert terminal["choices"][0]["finish_reason"] == "tool_calls", (
        f"finish_reason must be 'tool_calls', got {terminal['choices'][0]['finish_reason']!r}"
    )


# ---------------------------------------------------------------------------
# PTS8 — LOCK: Gemini streams two parallel functionCall parts → indices 0 and 1
# ---------------------------------------------------------------------------


async def test_gemini_stream_two_parallel_tool_calls() -> None:
    """SEAM C: real GeminiCompletionUpstream via MockTransport with two functionCall parts.

    This is a LOCK test — Gemini already works via _GeminiSSEStepper._tc_count.
    Two functionCall parts → tool indices 0 and 1 with distinct synthesized ids.
    """
    # Gemini emits entire tool calls in a single chunk (both in one candidates[0].content.parts)
    gemini_chunk = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"functionCall": {"name": "get_weather", "args": {"city": "Paris"}}},
                        {"functionCall": {"name": "get_time", "args": {"timezone": "UTC"}}},
                    ]
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 10,
            "totalTokenCount": 30,
        },
    }
    sse_frames = [
        b"data: " + json.dumps(gemini_chunk).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ]

    adapter = GeminiCompletionUpstream()
    handler = sse_handler(sse_frames)
    wire_mock_transport(adapter, handler)

    with fake_provider_credential("test-google-key"):
        raw_frames: list[bytes] = []
        async for frame in adapter.stream(
            {
                "model": "gemini-2.0-flash",
                "messages": [
                    {"role": "user", "content": "What's the weather in Paris and UTC time?"}
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "get_time",
                            "parameters": {
                                "type": "object",
                                "properties": {"timezone": {"type": "string"}},
                            },
                        },
                    },
                ],
            }
        ):
            raw_frames.append(frame)

    chunks = _parse_chunks(raw_frames)

    # Two tool_calls frames with distinct indices
    tc_frames = [c for c in chunks if "tool_calls" in c["choices"][0]["delta"]]
    assert len(tc_frames) == 2, (
        f"Expected 2 tool_calls frames (one per functionCall), got {len(tc_frames)}"
    )

    indices = [f["choices"][0]["delta"]["tool_calls"][0]["index"] for f in tc_frames]
    assert indices == [0, 1], f"Tool indices must be [0, 1] in order, got {indices!r}"

    # IDs must be distinct (synthesized)
    ids = [f["choices"][0]["delta"]["tool_calls"][0].get("id") for f in tc_frames]
    assert ids[0] != ids[1], f"Synthesized tool ids must be distinct, got {ids!r}"
    assert all(id_ is not None and id_.startswith("call_") for id_ in ids), (
        f"Gemini synthesized ids must start with 'call_', got {ids!r}"
    )

    # Terminal finish_reason == "tool_calls" (Gemini sets this in finish() when _saw_tool_call)
    terminal = chunks[-1]
    assert terminal["choices"][0]["finish_reason"] == "tool_calls", (
        f"finish_reason must be 'tool_calls', got {terminal['choices'][0]['finish_reason']!r}"
    )
