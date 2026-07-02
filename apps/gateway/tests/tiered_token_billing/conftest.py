"""Suite-local fixtures for tiered-token-billing tests (TASK.md §4).

Mirrors tests/pricing_units/conftest.py but extends FakeSession so the fake
`pricing_snapshots` row carries the TWO new tier-price columns
(cached_input_usd_per_token, reasoning_usd_per_token) at positions [5] and [6] —
the 7-tuple the FROZEN §3 contract pins for `_fetch_latest_pricing`.

Before BUILD: the live `_fetch_latest_pricing` reads only row[0..4], so the two
tier prices are dropped and the recorder bills FLAT — which is exactly why the
tiered-cost assertions go RED for the right reason (missing tiered logic), not a
broken harness.

Unit tests use FakeSession + StreamCapture (no infra). The DB persistence tests
use real Postgres (localhost:5433 gateway_test) + Redis (localhost:6380 db 9) via
the shared root conftest fixtures, same as pricing_units PU6/PU7.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake DB primitives (extended to the 7-tuple pricing row)
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
    """In-memory async session intercepting `_fetch_latest_pricing` +
    `_fetch_markup_pct`. Returns an 8-value pricing row:

      (id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit,
       cached_input_price, reasoning_price, cache_creation_price)

    cache_creation_price (prompt-cache-passthrough TASK.md §3): added at position [7];
    existing callers that don't set it get None (→ prompt-rate fallback, byte-identical).
    """

    def __init__(
        self,
        *,
        snapshot_id: uuid.UUID | None = None,
        prompt_price: Decimal = Decimal("0"),
        completion_price: Decimal = Decimal("0"),
        pricing_unit: str = "per_token",
        unit_usd_per_unit: Decimal | None = None,
        cached_input_price: Decimal | None = None,
        reasoning_price: Decimal | None = None,
        cache_creation_price: Decimal | None = None,
        markup_pct: Decimal = Decimal("0"),
        has_pricing: bool = True,
    ) -> None:
        self.snapshot_id = snapshot_id or uuid.uuid4()
        self.prompt_price = prompt_price
        self.completion_price = completion_price
        self.pricing_unit = pricing_unit
        self.unit_usd_per_unit = unit_usd_per_unit
        self.cached_input_price = cached_input_price
        self.reasoning_price = reasoning_price
        self.cache_creation_price = cache_creation_price
        self.markup_pct = markup_pct
        self.has_pricing = has_pricing

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
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
                        str(self.cached_input_price)
                        if self.cached_input_price is not None
                        else None,
                        str(self.reasoning_price) if self.reasoning_price is not None else None,
                        str(self.cache_creation_price)
                        if self.cache_creation_price is not None
                        else None,
                        # gpt-realtime-relay-billing extended _fetch_latest_pricing to an 11-tuple.
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
def stream_capture() -> StreamCapture:
    return StreamCapture()


# DB-backed api_key fixture (mirrors pricing_units PU6/PU7) — signup → login → key.
@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "TieredBillingTest",
            "email": "tiered-test@example.io",
            "password": "tiered billing battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "tiered-test@example.io", "password": "tiered billing battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "tiered-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
