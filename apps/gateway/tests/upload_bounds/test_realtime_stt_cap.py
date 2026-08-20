"""Red->green suite for upload-bounds-realtime-stt (TASK.md §CHECKS — SECURITY).

Calls realtime_ws._real_stt DIRECTLY with a fake websocket carrying `.app` — no WS
handshake; the seam under test is the TranscriptionUseCase construction inside it.

RED at the current tree: realtime_ws.py builds TranscriptionUseCase WITHOUT
max_file_bytes, so an over-cap buffer sails through to the (faked) upstream and returns
a transcript instead of raising. DO NOT weaken these tests to pass — that is Build's job.
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.core.error_catalog import ProblemError
from gateway.proxy.api.realtime_ws import _real_stt

from .conftest import (
    SMALL_AUDIO_CAP,
    STT_MODEL_ID,
    STT_RESPONSE_BODY,
    SpyRecorder,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)

pytestmark = pytest.mark.asyncio


class _FakeWebSocket:
    """The only attribute _real_stt reads is `.app`."""

    def __init__(self, app: Any) -> None:
        self.app = app


async def _prepped(bounds_app, db_session, api_key):
    application, _ = bounds_app
    await seed_stt_model(db_session)
    provider = inject_fake_openai_audio_provider(application)
    provider.set_multipart_response(200, STT_RESPONSE_BODY)
    spy = SpyRecorder()
    application.state.usage_recorder = spy
    return application, provider, spy


async def test_realtime_stt_over_cap_refused(bounds_app, db_session, api_key) -> None:
    """covers: M1, A2, A5, E1, R:SECOND_KNOB — a buffered utterance ONE byte over
    settings.max_audio_upload_bytes (the ONLY knob the fixture sets — no realtime-specific
    twin) raises the per-file ProblemError out of _real_stt with zero upstream calls and
    zero usage records. RED today: no max_file_bytes at the construction site, so the
    faked upstream answers and a transcript comes back instead."""
    application, provider, spy = await _prepped(bounds_app, db_session, api_key)

    with pytest.raises(ProblemError) as excinfo:
        await _real_stt(
            b"\0" * (SMALL_AUDIO_CAP + 1),
            STT_MODEL_ID,
            api_key,
            websocket=_FakeWebSocket(application),  # type: ignore[arg-type]
        )
    assert excinfo.value.code == "ERR_PAYLOAD_AUDIO_TOO_LARGE"
    assert provider.post_multipart_calls == [], "over-cap utterance must never reach upstream"
    assert spy.call_count == 0, "over-cap utterance must never produce a usage record"


async def test_realtime_stt_at_cap_passes(bounds_app, db_session, api_key) -> None:
    """covers: M2, E2, R:WEAKENED_UTTERANCE_GATE — exactly-at-cap still transcribes (one
    provider call, faked transcript back): the wiring must not break the normal WS leg or
    introduce an off-by-one against the parent task's strict->."""
    application, provider, _ = await _prepped(bounds_app, db_session, api_key)

    text = await _real_stt(
        b"\0" * SMALL_AUDIO_CAP,
        STT_MODEL_ID,
        api_key,
        websocket=_FakeWebSocket(application),  # type: ignore[arg-type]
    )
    assert text == STT_RESPONSE_BODY["text"]
    assert len(provider.post_multipart_calls) == 1
