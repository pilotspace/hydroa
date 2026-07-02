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
import logging
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
_log = logging.getLogger(__name__)


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
        on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        # default: dial OpenAI Realtime for real; tests inject a fake factory.
        self._ws_connect = ws_connect or self._dial_openai
        self._url = url
        self._connect_timeout = connect_timeout
        self._ws: RealtimeWebSocket | None = None
        # gpt-realtime-relay-billing (TASK.md §3): optional per-turn usage capture callback.
        # Never widens the RealtimeRelaySession Protocol — Gemini Live has no equivalent param.
        self._on_usage = on_usage

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
            if event.get("type") == "response.done" and self._on_usage is not None:
                raw_usage = event.get("usage")
                if isinstance(raw_usage, dict):
                    try:
                        await self._on_usage(self._translate_realtime_usage(raw_usage))
                    except Exception:
                        # gpt-realtime-relay-billing (TASK.md §5 safety rule): a billing-pipe
                        # failure must never disrupt the live relay session.
                        _log.warning("realtime usage capture failed (swallowed)", exc_info=True)
                elif raw_usage is not None:
                    _log.warning(
                        "usage_malformed_skip: response.done usage is not a dict",
                        extra={"usage_type": type(raw_usage).__name__},
                    )
            frame = self._translate_server_event(event)
            if frame is not None:
                yield frame

    @staticmethod
    def _translate_realtime_usage(raw: dict[str, Any]) -> dict[str, Any]:
        """Translate OpenAI Realtime's usage shape into the recorder-canonical shape.

        `_record_internal` already reads `prompt_tokens`/`completion_tokens` and, via
        `_safe_tier`, `prompt_tokens_details.cached_tokens` — this maps Realtime's
        `input_tokens`/`output_tokens`/`input_token_details`/`output_token_details` onto
        those exact keys, plus repackages the nested detail dicts so the 3 new audio-tier
        `_safe_tier` reads (input_token_details.audio_tokens, output_token_details.audio_tokens,
        input_token_details.cached_tokens) resolve to the correct values.

        IMPORTANT: `input_token_details.cached_tokens` in OpenAI's RAW payload is the
        COMBINED text+audio cached total, not audio-specific — the text/audio split lives
        one level deeper at `input_token_details.cached_tokens_details.{text,audio}_tokens`.
        This method un-nests that split so downstream code (which only reads one level of
        nesting via `_safe_tier`) sees the correct per-stream values: the translated
        `prompt_tokens_details.cached_tokens` carries the TEXT-cached count, and the
        translated `input_token_details.cached_tokens` carries the AUDIO-cached count —
        never the same ambiguous combined total for both (that was a real double/mis-count
        bug caught by an adversarial review before this task's VERIFY gate).

        Never raises — a missing/non-dict/non-numeric field simply reads as absent
        downstream via _safe_tier's own fail-safe (returns 0), never propagating here.
        """
        input_details = raw.get("input_token_details")
        if not isinstance(input_details, dict):
            input_details = {}
        output_details = raw.get("output_token_details")
        if not isinstance(output_details, dict):
            output_details = {}
        cached_details = input_details.get("cached_tokens_details")
        if not isinstance(cached_details, dict):
            cached_details = {}
        text_cached_tokens = cached_details.get("text_tokens", 0)
        audio_cached_tokens = cached_details.get("audio_tokens", 0)
        input_tokens = raw.get("input_tokens", 0)
        output_tokens = raw.get("output_tokens", 0)
        return {
            "prompt_tokens": input_tokens if isinstance(input_tokens, int) else 0,
            "completion_tokens": output_tokens if isinstance(output_tokens, int) else 0,
            "prompt_tokens_details": {"cached_tokens": text_cached_tokens},
            "input_token_details": {
                "audio_tokens": input_details.get("audio_tokens", 0),
                "cached_tokens": audio_cached_tokens,
            },
            "output_token_details": output_details,
        }

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
