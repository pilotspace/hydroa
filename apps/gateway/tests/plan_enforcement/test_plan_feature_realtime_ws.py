"""RED suite: M6/R6/M9 — WS /v1/realtime/relay connect-time feature gate (TASK.md §3,
FROZEN @ v1).

Deliberately does NOT use the `realtime_relay_governance_authorize` stub seam (unlike
tests/realtime_relay/test_relay_governance.py) — this suite exercises the REAL
`_authorize_governance` path end-to-end against a real Postgres + real key/plan, so the
actual `check_plan_feature` wiring inside realtime_relay_ws.py is proven, not just the
mechanical ProblemError->WS-close-code translation (which test_relay_governance.py already
covers exhaustively for the OTHER rejection reasons).

Own app + own DB schema (mirrors tests/conftest.py's `app` fixture exactly, extended with
the realtime_relay_* Settings fields test_relay_governance.py's own `_build_app` needs) —
the root `app` fixture's plain Settings() has no realtime provider configured.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET

from .conftest import assign_plan, seed_active_model, seed_plan, signup_owner
from tests import _redis_env

_MODEL = "gpt-realtime-planenf"


@pytest.fixture
async def ws_app() -> AsyncIterator[Any]:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # type: ignore[call-arg]
        realtime_relay_provider="openai",
        realtime_relay_openai_model=_MODEL,
        realtime_auth_timeout_seconds=5.0,
    )
    application = create_app(settings)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def ws_http_client(ws_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=ws_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def ws_db_session(ws_app: Any) -> AsyncIterator[AsyncSession]:
    async with ws_app.state.sessionmaker() as session:
        yield session


@pytest.fixture
async def owner(ws_http_client: httpx.AsyncClient) -> dict[str, str]:
    return await signup_owner(ws_http_client, tenant_name="RealtimeGateCo", email="owner@rtgate.io")


@pytest.fixture
async def active_model(ws_db_session: AsyncSession) -> str:
    return await seed_active_model(ws_db_session, model_id=_MODEL)


async def _connect_and_auth(ws_app: Any, key: str) -> int:
    """Open the WS, send the auth frame, drain until close; return the close code.

    Starlette's TestClient drives the ASGI app from its OWN portal thread/event loop —
    disjoint from pytest-asyncio's loop the async setup fixtures (ws_db_session,
    ws_http_client) ran on. Any pooled asyncpg connection opened during that async setup
    is bound to the pytest loop, so it MUST be disposed first (engine.dispose() is safe
    to call from any loop) or the pool hands the TestClient's loop a connection created
    on a different loop ("Future attached to a different loop").
    """
    await ws_app.state.engine.dispose()
    client = TestClient(ws_app)
    with pytest.raises(WebSocketDisconnect) as exc:  # noqa: PT012
        with client.websocket_connect("/v1/realtime/relay") as ws:
            ws.send_json({"type": "auth", "token": key})
            ws.receive_text()
    return exc.value.code


async def test_realtime_connect_refused_for_plan_lacking_feature(
    ws_app: Any,
    ws_db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
) -> None:
    plan_id = await seed_plan(ws_db_session, name="starter", feature_flags=["logs_explorer"])
    await assign_plan(ws_db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    close_code = await _connect_and_auth(ws_app, owner["key"])

    assert close_code == 4403, f"expected 4403 (governance 403), got {close_code}"


async def test_realtime_connect_proceeds_for_plan_granting_feature(
    ws_app: Any,
    ws_db_session: AsyncSession,
    owner: dict[str, str],
    active_model: str,
) -> None:
    """M6 happy path — governance (incl. the new feature gate) passes; the relay then
    falls through to session-build, which fails honestly (no real OpenAI creds wired in
    this DB-only suite) rather than a governance rejection. Proves the feature gate did
    NOT fire, without needing a live provider."""
    plan_id = await seed_plan(ws_db_session, name="enterprise", feature_flags=["realtime"])
    await assign_plan(ws_db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    close_code = await _connect_and_auth(ws_app, owner["key"])

    assert close_code != 4403, "governance must NOT reject when the plan grants 'realtime'"


async def test_unplanned_tenant_realtime_connect_unaffected(
    ws_app: Any,
    owner: dict[str, str],
    active_model: str,
) -> None:
    """M7 — no plan assigned -> the feature gate never fires; any non-4403 outcome
    (honest-degrade on the unconfigured credential, in this DB-only suite) proves it."""
    close_code = await _connect_and_auth(ws_app, owner["key"])

    assert close_code != 4403
