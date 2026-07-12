"""Failing-first (red) suite for region-pricing (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario. RED before BUILD — they fail for the right reason:

  * recorder-level tests (unit, FakeSession): `gateway.usage.application.
    rate_card_resolver.resolve_region_multiplier` does not exist at all — every
    such test either ImportErrors/AttributeErrors immediately, or (once a stub
    exists) the recorder never calls it, so the region premium is silently
    absent and the cost comes out at the PRE-region figure — a Decimal
    mismatch, not a harness bug (every such test deliberately picks eu's 1.1x,
    which differs from the identity 1.0x a leaked no-op would produce).
  * catalog/recovery equality tests (DB): `tenant_region_multiplier_overrides`
    does not exist yet -> setup/PUT raises UndefinedTable / 404 before any
    price assertion runs.
  * admin-router tests (DB): PUT/GET/DELETE /admin/region-pricing are not
    mounted yet -> 404, not the contracted 200/204/403/422.
  * `test_invoice_line_zero_drift`: `resolve_region_multiplier` does not exist
    on `rate_card_resolver` yet -> `monkeypatch.setattr(..., raising=True)`
    raises AttributeError before the generation assertion runs.

Run only the pure-unit / no-infra subset:
  cd apps/gateway && uv run pytest tests/region_pricing/ -q --no-cov \
      -k "bills_seeded_premium or byte_identical or divides_out_region or no_extra_query" \
      -p no:cacheprovider

Full suite (needs Postgres + Redis):
  cd apps/gateway && uv run pytest tests/region_pricing/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.region_pricing.conftest import (
    FakeSession,
    FakeSessionFactory,
    StreamCapture,
    seed_region_model,
)

# ---------------------------------------------------------------------------
# Shared helpers (router tests)
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _issue_token(app: Any, *, tenant_id: uuid.UUID, role: Role) -> str:
    """Issue a JWT for an arbitrary role bound to the given tenant (mirrors
    tests/tiered_rate_cards/test_tiered_rate_cards.py's `_issue_token`)."""
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        email=f"{role.value}@region-pricing-rbac.local",
    )
    return token


# ---------------------------------------------------------------------------
# Scenario 1 — EU-region request bills the seeded 1.1x premium  (M1, M2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eu_region_bills_seeded_premium(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),
        region_by_model={"anthropic/eu-model": "eu"},
        region_overrides={},  # no override -> falls back to the seeded 1.1x
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="anthropic/eu-model",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 1.00},
        status=200,
    )
    expected = Decimal("1.00") * (Decimal("1") + Decimal("20") / Decimal("100")) * Decimal("1.1")
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"EU region premium not applied: got {evt['cost_usd']!r}, expected {expected}"
    )
    # resolve_region_multiplier's unique fingerprint query fires exactly ONCE per request.
    region_queries = [s for s in session.executed if "tenant_region_multiplier_overrides" in s]
    assert len(region_queries) == 1, (
        f"region_multiplier must resolve exactly once; issued: {session.executed}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — US/global-region request stays byte-identical  (M1, M2, regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us", "global"])
