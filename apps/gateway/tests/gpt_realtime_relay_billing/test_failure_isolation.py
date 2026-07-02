"""Red suite: billing-pipe failure isolation + REJECTIONs (TASK.md §2, scenarios 12-16).

RED before BUILD: OpenAIRealtimeSession has no on_usage/_translate_realtime_usage yet,
and RecordingUsageRecorder has no audio-tier reads yet — every test below either
TypeErrors on construction or fails its behavioral assertion for the right reason.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

from tests.gpt_realtime_relay_billing.conftest import (
    FakeSession,
    FakeSessionFactory,
    FakeWebSocket,
    StreamCapture,
)


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


# ---------------------------------------------------------------------------
# Scenario 12 — A billing-pipe failure never disrupts the live session
# ---------------------------------------------------------------------------


async def test_on_usage_exception_does_not_disrupt_relay_frames() -> None:
    async def _boom(usage: dict) -> None:
        raise RuntimeError("redis down")

    ws = FakeWebSocket(
        incoming=[
            json.dumps({"type": "response.done", "usage": {"input_tokens": 1, "output_tokens": 1}}),
            json.dumps({"type": "response.done", "usage": {"input_tokens": 2, "output_tokens": 2}}),
        ]
    )
    session = _session(ws, on_usage=_boom)
    await session.connect()
    frames = await _collect(session)

    # Both turns' frames arrive, in order, despite EVERY on_usage call raising.
    assert frames == [{"type": "response.done"}, {"type": "response.done"}]


# ---------------------------------------------------------------------------
# Scenario 13 — response.done with no usage object is skipped (REJECTION)
# ---------------------------------------------------------------------------


async def test_response_done_without_usage_skips_capture() -> None:
    called: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        called.append(usage)

    ws = FakeWebSocket(incoming=[json.dumps({"type": "response.done"})])
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    frames = await _collect(session)

    assert frames == [{"type": "response.done"}]
    assert called == [], "no usage field present -> usage_absent_skip: callback never invoked"


# ---------------------------------------------------------------------------
# Scenario 14 — Malformed usage shape (not a dict) is skipped (REJECTION)
# ---------------------------------------------------------------------------


async def test_non_dict_usage_skips_capture() -> None:
    called: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        called.append(usage)

    ws = FakeWebSocket(incoming=[json.dumps({"type": "response.done", "usage": "not-a-dict"})])
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    frames = await _collect(session)

    assert frames == [{"type": "response.done"}]
    assert called == [], (
        "usage present but not a dict -> usage_malformed_skip: callback never invoked"
    )


async def test_usage_dict_with_missing_subfields_still_bills_degraded_to_zero() -> None:
    """A dict usage with missing/non-numeric sub-fields is NOT the malformed-skip case —
    it still captures, degrading unparseable fields to 0 via _safe_tier (companion scenario
    to §2's malformed-shape rejection, distinguishing 'not a dict' from 'partially parseable')."""
    called: list[dict] = []

    async def _on_usage(usage: dict) -> None:
        called.append(usage)

    ws = FakeWebSocket(
        incoming=[
            json.dumps(
                {
                    "type": "response.done",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": "not-a-number",  # malformed sub-field
                        # input_token_details / output_token_details entirely absent
                    },
                }
            )
        ]
    )
    session = _session(ws, on_usage=_on_usage)
    await session.connect()
    await _collect(session)

    assert len(called) == 1, "a dict usage with bad sub-fields still captures (degrades, not skips)"


# ---------------------------------------------------------------------------
# Scenario 15 — Missing pricing snapshot never bills a fabricated cost (REJECTION)
# ---------------------------------------------------------------------------


async def test_missing_pricing_snapshot_bills_zero_never_fabricated(
    tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(has_pricing=False)
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-realtime",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "input_token_details": {"audio_tokens": 80},
            "output_token_details": {"audio_tokens": 40},
        },
        status=200,
    )

    # record() never raises; it still XADDs (write-behind never drops the event per its own
    # Must), but MUST NOT report a non-zero-looking priced row — cost stays 0, never a fabricated
    # audio-tier charge with no backing pricing_snapshots row.
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == Decimal("0"), (
        "no pricing row -> cost must be 0, never a silently-fabricated audio charge"
    )


# ---------------------------------------------------------------------------
# Scenario 16 — usage_recorder.record() failure is swallowed (REJECTION)
# ---------------------------------------------------------------------------


async def test_usage_recorder_record_failure_is_swallowed(
    tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    class _BoomRedis:
        async def xadd(self, *_a: Any, **_k: Any) -> bytes:
            raise ConnectionError("redis unavailable")

    session = FakeSession(prompt_price=Decimal("0.000004"))
    recorder = RecordingUsageRecorder(
        redis=_BoomRedis(), session_factory=FakeSessionFactory(session)
    )

    # Must not raise — RecordingUsageRecorder.record()'s existing swallow+log guarantee.
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-realtime",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
        usage_source="realtime_relay",
    )
