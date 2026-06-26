"""A minimal provider-WebSocket seam shared by the realtime adapters (v52).

Both OpenAI Realtime and Gemini Live speak JSON **text** frames (audio rides as
base64 inside the JSON), so this seam is text-only: `send(str)` / `recv() -> str`
/ `aclose()`. Adapters depend on the `RealtimeWebSocket` Protocol so unit tests
inject a fake socket; `connect_websocket` is the real connector (wraps
`websockets.connect`, already available transitively — no new dependency) used
only by production + the credential-gated live-verify harness.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class RealtimeWebSocket(Protocol):
    """The minimal text-WebSocket surface an adapter needs."""

    async def send(self, message: str) -> None:
        """Send one text frame."""
        ...

    async def recv(self) -> str:
        """Receive one text frame. Raise (e.g. ConnectionClosed) when the socket closes."""
        ...

    async def aclose(self) -> None:
        """Close the socket. Idempotent."""
        ...


class _WebsocketsAdapter:
    """Wraps a `websockets` client connection in the RealtimeWebSocket surface."""

    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def send(self, message: str) -> None:
        await self._conn.send(message)  # type: ignore[attr-defined]

    async def recv(self) -> str:
        data = await self._conn.recv()  # type: ignore[attr-defined]
        return data if isinstance(data, str) else data.decode("utf-8")

    async def aclose(self) -> None:
        await self._conn.close()  # type: ignore[attr-defined]


async def connect_websocket(
    url: str,
    headers: dict[str, str],
    *,
    timeout: float,  # noqa: ASYNC109 — leaf connector; the deadline is applied via asyncio.timeout below
) -> RealtimeWebSocket:
    """Dial a provider realtime WebSocket (real connector — production / live-verify only).

    Imports `websockets` lazily so the unit suites (fake socket) never load it.
    """
    import asyncio

    import websockets

    async with asyncio.timeout(timeout):
        conn = await websockets.connect(url, additional_headers=headers)
    return _WebsocketsAdapter(conn)
