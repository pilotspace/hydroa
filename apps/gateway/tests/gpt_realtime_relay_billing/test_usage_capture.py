"""Red suite: usage capture at the OpenAI Realtime adapter (TASK.md §2, scenarios 1-4).

RED before BUILD: `OpenAIRealtimeSession.__init__` has no `on_usage` param yet
→ TypeError on construction; `_translate_realtime_usage` does not exist yet.

Pure-unit: a scripted FakeWebSocket, NO network, NO key — mirrors
tests/realtime_relay/test_openai_adapter.py's established pattern exactly.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

from tests.gpt_realtime_relay_billing.conftest import FakeWebSocket


def _session(ws: FakeWebSocket, *, on_usage: Any = None) -> OpenAIRealtimeSession:
    async def _factory() -> FakeWebSocket:
        return ws

    return OpenAIRealtimeSession(
        model="gpt-realtime", api_key="sk-test", ws_connect=_factory, on_usage=on_usage
    )


async def _collect(session: OpenAIRealtimeSession) -> list:
    out = []
    async for frame in session.events():
        out.append(frame)
    return out


def _usage_event(*, input_tokens: int, output_tokens: int, extra: dict | None = None) -> str:
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if extra:
        usage.update(extra)
    return json.dumps({"type": "response.done", "usage": usage})


# ---------------------------------------------------------------------------
# Scenario 1 — One usage_records row per completed turn
# ---------------------------------------------------------------------------


async def test_response_done_with_usage_triggers_exactly_one_capture() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _usage_event(
                input_tokens=10,
                output_tokens=5,
                extra={
                    "input_token_details": {"cached_tokens": 2, "audio_tokens": 0},
                    "output_token_details": {"audio_tokens": 0},
                },
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    frames = await _collect(session)

    assert frames == [{"type": "response.done"}]  # translated frame unchanged
    assert len(captured) == 1


# ---------------------------------------------------------------------------
# Scenario 2 — A multi-turn session bills per turn, not per session
# ---------------------------------------------------------------------------


async def test_multiturn_session_captures_once_per_turn_not_merged() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _usage_event(input_tokens=10, output_tokens=1),
            _usage_event(input_tokens=20, output_tokens=2),
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    assert len(captured) == 2, "each turn must capture separately, never merged into one"
    assert captured[0]["prompt_tokens"] == 10
    assert captured[1]["prompt_tokens"] == 20


# ---------------------------------------------------------------------------
# Scenario 3 — Raw usage is captured before translation discards it
# ---------------------------------------------------------------------------


async def test_raw_usage_captured_before_translation_discards_it() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _usage_event(
                input_tokens=42,
                output_tokens=7,
                extra={
                    "input_token_details": {"audio_tokens": 30, "cached_tokens": 0},
                    "output_token_details": {"audio_tokens": 5},
                },
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    frames = await _collect(session)

    # The frame yielded to the client is byte-identical to today — usage is gone.
    assert frames == [{"type": "response.done"}]
    # But the capture callback saw it, translated to the recorder-canonical shape.
    assert captured[0]["prompt_tokens"] == 42
    assert captured[0]["completion_tokens"] == 7
    assert captured[0]["input_token_details"]["audio_tokens"] == 30
    assert captured[0]["output_token_details"]["audio_tokens"] == 5


# ---------------------------------------------------------------------------
# Regression: cached_tokens_details is TEXT/AUDIO split one level deeper than
# input_token_details.cached_tokens (which is the COMBINED total, not audio-specific).
# Caught by adversarial review before VERIFY — a real double/mis-count defect.
# ---------------------------------------------------------------------------


async def test_translate_realtime_usage_splits_combined_cached_into_text_and_audio() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    # Real documented OpenAI Realtime shape: input_token_details.cached_tokens (640) is
    # the COMBINED text+audio cached total; the split lives at cached_tokens_details.
    ws = FakeWebSocket(
        incoming=[
            _usage_event(
                input_tokens=789,
                output_tokens=100,
                extra={
                    "input_token_details": {
                        "text_tokens": 313,
                        "audio_tokens": 476,
                        "cached_tokens": 640,
                        "cached_tokens_details": {"text_tokens": 256, "audio_tokens": 384},
                    },
                    "output_token_details": {"audio_tokens": 40},
                },
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    usage = captured[0]
    # Text-tier cached_tokens must be the TEXT-only split (256), never the combined total (640).
    assert usage["prompt_tokens_details"]["cached_tokens"] == 256
    # Audio-tier cached_tokens (read via input_token_details.cached_tokens downstream) must be
    # the AUDIO-only split (384), never the combined total (640) and never the same value as
    # the text-tier cached_tokens above.
    assert usage["input_token_details"]["cached_tokens"] == 384
    assert usage["input_token_details"]["audio_tokens"] == 476


# ---------------------------------------------------------------------------
# Scenario 4 — Gemini Live stays byte-identical
# ---------------------------------------------------------------------------


def test_gemini_live_constructor_has_no_on_usage_param() -> None:
    from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

    sig = inspect.signature(GeminiLiveSession.__init__)
    assert "on_usage" not in sig.parameters, (
        "GeminiLiveSession must stay untouched by this task — no usage-capture param"
    )
