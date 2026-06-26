"""WebSocket endpoint /v1/realtime/relay — provider-agnostic full-duplex relay (v52).

Flow:
  accept
   -> first frame {type:"auth", token}  (bounded by realtime_auth_timeout_seconds)
        | close 4401  bad/missing/expired token, or first frame not auth
        | close 4408  no auth frame within the timeout
   -> select the configured provider session
        | close 4404  no realtime provider configured (honest-degrade)
   -> run the t1 RelayPump (it owns connect-timeout / breaker / teardown + the relay
      outcome close code: 4503 / 1011 / 4408-idle / 1000)

AUTH-OVER-WS (security HARD, inherited from v47): the token is read ONLY from the
first frame, NEVER from the URL/query, and NO provider session is opened before a
successful authenticate. Reuses v47 `_authenticate_token` verbatim.

app.state STUB seams (present → used instead of the real path; DB/key-free WS tests):
  realtime_relay_authenticate(token, session) -> AuthzResult | None
  realtime_relay_session_factory(authz, websocket) -> RealtimeRelaySession | None  (None → 4404)
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from typing import Any

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from gateway.core.config import Settings

# Reuse v47's auth-over-WS helper verbatim (the approved security surface).
from gateway.proxy.api.realtime_ws import (
    _authenticate_token,  # pyright: ignore[reportPrivateUsage]
)
from gateway.proxy.application.realtime_relay_pump import RelayPump
from gateway.proxy.domain.realtime_relay import RealtimeRelayClosed, RealtimeRelaySession

_log = logging.getLogger(__name__)

realtime_relay_router = APIRouter(tags=["proxy"])

_CODE_AUTH_INVALID = 4401
_CODE_AUTH_TIMEOUT = 4408
_CODE_NO_PROVIDER = 4404


class _StarletteRelayTransport:
    """Adapts a Starlette WebSocket to the RealtimeClientTransport seam."""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket
        self._closed = False

    async def receive(self) -> dict[str, Any] | bytes:
        msg = await self._ws.receive()
        if msg.get("type") == "websocket.disconnect":
            raise RealtimeRelayClosed("client disconnected")
        data = msg.get("bytes")
        if data is not None:
            return data
        text = msg.get("text")
        if text is not None:
            try:
                return json.loads(text)
            except (ValueError, TypeError) as exc:
                raise RealtimeRelayClosed("client sent a non-JSON text frame") from exc
        raise RealtimeRelayClosed("client frame had neither bytes nor text")

    async def send_event(self, frame: dict[str, Any]) -> None:
        await self._ws.send_json(frame)

    async def send_audio(self, data: bytes) -> None:
        await self._ws.send_bytes(data)

    async def close(self, code: int) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close(code)
        except Exception:
            _log.debug("relay transport close failed", exc_info=True)


async def _authenticate(app: Any, token: str) -> Any:
    """Authenticate the token via the stub seam, else v47 _authenticate_token."""
    stub = getattr(app.state, "realtime_relay_authenticate", None)
    if stub is not None:
        return await stub(token, None)
    async with app.state.sessionmaker() as session:
        return await _authenticate_token(token, session)


async def _build_session(app: Any, authz: Any, websocket: WebSocket) -> RealtimeRelaySession | None:
    """Build the provider session via the stub seam, else the real provider factory."""
    stub = getattr(app.state, "realtime_relay_session_factory", None)
    if stub is not None:
        result = stub(authz, websocket)
        if inspect.isawaitable(result):
            result = await result
        return result
    return await _real_session_factory(app, authz)


def _extract_secret(cred: Any) -> str | None:
    for attr in ("secret", "api_key"):
        value = getattr(cred, attr, None)
        if value is not None and hasattr(value, "get_secret_value"):
            return value.get_secret_value()
    return None


async def _real_session_factory(app: Any, authz: Any) -> RealtimeRelaySession | None:
    """Production provider build — honest-degrade (None) when nothing is configured."""
    settings: Settings = app.state.settings
    provider = settings.realtime_relay_provider
    if provider not in ("openai", "gemini"):
        return None
    resolver = getattr(app.state, "tenant_credential_resolver", None)
    if resolver is None:
        return None
    try:
        cred = await resolver.resolve(authz.tenant_id, provider)
    except Exception:
        return None
    api_key = _extract_secret(cred)
    if not api_key:
        return None
    if provider == "openai":
        from gateway.proxy.infrastructure.openai_realtime import OpenAIRealtimeSession

        return OpenAIRealtimeSession(
            model=settings.realtime_relay_openai_model,
            api_key=api_key,
            connect_timeout=settings.realtime_relay_connect_timeout_seconds,
        )
    from gateway.proxy.infrastructure.gemini_live import GeminiLiveSession

    return GeminiLiveSession(
        model=settings.realtime_relay_gemini_model,
        api_key=api_key,
        connect_timeout=settings.realtime_relay_connect_timeout_seconds,
    )


@realtime_relay_router.websocket("/v1/realtime/relay")
async def realtime_relay(websocket: WebSocket) -> None:
    app = websocket.app
    settings: Settings = app.state.settings
    await websocket.accept()

    # 1) AUTH-OVER-WS — first frame must be {type:"auth", token}, bounded by the timeout.
    try:
        first = await asyncio.wait_for(
            websocket.receive(), timeout=settings.realtime_auth_timeout_seconds
        )
    except TimeoutError:
        await websocket.close(_CODE_AUTH_TIMEOUT)
        return
    except WebSocketDisconnect:
        return

    if first.get("type") == "websocket.disconnect":
        return
    text = first.get("text")
    if text is None:
        await websocket.close(_CODE_AUTH_INVALID)  # binary / non-text first frame
        return
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        await websocket.close(_CODE_AUTH_INVALID)
        return
    token = payload.get("token") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("type") != "auth"
        or not isinstance(token, str)
        or not token
    ):
        # A non-string/empty token would crash the authenticator (500) — close cleanly.
        await websocket.close(_CODE_AUTH_INVALID)
        return

    authz = await _authenticate(app, token)
    if authz is None:
        await websocket.close(_CODE_AUTH_INVALID)
        return

    # 2) SELECT provider — honest-degrade when none configured.
    session = await _build_session(app, authz, websocket)
    if session is None:
        await websocket.close(_CODE_NO_PROVIDER)
        return

    # 3) RELAY — the pump owns connect/timeout/teardown + the outcome close code.
    transport = _StarletteRelayTransport(websocket)
    breaker = getattr(app.state, "realtime_relay_breaker", None)
    pump = RelayPump(transport, session, settings, breaker=breaker)
    await pump.run()
