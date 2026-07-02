"""Suite-local fixtures for tiered-rate-cards tests (TASK.md §4).

Mirrors tests/tiered_token_billing/conftest.py (same 8-tuple `pricing_snapshots`
row shape) but extends `FakeSession` with a THIRD SQL branch answering the new
per-(tenant, model) rate-card override query:

    SELECT markup_pct FROM tenant_rate_card_entries WHERE tenant_id=:t AND model_id=:m

Byte-identical-fallback safety (TASK.md §0 Context): `tenant_rate_card_entries`
contains NEITHER the substring "tenants" NOR "pricing_snapshots", so this fake's
extra branch cannot shadow the two existing branches — the flat-markup fallback
query text stays untouched.

Before BUILD: `_fetch_markup_pct(session, tenant_id)` takes no `model_id` and
never issues a `tenant_rate_card_entries` query at all, so a configured
override is silently IGNORED by the recorder — every "override" test's cost
assertion mismatches the flat-markup result. That mismatch (not a harness bug)
is the RED reason for every override-billing test in this suite.

Unit tests use FakeSession + StreamCapture (no infra). The DB-backed tests use
real Postgres (GATEWAY_TEST_DATABASE_URL, run against an isolated
`gateway_test_gw_ratecard` DB per TASK.md §0) + Redis (localhost:6380 db 9) via
the shared root conftest fixtures — same as the sibling suites.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest


# ---------------------------------------------------------------------------
# Fake DB primitives (tiered-token-billing 8-tuple pricing row + a NEW
# tenant_rate_card_entries branch)
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


def _extract_param(params: Any, *keys: str) -> str | None:
    """Best-effort extraction of a bind param under any of `keys`.

    The resolver's exact parameter names are not pinned by the frozen §3
    CONTRACT (only the query SHAPE is: `WHERE tenant_id=:t AND model_id=:m`),
    so this fake tries several plausible spellings rather than guessing one —
    the resolver doesn't exist pre-build anyway (this branch is forward-looking
    for the GREEN suite, never exercised by today's `_fetch_markup_pct`).
    """
    if not isinstance(params, dict):
        return None
    for key in keys:
        if key in params:
            return str(params[key])
    return None


class FakeSession:
    """In-memory async session intercepting the markup/pricing queries.

    Returns an 8-value pricing row (tiered-token-billing shape):
      (id, prompt_price, completion_price, pricing_unit, unit_usd_per_unit,
       cached_input_price, reasoning_price, cache_creation_price)

    `rate_card_overrides`: {model_id: markup_pct} — the per-model override a
    (not-yet-built) resolver would read from `tenant_rate_card_entries`. A
    model_id absent from this dict must fall through to the flat `tenants`
    markup (empty FakeResult(None), never a synthesized zero-row).

    SQL routing is substring matching (mirrors every sibling FakeSession) —
    "tenant_rate_card_entries" is checked FIRST since it is the most specific
    of the three table names and shares no substring with the other two.
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
        rate_card_overrides: dict[str, Decimal] | None = None,
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
        self.rate_card_overrides = dict(rate_card_overrides or {})
        self.has_pricing = has_pricing
        # Observability seam: every SQL statement issued (text), so tests can
        # assert the byte-identical `tenants` fallback query is still issued
        # (TASK.md §2 "No entry falls back byte-identical" scenario) without
        # reaching into recorder internals.
        self.executed: list[str] = []

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        self.executed.append(sql)
        if "tenant_rate_card_entries" in sql:
            model_id = _extract_param(params, "model_id", "m", "model")
            override = self.rate_card_overrides.get(model_id) if model_id else None
            if override is None:
                return FakeResult(None)
            return FakeResult(FakeRow((str(override),)))
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


# DB-backed api_key fixture (mirrors tiered_token_billing/pricing_units) —
# signup -> login -> key. Tenant gets the default markup_pct=20 (the flat
# fallback every override test must differ from).
@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "TieredRateCardsTest",
            "email": "tiered-rate-cards@example.io",
            "password": "tiered rate cards battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "tiered-rate-cards@example.io",
                "password": "tiered rate cards battery",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "tiered-rate-cards-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
