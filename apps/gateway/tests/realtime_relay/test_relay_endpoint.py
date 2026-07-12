"""Red suite for the /v1/realtime/relay WS endpoint (v52 realtime-relay-endpoint).

RED until proxy.api.realtime_relay_ws exists. Drives the handler through a
Starlette TestClient with app.state STUB seams (fake authenticate + fake session
factory) — NO DB, NO network, NO key. Proves auth-over-WS (4401/4408),
honest-degrade (4404), and a full-duplex relay end-to-end over a fake provider
session.

Governance stub (realtime-relay-governance TASK.md §3, M1 — added 2026-07-10): the
endpoint now runs an ADDITIVE connect-time governance step after auth, before session
build. This suite's own concern is auth-over-WS + relay mechanics, unchanged by M1 — a
permissive `_permissive_governance` stub is wired by default (mirrors the SAME
app.state STUB seam convention `realtime_relay_authenticate`/`realtime_relay_session_factory`
already use) so these DB-free tests keep exercising exactly what they always exercised.
Every existing assertion (close codes, full-duplex frame order) is byte-identical.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.core.config import Settings


class FakeProviderSession:
    """A finite fake RealtimeRelaySession for the happy path."""

    def __init__(self, events: list | None = None) -> None:
        self._events = list(events or [])
        self.sent_audio: list[bytes] = []
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send_client_event(self, frame: dict) -> None:  # pragma: no cover - unused here
        pass

    async def send_audio(self, data: bytes) -> None:
        self.sent_audio.append(data)

    async def events(self) -> AsyncIterator:
        for ev in self._events:
            yield ev

    async def aclose(self) -> None:
        self.closed = True


async def _permissive_governance(token: str, model_id: str) -> SimpleNamespace:
    """Default governance stub: always PASSES with a fresh synthetic identity — this
    suite's own concern is auth-over-WS + relay mechanics, not governance (see M1 note
    above; governance rejection paths are covered by test_relay_governance.py)."""
    return SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=None)


def _build_app(
    *,
    authenticate,
    session_factory,
    auth_timeout: float = 5.0,
    governance_authorize=None,
) -> FastAPI:
    from gateway.proxy.api.realtime_relay_ws import realtime_relay_router

    app = FastAPI()
    app.include_router(realtime_relay_router)
    app.state.settings = Settings(
        realtime_auth_timeout_seconds=auth_timeout,
        realtime_relay_provider="openai",
    )
    app.state.realtime_relay_authenticate = authenticate
    app.state.realtime_relay_session_factory = session_factory
    app.state.realtime_relay_governance_authorize = governance_authorize or _permissive_governance
    # No real DB in this suite — record_audit's own fail-open contract swallows the
    # resulting failure when the fire-and-forget session_opened/closed events fire.
    app.state.sessionmaker = _no_db_sessionmaker
    return app


def _no_db_sessionmaker() -> None:
    raise RuntimeError("no DB wired in this DB-free suite")


async def _ok_auth(token, session):
    return (
        SimpleNamespace(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=None)
        if token == "sk-x"
        else None
    )


def test_authed_full_duplex_relay() -> None:
    captured: dict = {}

    def _factory(authz, websocket):
        session = FakeProviderSession(
            events=[
                {"type": "session.created"},
                b"\x01\x02\x03",
                {"type": "response.done"},
            ]
        )
        captured["session"] = session
        return session

    app = _build_app(authenticate=_ok_auth, session_factory=_factory)
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        ws.send_bytes(b"\x09")  # one client audio frame
        assert ws.receive_json() == {"type": "session.created"}
        assert ws.receive_bytes() == b"\x01\x02\x03"
        assert ws.receive_json() == {"type": "response.done"}
    assert captured["session"].connected is True
    assert captured["session"].closed is True


def test_bad_token_4401() -> None:
    built: dict = {}

    def _factory(authz, websocket):
        built["called"] = True
        return FakeProviderSession()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "nope"})
            ws.receive_text()
    assert exc.value.code == 4401
    assert "called" not in built


def test_first_frame_not_auth_4401() -> None:
    app = _build_app(authenticate=_ok_auth, session_factory=lambda a, w: FakeProviderSession())
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "audio.commit"})
            ws.receive_text()
    assert exc.value.code == 4401


def test_auth_timeout_4408() -> None:
    app = _build_app(
        authenticate=_ok_auth,
        session_factory=lambda a, w: FakeProviderSession(),
        auth_timeout=0.1,
    )
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.receive_text()  # send nothing → server closes 4408
    assert exc.value.code == 4408


def test_no_provider_4404() -> None:
    built: dict = {}

    def _factory(authz, websocket):
        built["called"] = True
        return None  # provider unconfigured → honest-degrade

    app = _build_app(authenticate=_ok_auth, session_factory=_factory)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4404


def test_non_string_token_4401() -> None:
    """SECURITY (F2): a non-string token must close 4401, not crash the authenticator (500)."""
    built: dict = {}

    def _factory(authz, websocket):
        built["called"] = True
        return FakeProviderSession()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": ["not", "a", "string"]})
            ws.receive_text()
    assert exc.value.code == 4401
    assert "called" not in built


def test_url_token_ignored_4401() -> None:
    app = _build_app(authenticate=_ok_auth, session_factory=lambda a, w: FakeProviderSession())
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay?token=sk-x") as ws:
            ws.send_json(
                {"type": "session.update"}
            )  # non-auth first frame; URL token must be ignored
            ws.receive_text()
    assert exc.value.code == 4401