async def test_us_global_region_byte_identical(
    region: str, snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    """NOT expressible as RED: us/global both map to the DECIDED 1.0x (identity)
    multiplier, so this scenario's expected cost is the SAME with or without the
    (not-yet-built) resolver in the loop — it is expected GREEN before AND after
    BUILD (mirrors tests/tiered_rate_cards's `test_no_entry_falls_back_byte_
    identical`). It stays in the suite as the regression guard BUILD must not
    break; RED coverage for the region factor itself is carried by the sibling
    `test_eu_region_bills_seeded_premium`, which fails loudly pre-build.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),
        region_by_model={"openai/non-eu-model": region},
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/non-eu-model",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "cost": 1.00},
        status=200,
    )
    expected = Decimal("1.00") * (Decimal("1") + Decimal("20") / Decimal("100"))
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"region={region}: must stay byte-identical to pre-task cost: "
        f"got {evt['cost_usd']!r}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — Tenant overrides the EU multiplier  (M1, M7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tenant_region_override_wins(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    other_api_key: dict[str, str],
) -> None:
    """RED reason: PUT /admin/region-pricing/{region} is not mounted yet -> 404,
    not the contracted 200."""
    model_id = "anthropic/eu-override-test"
    await seed_region_model(db_session, model_id, region="eu")

    put_resp = await client.put(
        "/admin/region-pricing/eu",
        json={"multiplier": 1.05},
        headers=_bearer(api_key["jwt"]),
    )
    assert put_resp.status_code == 200, put_resp.text
    assert Decimal(put_resp.json()["multiplier"]) == Decimal("1.05")

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        await recorder.record(
            tenant_id=uuid.UUID(other_api_key["tenant_id"]),
            key_id=uuid.UUID(other_api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row_override = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": model_id},
        )
    ).fetchone()
    assert row_override is not None
    assert Decimal(str(row_override[0])) == Decimal("1.00") * Decimal("1.20") * Decimal("1.05"), (
        "the overriding tenant must bill at 1.05x, not the 1.10x seed"
    )

    row_other = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": other_api_key["tenant_id"], "m": model_id},
        )
    ).fetchone()
    assert row_other is not None
    assert Decimal(str(row_other[0])) == Decimal("1.00") * Decimal("1.20") * Decimal("1.10"), (
        "a DIFFERENT tenant with no override must still resolve the 1.10x seed, unchanged"
    )


# ---------------------------------------------------------------------------
# Scenario 4 — Catalog display matches billed price for an EU model  (M5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_display_matches_billed_price(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: `catalog/infrastructure/repository.py`'s bulk multiplier does not
    yet fold in the region factor -> catalog price stays at the pre-region figure,
    mismatching the expected 1.10x-inflated price."""
    model_id = "anthropic/eu-catalog-eq"
    prompt_price = Decimal("0.00001")
    completion_price = Decimal("0.00003")
    await seed_region_model(
        db_session,
        model_id,
        region="eu",
        prompt_price=prompt_price,
        completion_price=completion_price,
    )

    resp = await client.get("/v1/models", headers=_bearer(api_key["jwt"]))
    assert resp.status_code == 200, resp.text
    catalog_model = next(m for m in resp.json()["data"] if m["id"] == model_id)
    expected_catalog_prompt = float(prompt_price) * 1.20 * 1.10
    assert catalog_model["prompt_per_token"] == pytest.approx(expected_catalog_prompt), (
        f"catalog price must reflect the eu region premium (x1.10): {catalog_model}"
    )

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": model_id},
        )
    ).fetchone()
    assert row is not None, "no usage row flushed"
    billed_cost = Decimal(str(row[0]))
    expected_billed = Decimal("1.00") * Decimal("1.20") * Decimal("1.10")
    assert billed_cost == expected_billed, (
        f"billing must use the same 1.10x region premium: {billed_cost}"
    )

    # No third-site drift: catalog multiplier == billing multiplier.
    catalog_multiplier = catalog_model["prompt_per_token"] / float(prompt_price)
    billing_multiplier = float(billed_cost) / 1.00
    assert catalog_multiplier == pytest.approx(billing_multiplier), (
        "catalog price and billed cost must resolve the SAME multiplier (no drift)"
    )


