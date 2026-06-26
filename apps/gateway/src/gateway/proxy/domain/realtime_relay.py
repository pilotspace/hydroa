"""The realtime relay seam (v52 realtime-relay-port).

A provider-agnostic full-duplex voice relay. The pump (application layer) speaks
ONLY these Protocols; concrete provider adapters (OpenAI Realtime, Gemini Live)
implement ``RealtimeRelaySession``, and the WS endpoint adapts a Starlette socket
to ``RealtimeClientTransport``.

Frame convention (the normalized gateway vocabulary — FROZEN @ v1):
  - a ``dict`` is a CONTROL frame:  {"type": <name>, ...}
  - ``bytes`` is AUDIO.
This holds symmetrically in BOTH directions:
  client→gateway control : session.update · audio.commit · response.create ·
                           interrupt   (+ audio bytes)
  gateway→client control : session.created · transcript · audio.delta(as bytes) ·
                           response.done · error

Adapters translate provider-native events to/from these frames; they NEVER leak
provider wire format to the client.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

# A relayed frame: a control dict is control, bytes is audio.
ControlFrame = dict[str, Any]
RelayFrame = ControlFrame | bytes


class RealtimeRelayError(Exception):
    """Base for relay-seam errors."""


class RealtimeProviderUnavailableError(RealtimeRelayError):
    """The provider could not be reached (connect/send timed out, or breaker open)."""


class RealtimeRelayClosed(RealtimeRelayError):
    """The client transport has closed (a normal end-of-session signal, not a fault)."""


@runtime_checkable
class RealtimeRelaySession(Protocol):
    """A provider realtime session — implemented by each provider adapter."""

    async def connect(self) -> None:
        """Open the provider session. Raise RealtimeProviderUnavailableError on failure."""
        ...

    async def send_client_event(self, frame: ControlFrame) -> None:
        """Translate + forward a gateway control frame to the provider."""
        ...

    async def send_audio(self, data: bytes) -> None:
        """Forward client audio bytes to the provider."""
        ...

    def events(self) -> AsyncIterator[RelayFrame]:
        """Yield provider events normalized to gateway frames (dict control / bytes audio)."""
        ...

    async def aclose(self) -> None:
        """Close the provider session. Idempotent."""
        ...


@runtime_checkable
class RealtimeClientTransport(Protocol):
    """The client side of the relay — adapted over a Starlette WebSocket by the endpoint."""

    async def receive(self) -> RelayFrame:
        """Receive one client frame (dict control / bytes audio).

        Raise RealtimeRelayClosed on disconnect.
        """
        ...

    async def send_event(self, frame: ControlFrame) -> None:
        """Send a gateway control frame to the client (JSON)."""
        ...

    async def send_audio(self, data: bytes) -> None:
        """Send audio bytes to the client (binary)."""
        ...

    async def close(self, code: int) -> None:
        """Close the client transport with a WS close code. Idempotent."""
        ...
