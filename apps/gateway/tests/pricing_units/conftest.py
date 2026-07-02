"""Suite-local fixtures for pricing_units tests.

All recorder-unit tests use a fake DB session (FakeSession) and a fake Redis
stream sink (StreamCapture) so the suite requires no live infrastructure.

PU6 / PU7 use real Postgres (localhost:5433 gateway_test) + real Redis
(localhost:6380 db 9) — the same connection profile as the shared conftest.
Those tests flush via UsageLedgerFlusher and assert DB column presence.

Design notes:
- FakeSession mocks _fetch_latest_pricing and _fetch_markup_pct by returning
  hard-coded rows via FakeRow objects.
- StreamCapture records every xadd() call so tests can assert event fields.
- FakeSessionFactory wraps FakeSession in an async context-manager protocol.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any
from collections.abc import AsyncIterator

import pytest


# ---------------------------------------------------------------------------
# Fake DB primitives
# ---------------------------------------------------------------------------


class FakeRow:
    """Minimal row proxy returned by FakeResult.fetchone()."""

    def __init__(self, values: tuple[Any, ...]) -> None:
        self._values = values

    def __getitem__(self, idx: int) -> Any:
        return self._values[idx]


class FakeResult:
    """Minimal query result returned by FakeSession.execute()."""

    def __init__(self, row: FakeRow | None) -> None:
        self._row = row

    def fetchone(self) -> FakeRow | None:
        return self._row


class FakeSession:
    """In-memory async session that intercepts the two SQL queries issued by
    _fetch_latest_pricing and _fetch_markup_pct.

    The caller sets pricing_unit, prompt_price, completion_price,
    unit_usd_per_unit, and markup_pct before the test runs.

    SQL routing uses a simple substring match on the query text:
      - "pricing_snapshots" → returns the pricing snapshot row
      - "tenants"           → returns the markup row
    """

    def __init__(
        self,
        *,
        snapshot_id: uuid.UUID | None = None,
        prompt_price: Decimal = Decimal("0"),
        completion_price: Decimal = Decimal("0"),
        pricing_unit: str = "per_token",
        unit_usd_per_unit: Decimal | None = None,
        markup_pct: Decimal = Decimal("0"),
        has_pricing: bool = True,
    ) -> None:
        self.snapshot_id = snapshot_id or uuid.uuid4()
        self.prompt_price = prompt_price
        self.completion_price = completion_price
        self.pricing_unit = pricing_unit
        self.unit_usd_per_unit = unit_usd_per_unit
        self.markup_pct = markup_pct
        self.has_pricing = has_pricing

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        # Detect which query this is by looking at the SQL text
        sql = str(stmt)
        if "pricing_snapshots" in sql:
            if not self.has_pricing:
                return FakeResult(None)
            return FakeResult(
                FakeRow(
                    (
                        str(self.snapshot_id),
                        str(self.prompt_price),
                        str(self.completion_price),
                        self.pricing_unit,
                        str(self.unit_usd_per_unit) if self.unit_usd_per_unit is not None else None,
                        # tiered-token-billing extended _fetch_latest_pricing to a 7-tuple;
                        # this sibling double carries NULL tier prices (→ base-price fallback,
                        # leaving per-unit pricing-units assertions byte-identical).
                        None,  # cached_input_usd_per_token
                        None,  # reasoning_usd_per_token
                        None,  # cache_creation_usd_per_token (prompt-cache-passthrough §3)
                        # gpt-realtime-relay-billing extended _fetch_latest_pricing to an 11-tuple;
                        # NULL audio prices (byte-identical for every non-realtime model here).
                        None,  # audio_prompt_usd_per_token
                        None,  # audio_completion_usd_per_token
                        None,  # audio_cached_usd_per_token
                    )
                )
            )
        if "tenants" in sql:
            return FakeResult(FakeRow((str(self.markup_pct),)))
        return FakeResult(None)

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


class FakeSessionFactory:
    """Async sessionmaker-compatible factory wrapping a FakeSession."""

    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


# ---------------------------------------------------------------------------
# Fake Redis stream sink
# ---------------------------------------------------------------------------


class StreamCapture:
    """Minimal async Redis fake that captures xadd() calls.

    Captures the fields dict from every xadd() call so tests can assert
    on event fields (cost_usd, pricing_unit, quantity, etc.).
    Also records incrbyfloat calls for spend-counter assertions.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, str]] = []
        self.incr_calls: list[tuple[str, float]] = []

    async def xadd(self, stream_key: str, fields: dict[str, str]) -> bytes:
        self.events.append(dict(fields))
        # Return a fake stream ID
        return b"1234567890123-0"

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.incr_calls.append((key, amount))
        return amount

    @property
    def last_event(self) -> dict[str, str]:
        assert self.events, "No events captured — recorder was not called"
        return self.events[-1]


# ---------------------------------------------------------------------------
# Call-counting spy recorder (for PU10 single-bill test)
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Minimal UsageRecorder-compatible spy that counts record() calls."""

    supported_extras: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self.call_count = 0

    async def record(self, **kwargs: Any) -> None:
        self.call_count += 1


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def snapshot_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def key_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def per_token_session(snapshot_id: uuid.UUID) -> FakeSession:
    return FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.0000025"),
        completion_price=Decimal("0.00001"),
        pricing_unit="per_token",
        unit_usd_per_unit=None,
        markup_pct=Decimal("20"),
    )


@pytest.fixture
def stream_capture() -> StreamCapture:
    return StreamCapture()


# ---------------------------------------------------------------------------
# api_key fixture for DB-backed tests (PU6, PU7)
# Mirrors the pattern in tests/usage/test_usage_metering.py — signup + login
# + create key via the HTTP admin API.
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key.

    Tenant gets the default markup_pct=20.
    Requires the shared `client` fixture (from the root conftest).
    """
    import httpx  # type: ignore[import-untyped]

    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "PricingUnitsTest",
            "email": "pu-test@example.io",
            "password": "pricing units battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "pu-test@example.io", "password": "pricing units battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "pu-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
