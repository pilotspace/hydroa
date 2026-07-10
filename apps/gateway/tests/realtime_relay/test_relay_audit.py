"""Red suite: fire-and-forget audit lifecycle events for /v1/realtime/relay (B2 TASK.md §2, M6).

RED until realtime_relay_ws.py schedules session_opened / session_closed / session_rejected
AuditEvents via record_audit(). Pure-unit: a Starlette TestClient + app.state STUB seams,
with app.state.sessionmaker replaced by a spy that captures every AuditEvent handed to
record_audit() (no real DB — record_audit's own fail-open contract is exercised directly).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.core.config import Settings
from gateway.core.error_catalog import MODEL_UNKNOWN


class FakeProviderSession:
    def __init__(self, events: list | None = None) -> None:
        self._events = list(events or [])
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send_client_event(self, frame: dict) -> None:  # pragma: no cover
        pass

    async def send_audio(self, data: bytes) -> None:  # pragma: no cover
        pass

    async def events(self) -> AsyncIterator:
        for ev in self._events:
            yield ev

    async def aclose(self) -> None:
        self.closed = True


def _authz(**over) -> SimpleNamespace:
    base = dict(tenant_id=uuid.uuid4(), key_id=uuid.uuid4(), team_id=None)
    base.update(over)
    return SimpleNamespace(**base)


def _build_app(
    *, authenticate, session_factory, governance_authorize=None, sessionmaker, provider="openai"
) -> FastAPI:
    from gateway.proxy.api.realtime_relay_ws import realtime_relay_router

    app = FastAPI()
    app.include_router(realtime_relay_router)
    app.state.settings = Settings(
        realtime_auth_timeout_seconds=5.0,
        realtime_relay_provider=provider,
        realtime_relay_openai_model="gpt-realtime",
    )
    app.state.realtime_relay_authenticate = authenticate
    app.state.realtime_relay_session_factory = session_factory
    app.state.sessionmaker = sessionmaker
    if governance_authorize is not None:
        app.state.realtime_relay_governance_authorize = governance_authorize
    return app


async def _ok_auth(token, session):
    return _authz() if token == "sk-x" else None


def _factory(authz, websocket):
    return FakeProviderSession(events=[{"type": "session.created"}, {"type": "response.done"}])


@pytest.fixture
def audit_spy(monkeypatch):
    """Capture every AuditEvent passed to record_audit(), without touching the DB.

    record_audit() is imported into realtime_relay_ws at module scope; patch it there so the
    endpoint's fire-and-forget asyncio.ensure_future(record_audit(...)) calls resolve against
    this spy instead of a real session_factory.
    """
    captured: list = []

    async def _fake_record_audit(session_factory, event):
        captured.append(event)

    monkeypatch.setattr(
        "gateway.proxy.api.realtime_relay_ws.record_audit", _fake_record_audit
    )
    return captured


async def _drain_ensure_future() -> None:
    """Let any asyncio.ensure_future()-scheduled fire-and-forget tasks resolve."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# session_opened
# ---------------------------------------------------------------------------


def test_session_opened_audit_scheduled_on_governance_pass(audit_spy) -> None:
    async def _gov(token, model_id):
        return _authz()

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        governance_authorize=_gov,
        sessionmaker=lambda: None,
    )
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        ws.receive_json()
        ws.receive_json()

    actions = [e.action for e in audit_spy]
    assert "realtime_relay.session_opened" in actions
    opened = next(e for e in audit_spy if e.action == "realtime_relay.session_opened")
    assert opened.result == "success"
    assert opened.metadata["provider"] == "openai"
    assert opened.metadata["model"] == "gpt-realtime"
    assert opened.actor_key_id is not None
    assert "token" not in opened.metadata and "key" not in opened.metadata


# ---------------------------------------------------------------------------
# session_closed
# ---------------------------------------------------------------------------


def test_session_closed_audit_carries_close_code(audit_spy) -> None:
    async def _gov(token, model_id):
        return _authz()

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        governance_authorize=_gov,
        sessionmaker=lambda: None,
    )
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        ws.receive_json()
        ws.receive_json()

    closed = next(e for e in audit_spy if e.action == "realtime_relay.session_closed")
    assert closed.metadata["close_code"] == 1000  # provider stream ended normally


# ---------------------------------------------------------------------------
# session_rejected
# ---------------------------------------------------------------------------


def test_session_rejected_audit_scheduled_on_governance_failure(audit_spy) -> None:
    async def _gov(token, model_id):
        raise MODEL_UNKNOWN.exc(model_id=model_id)

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        governance_authorize=_gov,
        sessionmaker=lambda: None,
    )
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()

    rejected = next(e for e in audit_spy if e.action == "realtime_relay.session_rejected")
    assert rejected.result == "rejected"
    assert rejected.metadata["close_code"] == 4400
    assert not any(e.action == "realtime_relay.session_opened" for e in audit_spy)


# ---------------------------------------------------------------------------
# Reject: pre-identity rejection is never audited
# ---------------------------------------------------------------------------


def test_pre_identity_rejection_not_audited(audit_spy) -> None:
    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        sessionmaker=lambda: None,
    )
    with pytest.raises(WebSocketDisconnect):
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "nope"})
            ws.receive_text()

    assert audit_spy == []


# ---------------------------------------------------------------------------
# Reject: audit write failure never disrupts the relay
# ---------------------------------------------------------------------------


def test_audit_write_failure_does_not_disrupt_relay() -> None:
    """Exercises the REAL record_audit() — its own session_factory raises (DB unavailable);
    record_audit's existing fail-open contract swallows it, so the relay completes normally.
    """

    def _broken_sessionmaker():
        raise RuntimeError("db is down")

    async def _gov(token, model_id):
        return _authz()

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        governance_authorize=_gov,
        sessionmaker=_broken_sessionmaker,
    )
    # Must complete normally despite record_audit's own session_factory raising.
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        assert ws.receive_json() == {"type": "session.created"}
        assert ws.receive_json() == {"type": "response.done"}
