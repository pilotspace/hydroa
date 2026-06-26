"""Red suite for the OpenAI Realtime adapter (v52 openai-realtime-adapter).

RED until proxy.infrastructure.realtime_ws_client + proxy.infrastructure.
openai_realtime exist. Pure-unit: a FAKE RealtimeWebSocket (scripted recv queue
+ recorded sends); NO network, NO key.

The adapter is the SOLE owner of OpenAI wire format — it translates the FROZEN
gateway frames (dict=control, bytes=audio) to/from OpenAI Realtime JSON events.
"""

from __future__ import annotations

import base64
import json

import pytest

from gateway.proxy.domain.realtime_relay import (
    RealtimeProviderUnavailableError,
    RealtimeRelaySession,
)
from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession


class FakeWebSocket:
    """A scripted RealtimeWebSocket: recv() drains `incoming`, send() records."""

    def __init__(self, incoming: list[str] | None = None) -> None:
        self._incoming = list(incoming or [])
        self.sent: list[str] = []
        self.closed = False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self._incoming:
            raise ConnectionError("socket closed")  # provider stream ended
        return self._incoming.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _session(ws: FakeWebSocket) -> OpenAIRealtimeSession:
    async def _factory() -> FakeWebSocket:
        return ws

    return OpenAIRealtimeSession(
        model="gpt-4o-realtime-preview", api_key="sk-test", ws_connect=_factory
    )


async def _collect(session: OpenAIRealtimeSession) -> list:
    out = []
    async for frame in session.events():
        out.append(frame)
    return out


async def test_provider_events_translate_in_order() -> None:
    audio_b64 = base64.b64encode(b"\x01\x02\x03").decode()
    ws = FakeWebSocket(
        incoming=[
            json.dumps({"type": "session.created"}),
            json.dumps({"type": "response.audio.delta", "delta": audio_b64}),
            json.dumps({"type": "response.audio_transcript.delta", "delta": "hi"}),
            json.dumps({"type": "response.done"}),
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


async def test_client_frames_translate_to_openai() -> None:
    ws = FakeWebSocket()
    session = _session(ws)
    await session.connect()
    await session.send_client_event({"type": "session.update", "voice": "alloy"})
    await session.send_audio(b"\x09")
    await session.send_client_event({"type": "audio.commit"})
    await session.send_client_event({"type": "interrupt"})

    sent = [json.loads(m) for m in ws.sent]
    assert sent[0] == {"type": "session.update", "session": {"voice": "alloy"}}
    assert sent[1] == {
        "type": "input_audio_buffer.append",
        "audio": base64.b64encode(b"\x09").decode(),
    }
    assert sent[2] == {"type": "input_audio_buffer.commit"}
    assert sent[3] == {"type": "response.cancel"}


async def test_dial_failure_provider_unavailable() -> None:
    async def _factory():
        raise ConnectionError("no route to host")

    session = OpenAIRealtimeSession(model="m", api_key="sk", ws_connect=_factory)
    with pytest.raises(RealtimeProviderUnavailableError):
        await session.connect()


async def test_malformed_message_raises() -> None:
    ws = FakeWebSocket(incoming=["not json"])
    session = _session(ws)
    await session.connect()
    gen = session.events()
    with pytest.raises(RealtimeProviderUnavailableError):
        await gen.__anext__()
    # iterator stopped — a second pull also stops (no further yields)
    with pytest.raises((StopAsyncIteration, RealtimeProviderUnavailableError)):
        await gen.__anext__()


async def test_openai_error_event_forwarded() -> None:
    ws = FakeWebSocket(
        incoming=[
            json.dumps({"type": "error", "error": {"message": "rate limited"}}),
            json.dumps({"type": "response.done"}),
        ]
    )
    session = _session(ws)
    await session.connect()
    frames = await _collect(session)
    assert frames[0] == {"type": "error", "code": "provider_error", "message": "rate limited"}
    assert frames[1] == {"type": "response.done"}
    assert ws.closed is False  # adapter did NOT tear down on a forwarded error


async def test_satisfies_seam_protocol() -> None:
    ws = FakeWebSocket()
    session = _session(ws)
    assert isinstance(session, RealtimeRelaySession)
    await session.aclose()  # safe before connect()
