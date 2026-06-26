"""OpenAI Realtime adapter implementing the frozen relay seam (v52).

`OpenAIRealtimeSession` is the SOLE owner of OpenAI Realtime wire format: it
translates the normalized gateway frames (dict=control, bytes=audio) to/from
OpenAI Realtime JSON events. The pump and the endpoint never see OpenAI JSON.

The provider socket is reached through an injected `ws_connect` factory so unit
tests drive a fake socket; production passes a `connect_websocket` partial.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from urllib.parse import quote

from gateway.proxy.domain.realtime_relay import (
    ControlFrame,
    RealtimeProviderUnavailableError,
    RelayFrame,
)
from gateway.proxy.infrastructure.realtime_ws_client import (
    RealtimeWebSocket,
    connect_websocket,
)

_DEFAULT_URL = "wss://api.openai.com/v1/realtime"
# OpenAI server event type -> a small translator producing a gateway frame.
_TRANSCRIPT_EVENTS = frozenset({"response.audio_transcript.delta", "response.text.delta"})


class OpenAIRealtimeSession:
    """A provider realtime session over the OpenAI Realtime WebSocket."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        ws_connect: Callable[[], Awaitable[RealtimeWebSocket]] | None = None,
        url: str = _DEFAULT_URL,
        connect_timeout: float = 10.0,
    ) -> None:
        self._model = model
        self._api_key = api_key
        # default: dial OpenAI Realtime for real; tests inject a fake factory.
        self._ws_connect = ws_connect or self._dial_openai
        self._url = url
        self._connect_timeout = connect_timeout
        self._ws: RealtimeWebSocket | None = None

    # -- connection ---------------------------------------------------------

    async def _dial_openai(self) -> RealtimeWebSocket:
        full_url = f"{self._url}?model={quote(self._model)}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        return await connect_websocket(full_url, headers, timeout=self._connect_timeout)

    async def connect(self) -> None:
        try:
            self._ws = await self._ws_connect()
        except Exception as exc:  # dial failure / timeout → provider unavailable
            self._ws = None
            # Do NOT stringify exc into the message — defense-in-depth against URL leaks.
            raise RealtimeProviderUnavailableError("OpenAI realtime dial failed") from exc

    async def aclose(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.aclose()

    # -- client → OpenAI ----------------------------------------------------

    async def send_client_event(self, frame: ControlFrame) -> None:
        await self._send(self._translate_client_event(frame))

    async def send_audio(self, data: bytes) -> None:
        await self._send(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(data).decode("ascii"),
            }
        )

    @staticmethod
    def _translate_client_event(frame: ControlFrame) -> dict[str, Any]:
        kind = frame.get("type")
        if kind == "session.update":
            return {
                "type": "session.update",
                "session": {k: v for k, v in frame.items() if k != "type"},
            }
        if kind == "audio.commit":
            return {"type": "input_audio_buffer.commit"}
        if kind == "response.create":
            return {"type": "response.create"}
        if kind == "interrupt":
            return {"type": "response.cancel"}
        # forward-compatible: unknown control passes through under its own type
        return dict(frame)

    async def _send(self, event: dict[str, Any]) -> None:
        if self._ws is None:
            raise RealtimeProviderUnavailableError("OpenAI realtime not connected")
        await self._ws.send(json.dumps(event))

    # -- OpenAI → client ----------------------------------------------------

    async def events(self) -> AsyncIterator[RelayFrame]:
        if self._ws is None:
            raise RealtimeProviderUnavailableError("OpenAI realtime not connected")
        while True:
            try:
                raw = await self._ws.recv()
            except Exception:
                return
            try:
                event = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise RealtimeProviderUnavailableError(
                    "OpenAI realtime sent a non-JSON message"
                ) from exc
            frame = self._translate_server_event(event)
            if frame is not None:
                yield frame

    @staticmethod
    def _translate_server_event(event: dict[str, Any]) -> RelayFrame | None:
        kind = event.get("type")
        if kind == "session.created":
            return {"type": "session.created"}
        if kind == "response.audio.delta":
            return base64.b64decode(event.get("delta", ""))
        if kind in _TRANSCRIPT_EVENTS:
            return {
                "type": "transcript",
                "role": "assistant",
                "text": event.get("delta", ""),
            }
        if kind == "response.done":
            return {"type": "response.done"}
        if kind == "error":
            message = (
                event.get("error", {}).get("message", "")
                if isinstance(event.get("error"), dict)
                else event.get("message", "")
            )
            return {"type": "error", "code": "provider_error", "message": message}
        # unmapped provider event → swallowed (not surfaced to the client)
        return None
