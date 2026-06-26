"""Credential-gated LIVE round-trip for the realtime relay adapters (v52 t5).

This module SHIPS SKIPPED. It dials a REAL provider realtime WebSocket only when
`GATEWAY_REALTIME_RELAY_LIVE` is set — the documented HARD-STOP for real
verification (Tin's credential-gated decision). CI / no-key runs report SKIPPED,
never failed, never a fake pass. Mirrors v51's `test_artifacts_s3_live.py`.

Run it for real:
    GATEWAY_REALTIME_RELAY_LIVE=1 \
    GATEWAY_REALTIME_RELAY_LIVE_PROVIDER=openai \
    OPENAI_API_KEY=sk-... \
    uv run pytest tests/realtime_relay/test_relay_live.py -v

It proves end-to-end translation: connect → send a setup/session frame → receive
AT LEAST ONE NORMALIZED gateway frame (dict control or audio bytes) → aclose,
all bounded by an asyncio.timeout so a hung provider FAILS rather than hangs.
"""

from __future__ import annotations

import asyncio
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GATEWAY_REALTIME_RELAY_LIVE"),
    reason="live realtime relay not enabled (set GATEWAY_REALTIME_RELAY_LIVE)",
)

_ROUND_TRIP_TIMEOUT_SECONDS = 30.0


def _build_live_session():
    """Build the chosen real adapter, or pytest.skip if its key is absent."""
    provider = os.getenv("GATEWAY_REALTIME_RELAY_LIVE_PROVIDER", "openai").lower()
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            pytest.skip("OPENAI_API_KEY not set")
        from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

        model = os.getenv("GATEWAY_REALTIME_RELAY_OPENAI_MODEL", "gpt-4o-realtime-preview")
        return OpenAIRealtimeSession(model=model, api_key=key), {"type": "session.update"}
    if provider == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            pytest.skip("GEMINI_API_KEY not set")
        from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

        model = os.getenv("GATEWAY_REALTIME_RELAY_GEMINI_MODEL", "gemini-2.0-flash-exp")
        # Gemini emits setupComplete only AFTER a setup frame.
        return GeminiLiveSession(model=model, api_key=key), {"type": "session.update"}
    pytest.skip(f"unknown GATEWAY_REALTIME_RELAY_LIVE_PROVIDER={provider!r}")


async def test_live_round_trip() -> None:
    """A real provider session translates the first event into a normalized gateway frame."""
    session, setup_frame = _build_live_session()
    first = None
    try:
        async with asyncio.timeout(_ROUND_TRIP_TIMEOUT_SECONDS):
            await session.connect()
            await session.send_client_event(setup_frame)
            async for frame in session.events():
                first = frame
                break  # any first event proves the round-trip end-to-end
    finally:
        await session.aclose()

    assert first is not None, "no provider event received within the round-trip timeout"
    # The adapter must have translated provider wire → a NORMALIZED gateway frame.
    assert isinstance(first, dict | bytes | bytearray)
    if isinstance(first, dict):
        assert "type" in first