# ---------------------------------------------------------------------------
# Scenario 5 — Invoice line for an EU tenant carries the premium, zero drift  (M6)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoice_line_zero_drift(
    app: Any, db_session: AsyncSession, api_key: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED reason: `rate_card_resolver.resolve_region_multiplier` does not exist yet,
    so `monkeypatch.setattr(..., raising=True)` raises AttributeError before the
    generation assertion runs — the honest missing-implementation red."""
    from gateway.billing.application.invoice_generator import InvoiceGenerator  # type: ignore[import]
    from gateway.usage.application import rate_card_resolver

    tenant_id = api_key["tenant_id"]
    key_id = api_key["key_id"]
    period_start = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    # Region-inflated cost_usd, as if a fresh record() call had already applied
    # markup x region (1.20 x 1.10) — invoice generation must sum this VERBATIM.
    region_inflated_cost = Decimal("1.00") * Decimal("1.20") * Decimal("1.10")

    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, status, cost_usd, raw, created_at)"
            " VALUES (:id, :t, :k, :m, 200, :cost, '{}', :ts)"
        ),
        {
            "id": str(uuid.uuid4()),
            "t": tenant_id,
            "k": key_id,
            "m": "anthropic/eu-invoice-test",
            "cost": str(region_inflated_cost),
            "ts": datetime.datetime(2026, 7, 15, tzinfo=datetime.UTC).replace(tzinfo=None),
        },
    )
    await db_session.commit()

    # invoice_generator.py must NEVER call resolve_region_multiplier — turn any such
    # call into a loud failure instead of a silent pass (M6 zero-touch proof).
    async def _boom(*_a: Any, **_k: Any) -> Decimal:
        raise AssertionError("invoice_generator must NEVER call resolve_region_multiplier")

    monkeypatch.setattr(rate_card_resolver, "resolve_region_multiplier", _boom)

    generator = InvoiceGenerator(session_factory=app.state.sessionmaker)
    invoice_id = await generator.generate_for_tenant(uuid.UUID(tenant_id), period_start)
    assert invoice_id is not None

    row = (
        await db_session.execute(
            text("SELECT total_usd FROM invoices WHERE id = :id"), {"id": str(invoice_id)}
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == region_inflated_cost.quantize(Decimal("0.01")), (
        f"invoice total must sum the ALREADY region-inflated cost_usd verbatim: {row}"
    )


# ---------------------------------------------------------------------------
# Scenario 6 — Disconnect-estimate on an EU model divides out BOTH factors  (M3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_estimate_divides_out_region(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    """NOT expressible as RED: pre-build, cost_usd is never region-inflated in the
    first place (no region multiplication exists yet), so the EXISTING markup-only
    back-derivation already recovers the true upstream estimate for THIS scenario —
    expected GREEN before AND after BUILD. It stays in the suite as the regression
    guard for M3 (the drift trap): once BUILD region-inflates cost_usd, this same
    assertion is what catches a back-derivation that forgets to divide region back
    out (a get-it-wrong-once change would fail this test loudly).
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),
        region_by_model={"anthropic/eu-disconnect": "eu"},
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="anthropic/eu-disconnect",
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        status=200,
        usage_source="client_disconnect",
        disconnect_estimate=True,
    )
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == Decimal("0"), "user-billed amount must stay zeroed"
    assert evt["cost_basis"] == "provider"

    # The TRUE upstream estimate — un-inflated by either markup or region.
    expected_provider_cost = Decimal("100") * Decimal("0.00001") + Decimal("50") * Decimal(
        "0.00003"
    )
    got_provider_cost = Decimal(evt["provider_cost"])
    assert got_provider_cost == expected_provider_cost, (
        f"disconnect back-derivation must divide out BOTH markup AND region: "
        f"got {got_provider_cost}, expected {expected_provider_cost}"
    )


