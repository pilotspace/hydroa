"""Suite-local fixtures for region-pricing tests (TASK.md §4).

Mirrors tests/tiered_rate_cards/conftest.py's FakeSession idiom (same 11-tuple
`pricing_snapshots` row shape recorder.py's `_fetch_latest_pricing` actually
returns today) but adds TWO new SQL branches answering the (not-yet-built)
`resolve_region_multiplier` resolver's two queries:

    1. SELECT multiplier FROM tenant_region_multiplier_overrides
       WHERE tenant_id=:t AND region=(SELECT region FROM models WHERE id=:m)
    2. SELECT region FROM models WHERE id=:m   (fallback, only reached on a
       step-1 miss — used to key the DECIDED seed lookup)

Before BUILD: `rate_card_resolver.resolve_region_multiplier` does not exist at
all, and `recorder.py` never fetches/applies a region multiplier — so every
region-premium assertion in this suite comes out at the PRE-region cost
(missing implementation, not a harness bug). Every such test deliberately
picks a region (eu) whose seeded multiplier (1.1x) differs from the identity
multiplier (1.0x) a leaked no-op resolution would produce, so a silently
absent region factor is caught, never a false green.

Unit tests use FakeSession + StreamCapture (no infra). The DB-backed tests use
real Postgres (GATEWAY_TEST_DATABASE_URL) + Redis (localhost:6380 db 9) via
the shared root conftest fixtures — same as the sibling suites.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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


def _extract_param(params: Any, *keys: str) -> str | None:
    """Best-effort extraction of a bind param under any of `keys` (the
    resolver's exact parameter names are not pinned by the frozen §3
    CONTRACT — only the query SHAPE is)."""
    if not isinstance(params, dict):
        return None
    for key in keys:
        if key in params:
            return str(params[key])
    return None


class FakeSession:
    """In-memory async session intercepting the pricing/markup/region queries.

    region_by_model: {model_id: region} — the (not-yet-built) resolver's
      fallback `SELECT region FROM models WHERE id=:m` answer. A model_id
      absent from this dict resolves to `default_region` (None -> genuinely
      absent model, mirroring an unrecognized/NULL region).
    region_overrides: {region: multiplier} — the per-region override THIS
      fake's single tenant holds (an empty dict means "no overrides at all",
      every region falls through to the DECIDED seed).
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
        region_by_model: dict[str, str] | None = None,
        default_region: str | None = "global",
        region_overrides: dict[str, Decimal] | None = None,
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
        self.region_by_model = dict(region_by_model or {})
        self.default_region = default_region
        self.region_overrides = dict(region_overrides or {})
        self.has_pricing = has_pricing
        # Observability seam: every SQL statement issued (text), so tests can
        # assert the resolver was invoked exactly once (its override query is
        # the unique, always-first fingerprint) and that NO region query fires
        # on the cached=True path.
        self.executed: list[str] = []

    def _region_for(self, model_id: str | None) -> str | None:
        if model_id is None:
            return self.default_region
        return self.region_by_model.get(model_id, self.default_region)

    async def execute(self, stmt: Any, params: Any = None) -> FakeResult:
        sql = str(stmt)
        self.executed.append(sql)
        if "tenant_region_multiplier_overrides" in sql:
            model_id = _extract_param(params, "m", "model_id", "model")
            region = self._region_for(model_id)
            override = self.region_overrides.get(region) if region else None
            if override is None:
                return FakeResult(None)
            return FakeResult(FakeRow((str(override),)))
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
                        # gpt-realtime dual-stream audio prices (row[8:11]) — None for
                        # every model exercised here; only the row SHAPE must match.
                        None,
                        None,
                        None,
                    )
                )
            )
        if "models" in sql:
            # The resolver's region-fallback query: SELECT region FROM models WHERE id=:m
            model_id = _extract_param(params, "m", "model_id", "model")
            region = self._region_for(model_id)
            if region is None:
                return FakeResult(None)
            return FakeResult(FakeRow((region,)))
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


# DB-backed api_key fixture (mirrors tiered_rate_cards) — signup -> login -> key.
# Tenant gets the default markup_pct=20 (the flat fallback every test can rely on).
@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RegionPricingTest",
            "email": "region-pricing@example.io",
            "password": "region pricing battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "region-pricing@example.io", "password": "region pricing battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "region-pricing-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


# A second, independent tenant — for the "other tenants unaffected by one
# tenant's override" scenario (M7 / scenario 3).
@pytest.fixture
async def other_api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RegionPricingOtherTest",
            "email": "region-pricing-other@example.io",
            "password": "region pricing other battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "region-pricing-other@example.io",
                "password": "region pricing other battery",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "region-pricing-other-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


async def seed_region_model(
    db_session: AsyncSession,
    model_id: str,
    *,
    region: str = "global",
    prompt_price: Decimal = Decimal("0.00001"),
    completion_price: Decimal = Decimal("0.00003"),
) -> None:
    """Insert a model (with the given `region`) + a per-token pricing snapshot.

    `region` is written via a raw literal (not `normalize_region`), so a test
    can seed a deliberately unrecognized token to exercise the fail-open edge
    case — the `models.region` column itself has no CHECK/enum constraint
    (TASK.md §1 assumption #4 forward-cite).
    """
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, region)"
            " VALUES (:i, :n, NULL, true, :r)"
            " ON CONFLICT (id) DO UPDATE SET region = :r"
        ),
        {"i": model_id, "n": f"Region-pricing test model ({region})", "r": region},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, :pp, :cp, now())"
        ),
        {
            "id": str(uuid.uuid4()),
            "m": model_id,
            "pp": str(prompt_price),
            "cp": str(completion_price),
        },
    )
    await db_session.commit()
