"""RED suite for stream-disconnect-billing (TASK.md §4, scenarios DC1–DC7).

When a streaming chat client disconnects mid-drain (GeneratorExit / CancelledError
raised into CompletionUseCase.stream._wrapped before normal completion), the gateway
must still fire EXACTLY ONE usage record — flagged usage_source="client_disconnect"
(or "frame" if a complete usage frame already arrived) — instead of today's ZERO rows.

RED at write time: _wrapped has no GeneratorExit/CancelledError billing handler, so a
disconnect writes no record (DC1/DC3/DC4/DC7 fail). DC2/DC5/DC6 are GREEN-BY-DESIGN
regression/preservation guards (they pass today and must keep passing).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    A0,
    A1,
    CAND_A,
    COMPLETE_USAGE,
    DONE,
    MarkerSpyRecorder,
    PlanStreamUpstream,
    open_stream,
)

# pytest is configured asyncio_mode=auto (pyproject.toml): `async def test_*` runs
# on the loop without a marker. No file-level pytest.mark.asyncio.


async def _settle() -> None:
    # Let the fire-and-forget usage record (asyncio.ensure_future) run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# DC1 — disconnect mid-stream with no usage frame → flagged, never silent
# ---------------------------------------------------------------------------


async def test_dc1_disconnect_no_frame_flagged(caplog: pytest.LogCaptureFixture) -> None:
    up = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    with caplog.at_level("WARNING"):
        first = await gen.__anext__()  # peeked first_chunk A0; collected == [A0]
        assert first == A0
        await gen.aclose()  # client disconnect → GeneratorExit at the suspended yield
        await _settle()

    assert rec.call_count == 1, f"disconnect must single-bill, got {rec.call_count}"
    call = rec.last_call
    assert call["status"] == 200
    assert call.get("usage") is None  # no usage frame in the partial stream → $0
    assert call.get("usage_source") == "client_disconnect", (
        f"usage_source: {call.get('usage_source')!r}"
    )
    assert "stream_client_disconnect" in caplog.text, (
        f"expected stream_client_disconnect warning, caplog: {caplog.text!r}"
    )


# ---------------------------------------------------------------------------
# DC2 — GeneratorExit is re-raised so the generator actually closes (guard)
# ---------------------------------------------------------------------------


async def test_dc2_generatorexit_reraised() -> None:
    # GREEN-BY-DESIGN: the disconnect handler must RE-RAISE GeneratorExit. If it
    # swallowed it (returned instead), aclose() raises RuntimeError("async generator
    # ignored GeneratorExit"). This guards that regression.
    up = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    await gen.__anext__()
    await gen.aclose()  # must return cleanly (no RuntimeError) — GeneratorExit not swallowed
    # A second aclose() on a closed generator is a no-op.
    await gen.aclose()
    await _settle()  # drain the fire-and-forget disconnect record (no quiet teardown task)


# ---------------------------------------------------------------------------
# DC3 — disconnect AFTER a complete frame bills the frame (usage_source=frame)
# ---------------------------------------------------------------------------


async def test_dc3_disconnect_after_complete_frame_bills_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    up = PlanStreamUpstream({CAND_A: [A0, COMPLETE_USAGE, A1, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    with caplog.at_level("WARNING"):
        await gen.__anext__()  # A0 (first_chunk); collected == [A0]
        await gen.__anext__()  # COMPLETE_USAGE; collected == [A0, COMPLETE_USAGE]
        await gen.aclose()  # disconnect AFTER the frame arrived
        await _settle()

    assert rec.call_count == 1
    call = rec.last_call
    assert call.get("usage_source") == "frame", (
        f"a complete frame before disconnect bills as frame, got {call.get('usage_source')!r}"
    )
    assert call["usage"]["total_tokens"] == 15  # the frame's real tokens (cost > 0)
    assert "stream_client_disconnect" not in caplog.text


# ---------------------------------------------------------------------------
# DC4 — single-bill on disconnect (exactly one record, not zero, not two)
# ---------------------------------------------------------------------------


async def test_dc4_single_bill_on_disconnect() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    await gen.__anext__()
    await gen.aclose()
    await _settle()

    assert rec.call_count == 1, (
        f"disconnect must fire exactly one record (was 0 today), got {rec.call_count}"
    )


# ---------------------------------------------------------------------------
# DC5 — normal completion is byte-identical to v27 (regression guard)
# ---------------------------------------------------------------------------


async def test_dc5_normal_completion_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    # GREEN-BY-DESIGN: a fully-drained stream keeps the v27 behavior.
    up = PlanStreamUpstream({CAND_A: [A0, COMPLETE_USAGE, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    with caplog.at_level("WARNING"):
        chunks = [chunk async for chunk in gen]  # full drain
        await _settle()

    assert chunks == [A0, COMPLETE_USAGE, DONE]
    assert rec.call_count == 1
    assert rec.last_call.get("usage_source") == "frame"
    assert "stream_client_disconnect" not in caplog.text


# ---------------------------------------------------------------------------
# DC6 — upstream-error 502 path is unchanged (not a disconnect)
# ---------------------------------------------------------------------------


async def test_dc6_upstream_error_502_not_disconnect() -> None:
    # GREEN-BY-DESIGN: a mid-stream upstream error still records status 502 (v27),
    # never a client_disconnect marker.
    up = PlanStreamUpstream({CAND_A: [A0, UpstreamUnavailableError("boom"), DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    chunks = [chunk async for chunk in gen]  # error is caught inside _wrapped → clean end
    await _settle()

    # v35 stream-upstream-error-frame: a mid-stream upstream failure now surfaces a
    # terminal error frame + [DONE] (was a silent truncation that ended at [A0]). The
    # 502-record + not-a-disconnect invariants are unchanged.
    assert chunks[0] == A0
    body = b"".join(chunks)
    assert b"ERR_UPSTREAM_UNAVAILABLE" in body
    assert b"[DONE]" in body
    assert rec.call_count == 1
    assert rec.last_call["status"] == 502
    assert rec.last_call.get("usage_source") != "client_disconnect"


# ---------------------------------------------------------------------------
# DC7 — cancellation mid-drain is treated like a disconnect
# ---------------------------------------------------------------------------


async def test_dc7_cancellation_treated_as_disconnect() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    rec = MarkerSpyRecorder()

    gen = await open_stream(up, rec)
    await gen.__anext__()  # A0; collected == [A0]
    # athrow(CancelledError) injects exactly what task-cancellation raises into the
    # suspended yield — deterministic, no real task race.
    with pytest.raises(asyncio.CancelledError):
        await gen.athrow(asyncio.CancelledError)
    await _settle()

    assert rec.call_count == 1, f"cancellation must single-bill, got {rec.call_count}"
    call: dict[str, Any] = rec.last_call
    assert call["status"] == 200
    assert call.get("usage_source") == "client_disconnect", (
        f"usage_source: {call.get('usage_source')!r}"
    )
