"""Suite-local fixtures for service-tiers tests (TASK.md §4).

Mirrors tests/credits_ledger/conftest.py's signup->login->create-key idiom and
tests/region_pricing/conftest.py's FakeSession/StreamCapture idiom for the pure-unit
billing-composition tests (no infra).

Infrastructure:
  - Real Postgres: GATEWAY_TEST_DATABASE_URL (see tests/conftest.py)
  - Real Redis: redis://localhost:6380/9 (tier-capacity pool ZSETs — direct-guard tests
    use a SEPARATE db-9 client, flushed before/after, mirrors credits_ledger's own
    `redis_client` fixture)
  - httpx.ASGITransport (no network)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env


# ---------------------------------------------------------------------------
# Fake DB primitives (mirrors tests/region_pricing/conftest.py)
# ---------------------------------------------------------------------------


class FakeRow:
    def __init__(self, values: tuple[Any, ...]) -> None:
        self._values = values

    def __getitem__(self, idx: int) -> Any:
        return self._values[idx]


class FakeResult:
    def __init__(self, row: FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> FakeRow | None:
        return self._row


class FakeSession:
    """In-memory async session intercepting the tier-markup override query.

    tier_markup_override: the SINGLE tenant's priority-markup override pct, or None
    (falls back to the DECIDED +25% seed).
    """

    def __init__(self, *, tier_markup_override: Decimal | None = None) -> None:
        self.tier_markup_override = tier_markup_override
        self.executed: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        self.executed.append(sql)
        if "tenant_priority_markup_overrides" in sql:
            if self.tier_markup_override is None:
                return FakeResult(None)
            return FakeResult(FakeRow((str(self.tier_markup_override),)))
        return FakeResult(None)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeSessionFactory:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


class StreamCapture:
    """Minimal async Redis fake capturing xadd() fields + incrbyfloat() calls."""

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.incr_calls: list[tuple[str, float]] = []

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> bytes:
        self.events.append(dict(fields))
        return b"1234567890123-0"

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.incr_calls.append((key, amount))
        return amount

    @property
    def last_event(self) -> dict[str, str]:
        assert self.events, "No events captured — recorder was not called"
        return self.events[-1]


# ---------------------------------------------------------------------------
# HTTP-level fixtures (mirrors tests/credits_ledger/conftest.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def key_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def stream_capture() -> StreamCapture:
    return StreamCapture()


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create API key; returns ids + plaintext key (owner role)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ServiceTiersCo",
            "email": "owner@servicetiersco.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201, signup.text
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@servicetiersco.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "service-tiers-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def other_api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """A second, independent tenant — for the "other tenants unaffected" scenarios."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ServiceTiersOtherCo",
            "email": "owner@servicetiersother.io",
            "password": "correct horse battery 2",
        },
    )
    assert signup.status_code == 201, signup.text
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@servicetiersother.io", "password": "correct horse battery 2"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "service-tiers-other-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.commit()
    return model_id


@pytest.fixture
async def priced_model(db_session: AsyncSession) -> str:
    """Active model + a pricing snapshot — billing-composition tests need a real,
    non-zero catalog rate (mirrors tests/credits_ledger's active_model_with_pricing
    and tests/region_pricing's seed_region_model)."""
    model_id = "openai/gpt-4o-priced"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-priced"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.01, 0.01, now())"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


def _issue_token(app: Any, *, role: Any, tenant_id: uuid.UUID, email: str) -> str:
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    assert resp.json()["code"] == code, resp.text


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before/after (mirrors
    tests/credits_ledger's own redis_client fixture — same db the app fixture's
    settings.redis_url already points at)."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()
