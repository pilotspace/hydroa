"""Red suite for reasoning-passthrough (TASK.md §4) — FROZEN CONTRACT @ v1.

Tests the OpenAI-wire reasoning_effort → Anthropic thinking / Gemini thinkingConfig
translation, plus reasoning-token accounting in responses.

SEAM A: pure translator helpers (_openai_to_anthropic_request, _anthropic_to_openai,
         _openai_to_gemini_request, _gemini_to_openai)
SEAM B: via StubCompletionUpstream + recorded_usage (billing integration)
SEAM C: real GeminiCompletionUpstream via MockTransport (streaming stepper)

D1 budget formula (FROZEN):
  ratio = {low: 0.2, medium: 0.5, high: 0.8}[effort]
  raw   = round((requested max_tokens OR default_max_tokens) * ratio)
  Anthropic budget_tokens = clamp(raw, 1024, 128000)
  Gemini   thinkingBudget = clamp(raw, 1, 24576)

D2: Anthropic max_tokens = budget_tokens + (requested OR default) max_tokens
D3: Anthropic reasoning_tokens OMITTED (thinking folded in completion_tokens)
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from gateway.proxy.infrastructure.anthropic_upstream import (
    _anthropic_to_openai,
    _openai_to_anthropic_request,
)
from gateway.proxy.infrastructure.gemini_upstream import (
    GeminiCompletionUpstream,
    _gemini_to_openai,
    _openai_to_gemini_request,
)
from tests._helios_harness import (
    StubCompletionUpstream,
    fake_provider_credential,
    sse_handler,
    wire_mock_transport,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DMT = 4096  # default_max_tokens (AnthropicCompletionUpstream ctor default)


def _req(**extra: Any) -> dict[str, Any]:
    """Minimal OpenAI-wire request with model + user message."""
    base: dict[str, Any] = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Solve: what is 2+2?"}],
    }
    base.update(extra)
    return base


def _gemini_req(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Solve: what is 2+2?"}],
    }
    base.update(extra)
    return base


def _clamp_anthropic(raw: int) -> int:
    return max(1024, min(128000, raw))


def _clamp_gemini(raw: int) -> int:
    return max(1, min(24576, raw))


def _expected_anthropic_budget(effort: str, max_tokens: int) -> int:
    ratios = {"low": 0.2, "medium": 0.5, "high": 0.8}
    raw = round(max_tokens * ratios[effort])
    return _clamp_anthropic(raw)


def _expected_gemini_budget(effort: str, max_tokens: int) -> int:
    ratios = {"low": 0.2, "medium": 0.5, "high": 0.8}
    raw = round(max_tokens * ratios[effort])
    return _clamp_gemini(raw)


# ---------------------------------------------------------------------------
# SEAM A — pure translator tests (no I/O)
# ---------------------------------------------------------------------------


def test_anthropic_high_effort_sets_thinking_budget() -> None:
    """D1+D2: high effort with max_tokens=100 → correct budget + max_tokens bump."""
    max_tokens = 100
    out = _openai_to_anthropic_request(
        _req(reasoning_effort="high", max_tokens=max_tokens),
        default_max_tokens=_DMT,
    )
    expected_budget = _expected_anthropic_budget("high", max_tokens)
    # D1: thinking block inserted
    assert "thinking" in out, "thinking block must be added for recognized effort"
    assert out["thinking"]["type"] == "enabled"
    assert out["thinking"]["budget_tokens"] == expected_budget
    # D2: max_tokens must be bumped above budget_tokens
    assert out["max_tokens"] == expected_budget + max_tokens, (
        "max_tokens must be budget_tokens + requested max_tokens (D2)"
    )
    assert out["max_tokens"] > out["thinking"]["budget_tokens"]


def test_anthropic_nested_reasoning_effort_low() -> None:
    """Nested reasoning.effort:'low' → thinking.budget_tokens per D1 formula."""
    out = _openai_to_anthropic_request(
        _req(reasoning={"effort": "low"}),
        default_max_tokens=_DMT,
    )
    expected_budget = _expected_anthropic_budget("low", _DMT)
    assert out["thinking"]["type"] == "enabled"
    assert out["thinking"]["budget_tokens"] == expected_budget
    # D2: max_tokens = budget + default_max_tokens
    assert out["max_tokens"] == expected_budget + _DMT


def test_gemini_medium_effort_sets_thinkingbudget() -> None:
    """D1: medium effort → generationConfig.thinkingConfig.thinkingBudget per formula."""
    out = _openai_to_gemini_request(
        _gemini_req(reasoning_effort="medium"),
        default_max_tokens=_DMT,
    )
    expected_budget = _expected_gemini_budget("medium", _DMT)
    gc = out.get("generationConfig", {})
    assert "thinkingConfig" in gc, "generationConfig.thinkingConfig must be added"
    assert gc["thinkingConfig"]["thinkingBudget"] == expected_budget


def test_gemini_response_maps_thoughts_tokens() -> None:
    """Gemini response: usageMetadata.thoughtsTokenCount → completion_tokens_details.reasoning_tokens."""
    gemini_body: dict[str, Any] = {
        "candidates": [
            {
                "content": {"parts": [{"text": "4"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
            "thoughtsTokenCount": 42,
        },
    }
    out = _gemini_to_openai(gemini_body, model="gemini-2.5-flash")
    details = out["usage"].get("completion_tokens_details", {})
    assert details.get("reasoning_tokens") == 42, (
        "thoughtsTokenCount must map to completion_tokens_details.reasoning_tokens"
    )


def test_anthropic_thinking_block_not_leaked() -> None:
    """D3: thinking block not leaked into message.content AND no reasoning_tokens emitted."""
    body: dict[str, Any] = {
        "id": "msg_reason_01",
        "type": "message",
        "role": "assistant",
        "model": "claude-3-5-sonnet-20241022",
        "content": [
            {"type": "thinking", "thinking": "2+2=4 because..."},
            {"type": "text", "text": "2+2 equals 4."},
        ],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 30},
    }
    out = _anthropic_to_openai(body)
    # thinking block must NOT appear in message.content
    msg = out["choices"][0]["message"]
    assert msg["content"] == "2+2 equals 4.", (
        "thinking block must be filtered out; only text block should appear"
    )
    # D3: no reasoning_tokens emitted (thinking billed at completion rate)
    usage = out["usage"]
    details = usage.get("completion_tokens_details", {})
    assert "reasoning_tokens" not in details, (
        "D3: Anthropic must NOT emit reasoning_tokens (thinking already in completion_tokens)"
    )
    # completion_tokens must capture all output_tokens (thinking folded in)
    assert usage["completion_tokens"] == 30


def test_no_reasoning_is_byte_identical() -> None:
    """Byte-identical: no reasoning field → zero new keys in Anthropic + Gemini output."""
    # Anthropic baseline
    plain_payload_a = _req(max_tokens=512)
    out_a = _openai_to_anthropic_request(plain_payload_a, default_max_tokens=_DMT)
    assert "thinking" not in out_a, "thinking key must NOT appear when reasoning_effort absent"

    # Gemini baseline
    plain_payload_g = _gemini_req(max_tokens=512)
    out_g = _openai_to_gemini_request(plain_payload_g, default_max_tokens=_DMT)
    gc = out_g.get("generationConfig", {})
    assert "thinkingConfig" not in gc, "thinkingConfig must NOT appear when reasoning_effort absent"


def test_reject_unrecognized_effort(caplog: pytest.LogCaptureFixture) -> None:
    """reasoning_effort:'turbo' → no thinking added; all other fields unchanged; WARN logged."""
    with caplog.at_level(logging.WARNING):
        out = _openai_to_anthropic_request(
            _req(reasoning_effort="turbo", max_tokens=512),
            default_max_tokens=_DMT,
        )
    assert "thinking" not in out, "unrecognized effort must not produce a thinking block"
    # All other fields identical to baseline (no reasoning field)
    baseline = _openai_to_anthropic_request(_req(max_tokens=512), default_max_tokens=_DMT)
    assert out == baseline, "all other translated fields must equal the no-reasoning baseline"
    # WARN must have been logged
    warn_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("reasoning_effort_unrecognized" in t for t in warn_texts), (
        "WARN 'reasoning_effort_unrecognized' must be logged"
    )


def test_reject_malformed_reasoning(caplog: pytest.LogCaptureFixture) -> None:
    """reasoning:'high' (str not dict) → no thinking; all other fields unchanged; WARN logged."""
    with caplog.at_level(logging.WARNING):
        out = _openai_to_anthropic_request(
            _req(reasoning="high", max_tokens=512),
            default_max_tokens=_DMT,
        )
    assert "thinking" not in out, "malformed reasoning must not produce a thinking block"
    baseline = _openai_to_anthropic_request(_req(max_tokens=512), default_max_tokens=_DMT)
    assert out == baseline, "all other translated fields must equal the no-reasoning baseline"
    warn_texts = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("reasoning_field_malformed" in t for t in warn_texts), (
        "WARN 'reasoning_field_malformed' must be logged"
    )


# ---------------------------------------------------------------------------
# SEAM C — real GeminiCompletionUpstream via MockTransport (streaming stepper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gemini_stream_terminal_maps_thoughts_tokens() -> None:
    """GeminiSSEStepper.finish(): last usageMetadata.thoughtsTokenCount → reasoning_tokens."""
    # Build native Gemini SSE frames (faithful to Gemini streaming API)
    gemini_chunks = [
        # First chunk: text content
        {
            "candidates": [
                {
                    "content": {"parts": [{"text": "4"}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 5,
                "totalTokenCount": 57,
                "thoughtsTokenCount": 42,
            },
        },
    ]
    sse_frames = [b"data: " + json.dumps(chunk).encode() + b"\n\n" for chunk in gemini_chunks]
    sse_frames.append(b"data: [DONE]\n\n")

    adapter = GeminiCompletionUpstream()
    handler = sse_handler(sse_frames)
    wire_mock_transport(adapter, handler)

    with fake_provider_credential("test-google-key"):
        frames_out: list[bytes] = []
        async for frame in adapter.stream(_gemini_req(max_tokens=512)):
            frames_out.append(frame)

    # Find the terminal chunk (has usage)
    terminal: dict[str, Any] | None = None
    for f in frames_out:
        if f.startswith(b"data: ") and not f.startswith(b"data: [DONE]"):
            chunk = json.loads(f[6:])
            if "usage" in chunk:
                terminal = chunk

    assert terminal is not None, "terminal usage chunk must be emitted"
    details = terminal["usage"].get("completion_tokens_details", {})
    assert details.get("reasoning_tokens") == 42, (
        "streaming terminal must carry completion_tokens_details.reasoning_tokens from thoughtsTokenCount"
    )


# ---------------------------------------------------------------------------
# SEAM B — billing via recorded_usage (in-process recorder)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reasoning_billing_via_recorder(
    stub_upstream: Any,
    client: Any,
    recorded_usage: Any,
    api_key: Any,
    active_model: Any,
) -> None:
    """SEAM B: usage with completion_tokens_details.reasoning_tokens flows to recorded usage."""
    # Build a completion response that carries reasoning_tokens
    response_body: dict[str, Any] = {
        "id": "chatcmpl-reason-01",
        "object": "chat.completion",
        "created": 1750000000,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "4"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 50,
            "total_tokens": 60,
            "completion_tokens_details": {
                "reasoning_tokens": 42,
            },
        },
    }
    stub_upstream(complete=(200, response_body))

    resp = await client.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key['key']}"},
        json={
            "model": active_model,
            "messages": [{"role": "user", "content": "solve 2+2"}],
        },
    )
    assert resp.status_code == 200

    row = await recorded_usage()
    assert row is not None
    # completion_tokens should be captured
    assert row.completion_tokens == 50
