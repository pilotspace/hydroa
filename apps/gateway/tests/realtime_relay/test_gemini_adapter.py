"""Red suite for the Gemini Live adapter (v52 gemini-live-adapter).

RED until proxy.infrastructure.gemini_live exists. Pure-unit: a FAKE
RealtimeWebSocket (scripted recv queue + recorded sends); NO network, NO key.

The adapter is the SOLE owner of Gemini Live (BidiGenerateContent) wire format;
one server message can fan out to MULTIPLE gateway frames (multi-part modelTurn).
"""

from __future__ import annotations

import base64
import json

import pytest

from gateway.proxy.domain.realtime_relay import (
    RealtimeProviderUnavailableError,
    RealtimeRelaySession,
)
from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

from .test_openai_adapter import FakeWebSocket  # reuse the scripted fake socket


def _session(ws: FakeWebSocket) -> GeminiLiveSession:
    async def _factory() -> FakeWebSocket:
        return ws

    return GeminiLiveSession(model="gemini-2.0-flash-exp", api_key="g-test", ws_connect=_factory)


async def _collect(session: GeminiLiveSession) -> list:
    out = []
    async for frame in session.events():
        out.append(frame)
    return out


async def test_server_message_fans_out_in_order() -> None:
    audio_b64 = base64.b64encode(b"\x01\x02\x03").decode()
    ws = FakeWebSocket(
        incoming=[
            json.dumps({"setupComplete": {}}),
            json.dumps(
                {
                    "serverContent": {
                        "modelTurn": {
                            "parts": [
                                {"inlineData": {"mimeType": "audio/pcm", "data": audio_b64}},
                                {"text": "hi"},
                            ]
                        },
                        "turnComplete": True,
                    }
                }
            ),
        ]
    )
    session = _session(ws)
    await session.connect()
    frames = await _collect(session)
    assert frames == [
        {"type": "session.created"},
        b"\x01\x02\x03",
        {"type": "transcript", "role": "assistant", "text": "hi"},
        {"type": "response.done"},
    ]


async def test_client_frames_translate_to_gemini() -> None:
    ws = FakeWebSocket()
    session = _session(ws)
    await session.connect()
    await session.send_client_event({"type": "session.update", "voice": "x"})
    await session.send_audio(b"\x09")
    await session.send_client_event({"type": "audio.commit"})

    sent = [json.loads(m) for m in ws.sent]
    assert sent[0] == {"setup": {"model": "gemini-2.0-flash-exp", "voice": "x"}}
    assert sent[1] == {
        "realtimeInput": {
            "mediaChunks": [{"mimeType": "audio/pcm", "data": base64.b64encode(b"\x09").decode()}]
        }
    }
    assert sent[2] == {"clientContent": {"turnComplete": True}}


async def test_dial_failure_provider_unavailable() -> None:
    async def _factory():
        raise ConnectionError("no route")

    session = GeminiLiveSession(model="m", api_key="g", ws_connect=_factory)
    with pytest.raises(RealtimeProviderUnavailableError):
        await session.connect()


async def test_malformed_message_raises() -> None:
    ws = FakeWebSocket(incoming=["not json"])
    session = _session(ws)
    await session.connect()
    gen = session.events()
    with pytest.raises(RealtimeProviderUnavailableError):
        await gen.__anext__()
    with pytest.raises((StopAsyncIteration, RealtimeProviderUnavailableError)):
        await gen.__anext__()


async def test_gemini_error_forwarded() -> None:
    ws = FakeWebSocket(
        incoming=[
            json.dumps({"error": {"message": "quota"}}),
            json.dumps({"serverContent": {"turnComplete": True}}),
        ]
    )
    session = _session(ws)
    await session.connect()
    frames = await _collect(session)
    assert frames[0] == {"type": "error", "code": "provider_error", "message": "quota"}
    assert frames[1] == {"type": "response.done"}
    assert ws.closed is False


async def test_satisfies_seam_protocol() -> None:
    ws = FakeWebSocket()
    session = _session(ws)
    assert isinstance(session, RealtimeRelaySession)
    await session.aclose()


async def test_default_dial_puts_key_in_header_not_url(monkeypatch) -> None:
    """SECURITY (F1): the api key must ride the x-goog-api-key header, never the URL query."""
    captured: dict = {}

    async def _fake_connect(url, headers, *, timeout):  # noqa: ASYNC109 — mirrors connect_websocket's signature
        captured["url"] = url
        captured["headers"] = headers
        return FakeWebSocket()

    monkeypatch.setattr("gateway.proxy.infrastructure.gemini_live.connect_websocket", _fake_connect)
    # no ws_connect injected → exercises the real default dialer
    session = GeminiLiveSession(model="m", api_key="SECRET-KEY")
    await session.connect()
    assert "SECRET-KEY" not in captured["url"]
    assert captured["headers"].get("x-goog-api-key") == "SECRET-KEY"
