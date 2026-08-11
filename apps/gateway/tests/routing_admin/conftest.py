"""Suite-local fixtures for routing_admin tests.

Design notes:
  - No live DB or Redis required. create_app() wires all app.state seams at
    construction time; tests override the gate and model_router after creation.
  - Auth uses JwtTokenService directly — issue a real HS256 token against the
    test jwt_secret. No signup/login DB round-trip needed.
  - FakeGate exposes snapshot_state (the additive RedisCooldownGate method
    being specified) so tests can assert state derivation without a real gate.
  - LocalFakeRedis is a LOCAL COPY of the minimal FakeRedis from the frozen
    tests/cooldown_circuit/conftest.py. Never imported from there — copy only.
    Minimal surface: GET, SET (EX, NX), no writes needed for snapshot_state
    (which only reads open and half keys).

Pattern: tests/cooldown_circuit_wiring/test_cooldown_circuit_wiring.py (no-DB
         create_app + app.state injection) and tests/model_mgmt/ (httpx ASGI
         transport + JWT auth headers).
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import create_async_engine

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.tenants.domain.entities import Role
from gateway.tenants.infrastructure.jwt_service import JwtTokenService
from tests import _redis_env

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_JWT_SECRET = "test-routing-admin-secret-xyzzy-0123456789"
TEST_DATABASE_URL = _redis_env.TEST_DATABASE_URL
TEST_REDIS_URL = _redis_env.TEST_REDIS_URL

ADMIN_ROUTING = "/admin/routing"
ADMIN_MODELS = "/admin/models"


# ---------------------------------------------------------------------------
# Minimal local FakeRedis (copy — NOT imported from frozen cooldown_circuit suite)
# Supports only GET and SET (EX, NX) — sufficient for snapshot_state read path.
# ---------------------------------------------------------------------------


class LocalFakeRedis:
    """In-memory async Redis fake for routing_admin tests.

    Minimal surface:  GET, SET (EX, NX), no write-side commands needed.
    snapshot_state only reads open and half keys — no INCR, EXPIRE, DEL needed.
    """

    def __init__(
        self,
        *,
        raise_on_command: bool = False,
        clock: Callable[[], float] | None = None,
    ) -> None:
        # key → (value: str, expires_at: float | None)
        self._store: dict[str, tuple[str, float | None]] = {}
        self._raise = raise_on_command
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

    def _now(self) -> float:
        return self._clock()

    def _get_live(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        val, exp = entry
        if exp is not None and exp <= self._now():
            del self._store[key]
            return None
        return val

    def seed(self, key: str, value: str = "1", *, ex: int | None = None) -> None:
        """Directly seed a key (test helper — skips the async interface)."""
        expires_at = (self._now() + ex) if ex is not None else None
        self._store[key] = (value, expires_at)

    async def get(self, key: str) -> str | None:
        if self._raise:
            raise ConnectionError("LocalFakeRedis: simulated connection error")
        return self._get_live(key)

    async def set(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
        nx: bool = False,
        **kwargs: Any,
    ) -> bool | None:
        if self._raise:
            raise ConnectionError("LocalFakeRedis: simulated connection error")
        existing = self._get_live(key)
        if nx and existing is not None:
            return None
        expires_at = (self._now() + ex) if ex is not None else None
        self._store[key] = (str(value), expires_at)
        return True

    async def aclose(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Fake gate objects
# ---------------------------------------------------------------------------


class FakeGate:
    """Fake cooldown gate with configurable per-model snapshot_state responses.

    Does NOT implement is_available/record_failure/record_success — this suite
    only tests the routing-admin read surface, which calls snapshot_state only.
    """

    def __init__(self, states: dict[str, str] | None = None) -> None:
        # model_id → state string ("closed", "open", "half_open", "unknown")
        self._states: dict[str, str] = states or {}
        self.calls: list[str] = []

    async def snapshot_state(self, model_id: str) -> str:
        self.calls.append(model_id)
        return self._states.get(model_id, "closed")


class ErrorGate:
    """Gate whose snapshot_state always raises ConnectionError (for RA4)."""

    async def snapshot_state(self, model_id: str) -> str:
        raise ConnectionError("ErrorGate: simulated Redis error")


class CalledGate:
    """Gate that raises AssertionError if snapshot_state is ever called (for RA2)."""

    async def snapshot_state(self, model_id: str) -> str:
        raise AssertionError(
            f"snapshot_state must NOT be called when gate is None; called with {model_id!r}"
        )


# ---------------------------------------------------------------------------
# Settings factory
# ---------------------------------------------------------------------------


def make_settings(**kwargs: Any) -> Settings:
    """Build minimal Settings (no live DB/Redis required for routing-admin tests)."""
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": TEST_REDIS_URL,
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# JWT helper
# ---------------------------------------------------------------------------


def issue_jwt(
    settings: Settings,
    *,
    role: Role = Role.OWNER,
) -> str:
    """Issue a real HS256 JWT for the given role using the test jwt_secret."""
    svc = JwtTokenService(settings)
    token, _ = svc.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=role,
        email="test@example.com",
    )
    return token


# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def pristine_routing_config() -> AsyncIterator[None]:
    """Clear the persisted routing-config singleton before every test in this suite.

    THIS SUITE'S ARRANGE, not a workaround. `/admin/routing` GET prefers the row in
    `routing_config` over the settings-derived config, and that table holds exactly ONE
    row for the whole database (`id IS TRUE`, a singleton by CHECK constraint). So it is
    cross-test shared state for every suite that reads it.

    Most suites never notice: the root `app` fixture rebuilds the schema and DELETEs every
    table per test. This suite deliberately does NOT use it — `base_app` calls create_app()
    directly so it can inject custom Settings — which also means nothing here clears the
    table it reads. Under `--dist loadscope` any file scheduled onto the same worker before
    this one can leave a row behind (`tests/routing_config_write` ends by persisting
    `{"gpt": ["a"]}`), and then RA1/RA2/RA3/RA4/RA7 assert the settings-derived config and
    get the neighbour's instead.

    That is the whole of todo #99, which had been recorded as an unexplained app.state or
    Settings leak after four probes failed to reproduce it. It is neither: it is a database
    row. `pytest tests/routing_config_write tests/routing_admin` reproduces all five
    failures in 5 seconds, deterministically.

    Fail-soft on a missing table: if no suite has built the schema on this worker yet there
    is no persisted override to clear, which is exactly the state this fixture wants.
    """
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM routing_config"))
    except (ProgrammingError, OperationalError):
        pass
    finally:
        await engine.dispose()
    yield


@pytest.fixture
def base_settings() -> Settings:
    """Default settings with empty model_groups and cooldown disabled."""
    return make_settings()


@pytest.fixture
async def base_app(base_settings: Settings) -> Any:
    """A minimal gateway app (no DB schema needed — no DB routes exercised)."""
    return create_app(base_settings)


@pytest.fixture
async def base_client(base_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=base_app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Auth header helper
# ---------------------------------------------------------------------------


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
