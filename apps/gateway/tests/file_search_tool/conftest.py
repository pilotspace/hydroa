"""Suite-local fixtures for file-search-tool tests.

The per_query metering tests use a fake DB session (FakeSession) + a fake Redis
stream sink (StreamCapture) so they need no live infrastructure — the exact
harness shape the pricing_units suite froze (tests/pricing_units/conftest.py),
copied here so this suite is self-contained.

The retrieval-repo test uses the shared `db_session` fixture from the root
conftest (real Postgres on :5433, gateway_test_file_search_tool) — the pgvector
`vector` column requires the extension, which the root `app` fixture provisions.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake DB primitives (mirror tests/pricing_units/conftest.py — frozen shape)
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
    """In-memory async session intercepting _fetch_latest_pricing / _fetch_markup_pct.

    Returns the same 11-tuple pricing row shape the live recorder unpacks today
    (id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit, then 6
    NULL tier/audio prices). The caller sets pricing_unit + unit_usd_per_unit.
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
                        str(self.unit_usd_per_unit)
                        if self.unit_usd_per_unit is not None
                        else None,
                        None,  # cached_input_usd_per_token
                        None,  # reasoning_usd_per_token
                        None,  # cache_creation_usd_per_token
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
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    def __call__(self) -> FakeSession:
        return self._session


class StreamCapture:
    """Async Redis fake capturing xadd() fields so tests can assert event fields."""

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


@pytest.fixture
def snapshot_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def tenant_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def key_id() -> uuid.UUID:
    return uuid.uuid4()
