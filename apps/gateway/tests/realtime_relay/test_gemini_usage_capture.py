"""Red suite: Gemini Live per-turn usage capture (B2 TASK.md §2/§3, M3).

RED until GeminiLiveSession.__init__ accepts on_usage and events() fires it on a
turn-boundary message carrying usageMetadata.

Field shape LIVE-VERIFIED 2026-07-10 (ai.google.dev/api/live + a real forum-posted raw
BidiGenerateContentServerMessage payload): `usageMetadata` is a top-level sibling of
`serverContent` on the SAME server message — never nested inside it — carrying
`promptTokenCount` / `responseTokenCount` / `totalTokenCount` / `cachedContentTokenCount`
plus `promptTokensDetails` / `responseTokensDetails` arrays of {modality, tokenCount}.
This adapter fires on_usage ONLY when usageMetadata co-occurs on the exact message that
also flips serverContent.turnComplete=true (the same per-turn boundary
_translate_server_message already reads to emit {"type":"response.done"}) — never
accumulating across messages, mirroring the shipped OpenAI per-turn shape exactly and
avoiding a double-count risk if Gemini repeats usageMetadata on intermediate messages.

Pure-unit: a scripted FakeWebSocket (reused from test_openai_adapter), NO network, NO key.
"""

from __future__ import annotations

import json

from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

from .test_openai_adapter import FakeWebSocket


def _session(ws: FakeWebSocket, *, on_usage=None) -> GeminiLiveSession:
    async def _factory() -> FakeWebSocket:
        return ws

    return GeminiLiveSession(
        model="gemini-2.0-flash-exp", api_key="g-test", ws_connect=_factory, on_usage=on_usage
    )


async def _collect(session: GeminiLiveSession) -> list:
    out = []
    async for frame in session.events():
        out.append(frame)
    return out


def _turn_complete_with_usage(usage: dict) -> str:
    return json.dumps(
        {
            "serverContent": {"turnComplete": True},
            "usageMetadata": usage,
        }
    )


# ---------------------------------------------------------------------------
# M3: a Gemini relay turn with usage data now bills
# ---------------------------------------------------------------------------


async def test_turn_complete_with_usage_metadata_triggers_capture() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _turn_complete_with_usage(
                {
                    "promptTokenCount": 25,
                    "responseTokenCount": 25,
                    "totalTokenCount": 50,
                    "promptTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 0},
                        {"modality": "AUDIO", "tokenCount": 25},
                    ],
                    "responseTokensDetails": [{"modality": "AUDIO", "tokenCount": 25}],
                }
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    frames = await _collect(session)

    assert frames == [{"type": "response.done"}]  # translated frame unchanged
    assert len(captured) == 1
    usage = captured[0]
    assert usage["prompt_tokens"] == 25
    assert usage["completion_tokens"] == 25
    assert usage["input_token_details"]["audio_tokens"] == 25
    assert usage["output_token_details"]["audio_tokens"] == 25


async def test_usage_capture_maps_cached_content_tokens_to_text_tier_only() -> None:
    """cachedContentTokenCount has no modality split -> honest degrade: text-tier only,
    never guessed into the audio-tier bucket (would double-count a fabricated value)."""
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _turn_complete_with_usage(
                {
                    "promptTokenCount": 100,
                    "responseTokenCount": 10,
                    "cachedContentTokenCount": 40,
                }
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    usage = captured[0]
    assert usage["prompt_tokens_details"]["cached_tokens"] == 40
    assert usage["input_token_details"]["cached_tokens"] == 0, (
        "no audio-specific cached count exists in Gemini's shape — must degrade to 0, "
        "never reuse the combined total (would double-count against the text tier above)"
    )


async def test_multiturn_session_captures_once_per_turn_not_merged() -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            _turn_complete_with_usage({"promptTokenCount": 10, "responseTokenCount": 1}),
            _turn_complete_with_usage({"promptTokenCount": 20, "responseTokenCount": 2}),
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    assert len(captured) == 2, "each turn must capture separately, never merged into one"
    assert captured[0]["prompt_tokens"] == 10
    assert captured[1]["prompt_tokens"] == 20


# ---------------------------------------------------------------------------
# M3, Reject: a Gemini turn with no usage data records nothing, honestly
# ---------------------------------------------------------------------------


async def test_turn_complete_without_usage_metadata_records_nothing(caplog) -> None:
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(incoming=[json.dumps({"serverContent": {"turnComplete": True}})])
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    with caplog.at_level("DEBUG", logger="gateway.proxy.infrastructure.gemini_live"):
        frames = await _collect(session)

    assert frames == [{"type": "response.done"}]  # relay session undisturbed
    assert captured == []
    assert any("gemini_usage_absent_skip" in r.message for r in caplog.records)


async def test_non_turn_boundary_message_never_fires_even_with_usage_metadata() -> None:
    """usageMetadata on a message that is NOT the turnComplete boundary must not fire —
    avoids a double-count if Gemini repeats usageMetadata on intermediate messages."""
    captured: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        captured.append(usage)

    ws = FakeWebSocket(
        incoming=[
            json.dumps(
                {
                    "serverContent": {"modelTurn": {"parts": [{"text": "partial"}]}},
                    "usageMetadata": {"promptTokenCount": 5, "responseTokenCount": 1},
                }
            ),
            _turn_complete_with_usage({"promptTokenCount": 10, "responseTokenCount": 2}),
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    assert len(captured) == 1, "only the turnComplete message may trigger a capture"
    assert captured[0]["prompt_tokens"] == 10


# ---------------------------------------------------------------------------
# Capture/record failure never disrupts the live relay session
# ---------------------------------------------------------------------------


async def test_on_usage_failure_is_swallowed_never_disrupts_relay() -> None:
    async def _boom(usage: dict) -> None:
        raise RuntimeError("recorder down")

    ws = FakeWebSocket(
        incoming=[_turn_complete_with_usage({"promptTokenCount": 1, "responseTokenCount": 1})]
    )
    session = _session(ws, on_usage=_boom)
    await session.connect()
    frames = await _collect(session)  # must not raise
    assert frames == [{"type": "response.done"}]


async def test_on_usage_absent_is_backward_compatible() -> None:
    """on_usage=None (the default) -> byte-identical to pre-M3 behavior."""
    ws = FakeWebSocket(
        incoming=[_turn_complete_with_usage({"promptTokenCount": 1, "responseTokenCount": 1})]
    )
    session = _session(ws, on_usage=None)
    await session.connect()
    frames = await _collect(session)
    assert frames == [{"type": "response.done"}]