# ---------------------------------------------------------------------------
# Scenario 7 — OpenRouter cost-recovery on an EU model matches record()'s rate  (M4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_recovery_matches_original_rate(
    app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: `tenant_region_multiplier_overrides` schema aside, `cost_recovery.py`
    never multiplies in a region factor today -> the recovered total lands at
    2.00 x 1.20 = 2.40, not the contracted 2.00 x 1.20 x 1.10 = 2.64."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.proxy.infrastructure.openrouter_upstream import GenerationCost
    from gateway.usage.application.cost_recovery import OpenRouterCostRecoveryService
    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    class _FakeUpstream:
        def __init__(self, cost: GenerationCost) -> None:
            self._cost = cost

        async def get_generation(self, generation_id: str) -> GenerationCost | None:
            return self._cost

    class _FakeClock:
        def __init__(self) -> None:
            self.t = 0.0

        def now(self) -> float:
            return self.t

        async def sleep(self, seconds: float) -> None:
            self.t += seconds

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/eu-recover-test"
        gid = "gen-region-recover-1"
        tenant_id = api_key["tenant_id"]

        await seed_region_model(db_session, model_id, region="eu")

        await db_session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, status, raw, cost_usd,"
                "  provider_generation_id, usage_source)"
                " VALUES (:id, :t, :k, :m, 200, '{}', 0.00, :gid, 'client_disconnect')"
            ),
            {
                "id": str(uuid.uuid4()),
                "t": tenant_id,
                "k": api_key["key_id"],
                "m": model_id,
                "gid": gid,
            },
        )
        await db_session.commit()

        clock = _FakeClock()
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        svc = OpenRouterCostRecoveryService(
            upstream=_FakeUpstream(  # type: ignore[arg-type]
                GenerationCost(
                    total_cost=Decimal("2.00"),
                    upstream_inference_cost=Decimal("2.00"),
                    native_tokens_prompt=0,
                    native_tokens_completion=0,
                    native_tokens_cached=0,
                )
            ),
            recorder=recorder,
            session_factory=app.state.sessionmaker,
            credential_resolver=None,
            sleep=clock.sleep,
            monotonic=clock.now,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(tenant_id),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            provider_generation_id=gid,
        )
        assert outcome.status == "recovered", outcome

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        total_row = (
            await db_session.execute(
                text(
                    "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records"
                    " WHERE tenant_id = :t AND provider_generation_id = :g"
                ),
                {"t": tenant_id, "g": gid},
            )
        ).fetchone()
        assert total_row is not None
        assert Decimal(str(total_row[0])) == Decimal("2.00") * Decimal("1.20") * Decimal("1.10"), (
            f"recovery must apply the SAME region multiplier a fresh record() would: {total_row}"
        )
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# Scenario 8 — Invalid multiplier value is rejected  (R1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invalid_multiplier_422(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: /admin/region-pricing is not mounted yet -> 404, not the
    contracted 422."""
    owner_token = api_key["jwt"]

    for bad_multiplier in (-1, "abc"):
        resp = await client.put(
            "/admin/region-pricing/eu",
            json={"multiplier": bad_multiplier},
            headers=_bearer(owner_token),
        )
        assert resp.status_code == 422, (
            f"multiplier={bad_multiplier!r}: expected 422 got {resp.status_code}: {resp.text}"
        )
        assert "code" in resp.json(), resp.text

    row = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM tenant_region_multiplier_overrides"
                " WHERE tenant_id = :t AND region = 'eu'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchone()
    assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------------
# Scenario 9 — Non-owner cannot manage region pricing  (R2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_owner_403(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: /admin/region-pricing is not mounted yet -> 404, not the
    contracted 403 ERR_AUTH_FORBIDDEN."""
    tenant_id = uuid.UUID(api_key["tenant_id"])
    for role in (Role.ADMIN, Role.MEMBER):
        token = _issue_token(app, tenant_id=tenant_id, role=role)
        resp = await client.put(
            "/admin/region-pricing/eu", json={"multiplier": 1.05}, headers=_bearer(token)
        )
        assert resp.status_code == 403, (
            f"role={role.value}: expected 403 got {resp.status_code}: {resp.text}"
        )
        assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text

        row = (
            await db_session.execute(
                text(
                    "SELECT count(*) FROM tenant_region_multiplier_overrides"
                    " WHERE tenant_id = :t AND region = 'eu'"
                ),
                {"t": api_key["tenant_id"]},
            )
        ).fetchone()
        assert row is not None and int(row[0]) == 0


