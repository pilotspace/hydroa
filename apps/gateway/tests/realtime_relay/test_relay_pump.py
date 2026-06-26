"""Red suite for the realtime relay seam + pump (v52 realtime-relay-port).

RED until gateway.proxy.domain.realtime_relay + gateway.proxy.application.
realtime_relay_pump exist. Pure-unit: a FAKE provider session + FAKE client
transport, no WebSocket and no network.

FROZEN contract @ v1 (Tin 2026-06-26 — NO send queue; direct awaited relay):
  Frame convention: dict = a control frame · bytes = audio (symmetric both ways).
  RelayPump.run(): connect (timeout + breaker) → 2 cancel-safe relay tasks →
  deterministic teardown in finally (both sides closed exactly once).
  Close codes: 4503 provider-unavailable (connect/send timeout or breaker open) ·
  1011 provider-error mid-session · 4408 idle · 1000 normal.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from pydantic import ValidationError

from gateway.core.config import Settings
from gateway.proxy.application.realtime_relay_pump import RelayPump
from gateway.proxy.domain.realtime_relay import (
    RealtimeProviderUnavailableError,
    RealtimeRelayClosed,
    RealtimeRelaySession,
)
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# asyncio_mode="auto" (pyproject) marks the async tests; no explicit pytestmark
# (it would wrongly mark the sync tests too).

_HANG = 100.0  # seconds — far longer than any test timeout


def _settings(**over) -> Settings:
    base = dict(
        realtime_relay_connect_timeout_seconds=0.1,
        realtime_relay_idle_timeout_seconds=0.2,
    )
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeProviderSession:
    """A fake RealtimeRelaySession. dict=control, bytes=audio."""

    def __init__(
        self,
        *,
        events: list[dict | bytes] | None = None,
        connect_hang: bool = False,
        connect_error: Exception | None = None,
        send_hang: bool = False,
        events_raise_after: int | None = None,
        block_events: bool = False,
    ) -> None:
        self._events = list(events or [])
        self._connect_hang = connect_hang
        self._connect_error = connect_error
        self._send_hang = send_hang
        self._events_raise_after = events_raise_after
        self._block_events = block_events
        self.sent_events: list[dict] = []
        self.sent_audio: list[bytes] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        if self._connect_error is not None:
            raise self._connect_error
        if self._connect_hang:
            await asyncio.sleep(_HANG)
        self.connected = True

    async def send_client_event(self, frame: dict) -> None:
        if self._send_hang:
            await asyncio.sleep(_HANG)
        self.sent_events.append(frame)

    async def send_audio(self, data: bytes) -> None:
        if self._send_hang:
            await asyncio.sleep(_HANG)
        self.sent_audio.append(data)

    async def events(self) -> AsyncIterator[dict | bytes]:
        for i, ev in enumerate(self._events):
            if self._events_raise_after is not None and i == self._events_raise_after:
                raise RuntimeError("provider boom")
            yield ev
        if self._block_events:
            await asyncio.sleep(_HANG)  # provider stays open (no more events)

    async def aclose(self) -> None:
        self.closed = True


class FakeClientTransport:
    """A fake RelayClientTransport. dict=control, bytes=audio."""

    def __init__(
        self,
        *,
        frames: list[dict | bytes] | None = None,
        disconnect_after: bool = False,
        idle: bool = False,
    ) -> None:
        self._frames = list(frames or [])
        self._disconnect_after = disconnect_after
        self._idle = idle
        self.sent_events: list[dict] = []
        self.sent_audio: list[bytes] = []
        self.close_code: int | None = None

    async def receive(self) -> dict | bytes:
        if self._frames:
            return self._frames.pop(0)
        if self._disconnect_after:
            raise RealtimeRelayClosed("client gone")
        await asyncio.sleep(_HANG)  # idle — no more client frames

    async def send_event(self, frame: dict) -> None:
        self.sent_events.append(frame)

    async def send_audio(self, data: bytes) -> None:
        self.sent_audio.append(data)

    async def close(self, code: int) -> None:
        if self.close_code is None:
            self.close_code = code


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_happy_path_full_duplex() -> None:
    """Provider events reach the client in order; teardown closes both once."""
    session = FakeProviderSession(
        events=[
            {"type": "session.created"},
            {"type": "transcript", "role": "assistant", "text": "hi"},
            b"\x01audio",
            {"type": "response.done"},
        ]
    )
    transport = FakeClientTransport(idle=True)  # client just listens
    pump = RelayPump(transport, session, _settings())
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert transport.sent_events == [
        {"type": "session.created"},
        {"type": "transcript", "role": "assistant", "text": "hi"},
        {"type": "response.done"},
    ]
    assert transport.sent_audio == [b"\x01audio"]
    assert session.closed is True
    assert transport.close_code == 1000  # provider stream ended normally


async def test_client_to_provider_then_disconnect() -> None:
    """Client control + audio frames reach the provider; client disconnect tears down cleanly."""
    session = FakeProviderSession(block_events=True)  # provider stays silent/open
    transport = FakeClientTransport(
        frames=[{"type": "session.update", "voice": "alloy"}, b"\x02mic", {"type": "audio.commit"}],
        disconnect_after=True,
    )
    pump = RelayPump(transport, session, _settings())
    await asyncio.wait_for(pump.run(), timeout=2.0)

    assert session.sent_events == [
        {"type": "session.update", "voice": "alloy"},
        {"type": "audio.commit"},
    ]
    assert session.sent_audio == [b"\x02mic"]
    assert session.closed is True  # provider aclose()d on client disconnect


async def test_provider_connect_timeout_4503() -> None:
    session = FakeProviderSession(connect_hang=True)
    transport = FakeClientTransport(idle=True)
    breaker = CircuitBreaker()
    pump = RelayPump(transport, session, _settings(), breaker=breaker)
    await asyncio.wait_for(pump.run(), timeout=2.0)
    assert transport.close_code == 4503
    assert session.connected is False


async def test_breaker_open_closes_4503_without_connect() -> None:
    session = FakeProviderSession(events=[{"type": "response.done"}])
    transport = FakeClientTransport(idle=True)
    breaker = CircuitBreaker()
    for _ in range(20):
        breaker.record_failure()
    assert breaker.is_open() is True
    pump = RelayPump(transport, session, _settings(), breaker=breaker)
    await asyncio.wait_for(pump.run(), timeout=2.0)
    assert transport.close_code == 4503
    assert session.connected is False  # never attempted


async def test_provider_send_timeout_4503() -> None:
    session = FakeProviderSession(send_hang=True, block_events=True)
    transport = FakeClientTransport(frames=[b"\x03mic"], idle=True)
    breaker = CircuitBreaker()
    pump = RelayPump(transport, session, _settings(), breaker=breaker)
    await asyncio.wait_for(pump.run(), timeout=2.0)
    assert transport.close_code == 4503
    assert session.closed is True


async def test_provider_events_raise_1011() -> None:
    session = FakeProviderSession(
        events=[
            {"type": "transcript", "role": "assistant", "text": "x"},
            {"type": "response.done"},  # never reached — raise fires before yielding it
        ],
        events_raise_after=1,
    )
    transport = FakeClientTransport(idle=True)
    pump = RelayPump(transport, session, _settings())
    await asyncio.wait_for(pump.run(), timeout=2.0)
    # the one event before the raise was delivered, then an error frame + 1011
    assert {"type": "transcript", "role": "assistant", "text": "x"} in transport.sent_events
    assert any(
        f.get("type") == "error" and f.get("code") == "provider_error"
        for f in transport.sent_events
    )
    assert transport.close_code == 1011
    assert session.closed is True


async def test_idle_timeout_4408() -> None:
    session = FakeProviderSession(block_events=True)
    transport = FakeClientTransport(idle=True)  # never sends a frame
    pump = RelayPump(transport, session, _settings(realtime_relay_idle_timeout_seconds=0.1))
    await asyncio.wait_for(pump.run(), timeout=2.0)
    assert transport.close_code == 4408
    assert session.closed is True


async def test_no_leaked_tasks() -> None:
    before = len(asyncio.all_tasks())
    session = FakeProviderSession(events=[{"type": "response.done"}])
    transport = FakeClientTransport(idle=True)
    pump = RelayPump(transport, session, _settings())
    await asyncio.wait_for(pump.run(), timeout=2.0)
    await asyncio.sleep(0)  # let any cancellations settle
    after = len(asyncio.all_tasks())
    assert after <= before  # no relay task left pending


def test_session_protocol_runtime_checkable() -> None:
    assert isinstance(FakeProviderSession(), RealtimeRelaySession)


def test_config_knobs_validated() -> None:
    s = _settings()
    assert s.realtime_relay_connect_timeout_seconds > 0
    assert s.realtime_relay_idle_timeout_seconds > 0
    with pytest.raises(ValidationError):
        _settings(realtime_relay_connect_timeout_seconds=0)


def test_provider_unavailable_is_relay_error() -> None:
    assert issubclass(RealtimeProviderUnavailableError, Exception)
    assert issubclass(RealtimeRelayClosed, Exception)
