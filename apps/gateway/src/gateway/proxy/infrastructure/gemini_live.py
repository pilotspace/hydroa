"""Gemini Live adapter implementing the frozen relay seam (v52).

`GeminiLiveSession` is the SOLE owner of Gemini Live (BidiGenerateContent) wire
format: it translates the normalized gateway frames (dict=control, bytes=audio)
to/from Gemini Live messages. Mirrors the t2 OpenAI adapter's shape (injected
`ws_connect` + a real default dialer) but a single Gemini server message can
fan out to MULTIPLE gateway frames (multi-part modelTurn).
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from gateway.proxy.domain.realtime_relay import (
    ControlFrame,
    RealtimeProviderUnavailableError,
    RelayFrame,
)
from gateway.proxy.infrastructure.realtime_ws_client import (
    RealtimeWebSocket,
    connect_websocket,
)

_DEFAULT_URL = (
    "wss://generativelanguage.googleapis.com/ws/"
    "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent"
)
_AUDIO_MIME = "audio/pcm"
_log = logging.getLogger(__name__)


class GeminiLiveSession:
    """A provider realtime session over the Gemini Live (BidiGenerateContent) WebSocket."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        ws_connect: Callable[[], Awaitable[RealtimeWebSocket]] | None = None,
        url: str = _DEFAULT_URL,
        connect_timeout: float = 10.0,
        on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._ws_connect = ws_connect or self._dial_gemini
        self._url = url
        self._connect_timeout = connect_timeout
        self._ws: RealtimeWebSocket | None = None
        # realtime-relay-governance (B2 TASK.md §3, M3): optional per-turn usage capture
        # callback, mirroring OpenAIRealtimeSession's ALREADY-SHIPPED shape verbatim.
        # Never widens the RealtimeRelaySession Protocol.
        self._on_usage = on_usage

    # -- connection ---------------------------------------------------------

    async def _dial_gemini(self) -> RealtimeWebSocket:
        # SECURITY: the key goes in the `x-goog-api-key` HEADER, never the URL query
        # string — a URL-embedded key leaks via WS debug logs, exception strings, and
        # network intermediaries (the OpenAI adapter uses Authorization: Bearer for the
        # same reason). Google's Gemini API accepts x-goog-api-key as a header.
        headers = {"x-goog-api-key": self._api_key}
        return await connect_websocket(self._url, headers, timeout=self._connect_timeout)

    async def connect(self) -> None:
        try:
            self._ws = await self._ws_connect()
        except Exception as exc:  # dial failure / timeout → provider unavailable
            self._ws = None
            # Do NOT stringify exc into the message — it can carry the dial URL.
            raise RealtimeProviderUnavailableError("Gemini Live dial failed") from exc

    async def aclose(self) -> None:
        ws, self._ws = self._ws, None
        if ws is not None:
            await ws.aclose()

    # -- client → Gemini ----------------------------------------------------

    async def send_client_event(self, frame: ControlFrame) -> None:
        await self._send(self._translate_client_event(frame))

    async def send_audio(self, data: bytes) -> None:
        await self._send(
            {
                "realtimeInput": {
                    "mediaChunks": [
                        {"mimeType": _AUDIO_MIME, "data": base64.b64encode(data).decode("ascii")}
                    ]
                }
            }
        )

    def _translate_client_event(self, frame: ControlFrame) -> dict[str, Any]:
        kind = frame.get("type")
        if kind == "session.update":
            setup = {"model": self._model}
            setup.update({k: v for k, v in frame.items() if k != "type"})
            return {"setup": setup}
        if kind in {"audio.commit", "response.create", "interrupt"}:
            return {"clientContent": {"turnComplete": True}}
        # forward-compatible: unknown control rides under clientContent
        return {"clientContent": {k: v for k, v in frame.items() if k != "type"}}

    async def _send(self, message: dict[str, Any]) -> None:
        if self._ws is None:
            raise RealtimeProviderUnavailableError("Gemini Live not connected")
        await self._ws.send(json.dumps(message))

    # -- Gemini → client ----------------------------------------------------

    async def events(self) -> AsyncIterator[RelayFrame]:
        if self._ws is None:
            raise RealtimeProviderUnavailableError("Gemini Live not connected")
        while True:
            try:
                raw = await self._ws.recv()
            except Exception:
                return
            try:
                message = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise RealtimeProviderUnavailableError(
                    "Gemini Live sent a non-JSON message"
                ) from exc
            if self._on_usage is not None:
                await self._maybe_capture_usage(message)
            for frame in self._translate_server_message(message):
                yield frame

    async def _maybe_capture_usage(self, message: dict[str, Any]) -> None:
        """Fire on_usage exactly once per turn boundary (B2 TASK.md §3, M3).

        LIVE-VERIFIED shape (ai.google.dev/api/live, 2026-07-10): `usageMetadata` is a
        top-level sibling of `serverContent` on the SAME server message — never nested
        inside it. Gated on the SAME message ALSO carrying serverContent.turnComplete=true
        (the exact boundary _translate_server_message reads to emit response.done) so a
        usageMetadata block repeated on an earlier, non-boundary message can never fire a
        second, over-counted capture for the same turn — mirrors the shipped OpenAI
        per-turn (never session-aggregate) shape exactly.
        """
        server_content = message.get("serverContent")
        turn_complete = isinstance(server_content, dict) and server_content.get("turnComplete")
        if not turn_complete:
            return
        raw_usage = message.get("usageMetadata")
        if not isinstance(raw_usage, dict):
            _log.debug("gemini_usage_absent_skip", extra={"model": self._model})
            return
        try:
            await self._on_usage(self._translate_gemini_usage(raw_usage))  # type: ignore[misc]
        except Exception:
            # Mirrors the shipped OpenAI path (TASK.md §5 safety rule): a billing-pipe
            # failure must never disrupt the live relay session.
            _log.warning("gemini realtime usage capture failed (swallowed)", exc_info=True)

    @staticmethod
    def _translate_gemini_usage(raw: dict[str, Any]) -> dict[str, Any]:
        """Translate Gemini Live's usageMetadata into the recorder-canonical shape.

        Recorder-canonical shape (matches OpenAIRealtimeSession._translate_realtime_usage,
        read by gateway.usage.application.recorder._record_internal via _safe_tier):
          prompt_tokens, completion_tokens,
          prompt_tokens_details.cached_tokens,
          input_token_details.{audio_tokens,cached_tokens}, output_token_details.audio_tokens.

        Gemini's shape has no modality-split cached-token count (only a single, non-split
        `cachedContentTokenCount`) — mapped to the TEXT-tier `prompt_tokens_details.
        cached_tokens` only; the AUDIO-tier `input_token_details.cached_tokens` degrades to
        0 rather than reusing the combined total a second time (would double-count a
        fabricated value — never a fabricated non-zero estimate, per TASK.md §1 Reject).

        Never raises — every field degrades to 0 on absence/non-numeric type.
        """

        def _int(value: Any) -> int:
            return value if isinstance(value, int) else 0

        def _modality_tokens(details: Any, modality: str) -> int:
            if not isinstance(details, list):
                return 0
            for entry in details:
                if isinstance(entry, dict) and entry.get("modality") == modality:
                    return _int(entry.get("tokenCount", 0))
            return 0

        return {
            "prompt_tokens": _int(raw.get("promptTokenCount", 0)),
            "completion_tokens": _int(raw.get("responseTokenCount", 0)),
            "prompt_tokens_details": {
                "cached_tokens": _int(raw.get("cachedContentTokenCount", 0)),
            },
            "input_token_details": {
                "audio_tokens": _modality_tokens(raw.get("promptTokensDetails"), "AUDIO"),
                "cached_tokens": 0,
            },
            "output_token_details": {
                "audio_tokens": _modality_tokens(raw.get("responseTokensDetails"), "AUDIO"),
            },
        }

    @staticmethod
    def _translate_server_message(message: dict[str, Any]) -> list[RelayFrame]:
        frames: list[RelayFrame] = []
        if "setupComplete" in message:
            frames.append({"type": "session.created"})
        server_content = message.get("serverContent")
        if isinstance(server_content, dict):
            parts = server_content.get("modelTurn", {}).get("parts", [])
            for part in parts:
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData")
                if isinstance(inline, dict) and inline.get("data"):
                    frames.append(base64.b64decode(inline["data"]))
                elif part.get("text"):
                    frames.append({"type": "transcript", "role": "assistant", "text": part["text"]})
            if server_content.get("turnComplete"):
                frames.append({"type": "response.done"})
        error = message.get("error")
        if isinstance(error, dict):
            frames.append(
                {"type": "error", "code": "provider_error", "message": error.get("message", "")}
            )
        return frames