# ---------------------------------------------------------------------------
# Scenario 10 — Duplicate region override PUT is an idempotent upsert  (R3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_put_idempotent_upsert(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: PUT /admin/region-pricing/{region} is not mounted yet -> 404 on
    the FIRST call, not the contracted 200."""
    owner_token = api_key["jwt"]

    first = await client.put(
        "/admin/region-pricing/eu", json={"multiplier": 1.05}, headers=_bearer(owner_token)
    )
    assert first.status_code == 200, first.text

    second = await client.put(
        "/admin/region-pricing/eu", json={"multiplier": 1.08}, headers=_bearer(owner_token)
    )
    assert second.status_code == 200, second.text
    assert Decimal(second.json()["multiplier"]) == Decimal("1.08")

    rows = (
        await db_session.execute(
            text(
                "SELECT multiplier FROM tenant_region_multiplier_overrides"
                " WHERE tenant_id = :t AND region = 'eu'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1, f"exactly one entry must remain for (tenant, eu); got {rows}"
    assert Decimal(str(rows[0][0])) == Decimal("1.08")


# ---------------------------------------------------------------------------
# Scenario 11 — Deleting an absent region override is a no-op success  (R4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_absent_override_204(
    client: httpx.AsyncClient, api_key: dict[str, str]
) -> None:
    """RED reason: DELETE /admin/region-pricing/{region} is not mounted yet -> 404,
    not the contracted 204."""
    resp = await client.delete("/admin/region-pricing/us", headers=_bearer(api_key["jwt"]))
    assert resp.status_code == 204, f"expected 204 got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Scenario 12 — Unrecognized/NULL region resolves safe, never blocks  (edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unrecognized_region_defaults_safe(
    app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: the recorder does not yet apply/skip a region multiplier at all,
    so the pre-region cost (1.00 x 1.20 = 1.20) happens to already equal the
    post-build "safe default" figure for THIS scenario — so this test alone
    would falsely read GREEN pre-build. It is included anyway as the REQUIRED
    regression guard for the edge case (TASK.md §4 test_plan); its RED coverage
    is carried by every OTHER eu-region test in this suite, which fails loudly
    pre-build. Post-build this test's own assertions (200, no refusal, 1.0x) are
    the durable regression pin.
    """
    model_id = "anthropic/typo-region-model"
    await seed_region_model(db_session, model_id, region="eu-typo-not-real")

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        outcome = await recorder.record_with_outcome(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        assert outcome is not None, "an unrecognized region must never block/refuse billing"
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": model_id},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("1.00") * Decimal("1.20"), (
        "an unrecognized region must resolve region_multiplier=1.0, never a 4xx/refusal"
    )


# ---------------------------------------------------------------------------
# Scenario 13 — Cached hit unaffected, zero extra region query  (edge case)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_hit_unaffected_no_extra_query(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    """NOT expressible as RED: pre-build, NOTHING is fetched/applied on the
    cached=True path (region-pricing doesn't exist yet), so `session.executed == []`
    already holds today — expected GREEN before AND after BUILD. It stays in the
    suite as the regression guard for the edge case (TASK.md §4 test_plan): once
    BUILD wires region_multiplier resolution alongside markup_pct, this is what
    catches a change that accidentally fetches it OUTSIDE the `if not cached:` guard.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("20"),
        region_by_model={"anthropic/eu-cached": "eu"},
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="anthropic/eu-cached",
        usage={"prompt_tokens": 100, "completion_tokens": 50},
        status=200,
        cached=True,
    )
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == Decimal("0")
    assert session.executed == [], (
        f"cached=True must skip EVERY DB round trip (pricing, markup, region): "
        f"issued {session.executed}"
    )


# ---------------------------------------------------------------------------
# Scenario 14 — Concurrent PUTs to the same (tenant, region) resolve without a race
# (edge case, mirrors RC7)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_put_no_race(
    client: httpx.AsyncClient, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: /admin/region-pricing is not mounted yet -> both PUTs 404."""
    owner_token = api_key["jwt"]

    results = await asyncio.gather(
        client.put(
            "/admin/region-pricing/eu", json={"multiplier": 1.11}, headers=_bearer(owner_token)
        ),
        client.put(
            "/admin/region-pricing/eu", json={"multiplier": 1.22}, headers=_bearer(owner_token)
        ),
    )
    for resp in results:
        assert resp.status_code == 200, resp.text

    rows = (
        await db_session.execute(
            text(
                "SELECT multiplier FROM tenant_region_multiplier_overrides"
                " WHERE tenant_id = :t AND region = 'eu'"
            ),
            {"t": api_key["tenant_id"]},
        )
    ).fetchall()
    assert len(rows) == 1, f"exactly one row must exist for (tenant, eu); got {rows}"
    assert Decimal(str(rows[0][0])) in (Decimal("1.1100"), Decimal("1.2200")), rows
