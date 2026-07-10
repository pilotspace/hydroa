"""Red suite: connect-time governance for /v1/realtime/relay (B2 TASK.md §2, M1/M2).

RED until realtime_relay_ws.py runs NonChatGovernance.authorize() at connect-time.
Pure-unit: a Starlette TestClient + app.state STUB seams — NO DB, NO network, NO key.

New test seam (mirrors the existing realtime_relay_authenticate / realtime_relay_session_factory
convention already documented in realtime_relay_ws.py's own module docstring):
  app.state.realtime_relay_governance_authorize(token, model_id) -> AuthzResult-like
    raises ProblemError to simulate a governance rejection.

Scenarios covered (TASK.md §2):
  - authed relay passes governance and opens a session (M1, M2 happy path)
  - dialed model unknown in the catalog closes 4400
  - dialed model not on the key's allowlist / tenant-disabled closes 4403
  - per-key/team/tenant budget exhausted closes 4402
  - RPM exceeded at connect closes 4429
  - existing auth-over-WS outcomes are unchanged (M8 regression guard)
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
from gateway.core.error_catalog import (
    BUDGET_EXCEEDED,
    MODEL_DISABLED,
    MODEL_NOT_ALLOWED,
    MODEL_UNKNOWN,
    RATE_LIMITED,
)


class FakeProviderSession:
    def __init__(self, events: list | None = None) -> None:
        self._events = list(events or [])
        self.connected = False
        self.closed = False

    async def connect(self) -> None:
        self.connected = True

    async def send_client_event(self, frame: dict) -> None:  # pragma: no cover - unused here
        pass

    async def send_audio(self, data: bytes) -> None:  # pragma: no cover - unused here
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
    *,
    authenticate,
    session_factory,
    governance_authorize=None,
    auth_timeout: float = 5.0,
    provider: str = "openai",
) -> FastAPI:
    from gateway.proxy.api.realtime_relay_ws import realtime_relay_router

    app = FastAPI()
    app.include_router(realtime_relay_router)
    app.state.settings = Settings(
        realtime_auth_timeout_seconds=auth_timeout,
        realtime_relay_provider=provider,
        realtime_relay_openai_model="gpt-realtime",
    )
    app.state.realtime_relay_authenticate = authenticate
    app.state.realtime_relay_session_factory = session_factory
    if governance_authorize is not None:
        app.state.realtime_relay_governance_authorize = governance_authorize
    # No real DB in this suite — record_audit's own fail-open contract swallows the
    # resulting failure when the fire-and-forget session_opened/closed/rejected events fire.
    app.state.sessionmaker = _no_db_sessionmaker
    return app


def _no_db_sessionmaker() -> None:
    raise RuntimeError("no DB wired in this DB-free suite")


async def _ok_auth(token, session):
    return _authz() if token == "sk-x" else None


def _factory(authz, websocket):
    return FakeProviderSession(events=[{"type": "session.created"}, {"type": "response.done"}])


# ---------------------------------------------------------------------------
# M1/M2 happy path
# ---------------------------------------------------------------------------


def test_governance_pass_opens_session() -> None:
    async def _gov(token, model_id):
        assert model_id == "gpt-realtime"
        return _authz()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        assert ws.receive_json() == {"type": "session.created"}
        assert ws.receive_json() == {"type": "response.done"}


# ---------------------------------------------------------------------------
# M1/M2 rejection close codes (mechanical: 4000 + exc.status)
# ---------------------------------------------------------------------------


def test_model_unknown_closes_4400() -> None:
    built: dict = {}

    def _fail_factory(authz, websocket):
        built["called"] = True
        return FakeProviderSession()

    async def _gov(token, model_id):
        raise MODEL_UNKNOWN.exc(model_id=model_id)

    app = _build_app(
        authenticate=_ok_auth, session_factory=_fail_factory, governance_authorize=_gov
    )
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4400
    assert "called" not in built, "no provider session may be built when governance rejects"


def test_model_not_allowed_closes_4403() -> None:
    async def _gov(token, model_id):
        raise MODEL_NOT_ALLOWED.exc()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4403


def test_model_disabled_closes_4403() -> None:
    async def _gov(token, model_id):
        raise MODEL_DISABLED.exc()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4403


def test_budget_exceeded_closes_4402() -> None:
    async def _gov(token, model_id):
        raise BUDGET_EXCEEDED.exc(detail="over budget")

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4402


def test_rpm_exceeded_closes_4429() -> None:
    async def _gov(token, model_id):
        raise RATE_LIMITED.exc(detail="rpm exceeded", headers={"Retry-After": "1"})

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4429


# ---------------------------------------------------------------------------
# TPM pre-flight is never invoked for a relay connect (documented inapplicability)
# ---------------------------------------------------------------------------


def test_governance_called_with_estimated_tokens_none() -> None:
    """The relay must call authorize() the same way the stub receives it — no TPM estimate."""
    captured: dict = {}

    async def _gov(token, model_id):
        captured["model_id"] = model_id
        return _authz()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
        ws.send_json({"type": "auth", "token": "sk-x"})
        ws.receive_json()
        ws.receive_json()
    assert captured["model_id"] == "gpt-realtime"


# ---------------------------------------------------------------------------
# M8 regression guard — existing auth-over-WS outcomes unchanged; governance
# unreached when identity fails first.
# ---------------------------------------------------------------------------


def test_bad_token_4401_governance_never_reached() -> None:
    gov_called: dict = {}

    async def _gov(token, model_id):
        gov_called["called"] = True
        return _authz()

    app = _build_app(authenticate=_ok_auth, session_factory=_factory, governance_authorize=_gov)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "nope"})
            ws.receive_text()
    assert exc.value.code == 4401
    assert "called" not in gov_called


def test_auth_timeout_4408_governance_never_reached() -> None:
    gov_called: dict = {}

    async def _gov(token, model_id):
        gov_called["called"] = True
        return _authz()

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=_factory,
        governance_authorize=_gov,
        auth_timeout=0.1,
    )
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.receive_text()
    assert exc.value.code == 4408
    assert "called" not in gov_called


def test_no_provider_configured_governance_never_reached() -> None:
    """settings.realtime_relay_provider unset -> dialed_model is None -> no governance call."""
    gov_called: dict = {}

    async def _gov(token, model_id):
        gov_called["called"] = True
        return _authz()

    app = _build_app(
        authenticate=_ok_auth,
        session_factory=lambda a, w: None,
        governance_authorize=_gov,
        provider="",
    )
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with TestClient(app).websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": "sk-x"})
            ws.receive_text()
    assert exc.value.code == 4404
    assert "called" not in gov_called
