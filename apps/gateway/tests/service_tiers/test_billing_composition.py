"""RED-first suite for service-tiers M11/M12 — the tier-multiplier resolver, its
composition into billed cost_usd (region_multiplier x tier_multiplier), and the
cost-recovery/catalog zero-drift proofs (TASK.md §4, contract FROZEN @ v1).

RED reason before BUILD: `resolve_tier_multiplier` raises NotImplementedError;
recorder.py never composes a tier factor into cost_usd (Decimal mismatch, not a
harness bug — every test below deliberately picks a tier multiplier that differs
from the identity 1.0x a leaked no-op would produce).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.service_tiers.conftest import FakeSession, bearer


# ---------------------------------------------------------------------------
# M11 — resolve_tier_multiplier: pure-unit (FakeSession) resolution rules
# ---------------------------------------------------------------------------


async def test_standard_tier_is_always_the_identity_multiplier() -> None:
    from gateway.usage.application.rate_card_resolver import resolve_tier_multiplier

    # Even WITH a priority override present, "standard" must never be inflated —
    # the FakeSession's override branch is never consulted for a non-priority tier.
    session = FakeSession(tier_markup_override=Decimal("40"))
    result = await resolve_tier_multiplier(session, uuid.uuid4(), "openai/gpt-4o", "standard")
    assert result == Decimal("1")


async def test_priority_tier_with_no_override_resolves_seed_25pct() -> None:
    from gateway.usage.application.rate_card_resolver import resolve_tier_multiplier

    session = FakeSession(tier_markup_override=None)
    result = await resolve_tier_multiplier(session, uuid.uuid4(), "openai/gpt-4o", "priority")
    assert result == Decimal("1.25")


async def test_priority_tier_with_override_resolves_override_not_seed() -> None:
    from gateway.usage.application.rate_card_resolver import resolve_tier_multiplier

    session = FakeSession(tier_markup_override=Decimal("15"))
    result = await resolve_tier_multiplier(session, uuid.uuid4(), "openai/gpt-4o", "priority")
    assert result == Decimal("1.15")


# ---------------------------------------------------------------------------
# M12 — recorder composes cost_usd = base * markup * region_multiplier * tier_multiplier
# ---------------------------------------------------------------------------


async def test_recorder_composes_tier_multiplier_for_priority_served(
    app: Any, db_session: AsyncSession, api_key: dict[str, str], priced_model: str
) -> None:
    """A priority-served request must bill markup x region(1.0, no override) x tier(1.25
    seed) — no third-site drift, mirrors region-pricing's own composition proof."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    redis_client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=priced_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
            tier_served="priority",
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text(
                "SELECT cost_usd, tier_served, tier_capacity_degraded FROM usage_records"
                " WHERE tenant_id = :t AND model_id = :m"
            ),
            {"t": api_key["tenant_id"], "m": priced_model},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("1.00") * Decimal("1.20") * Decimal("1.25"), (
        "priority-served cost must compose markup x tier_multiplier (region is identity "
        "here — priced_model has no region override/seed)"
    )
    assert row[1] == "priority"
    assert row[2] is False


async def test_recorder_standard_served_stays_byte_identical(
    app: Any, db_session: AsyncSession, api_key: dict[str, str], priced_model: str
) -> None:
    """NOT expressible as RED: default tier_served="standard" resolves the identity
    1.0x multiplier with or without the (not-yet-built) resolver in the loop — expected
    GREEN before AND after BUILD (mirrors region-pricing's own byte-identical regression
    guard). Stays in the suite as the regression BUILD must not break."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    redis_client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=priced_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text(
                "SELECT cost_usd, tier_served FROM usage_records"
                " WHERE tenant_id = :t AND model_id = :m"
            ),
            {"t": api_key["tenant_id"], "m": priced_model},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("1.00") * Decimal("1.20")
    assert row[1] == "standard"


# ---------------------------------------------------------------------------
# Scenario — Tenant overrides the priority markup (M11, M13)
# ---------------------------------------------------------------------------


async def test_tenant_priority_markup_override_wins_others_unaffected(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    api_key: dict[str, str],
    other_api_key: dict[str, str],
    priced_model: str,
) -> None:
    put_resp = await client.put(
        "/admin/service-tiers/priority-markup",
        json={"markup_pct": 15},
        headers=bearer(api_key["jwt"]),
    )
    assert put_resp.status_code == 200, put_resp.text

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    redis_client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=priced_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
            tier_served="priority",
        )
        await recorder.record(
            tenant_id=uuid.UUID(other_api_key["tenant_id"]),
            key_id=uuid.UUID(other_api_key["key_id"]),
            model=priced_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
            tier_served="priority",
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    overriding = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": priced_model},
        )
    ).fetchone()
    assert overriding is not None
    assert Decimal(str(overriding[0])) == Decimal("1.00") * Decimal("1.20") * Decimal("1.15"), (
        "overriding tenant must bill at 1.15x, not the 1.25x seed"
    )

    other = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": other_api_key["tenant_id"], "m": priced_model},
        )
    ).fetchone()
    assert other is not None
    assert Decimal(str(other[0])) == Decimal("1.00") * Decimal("1.20") * Decimal("1.25"), (
        "a DIFFERENT tenant with no override must still resolve the 1.25x seed, unchanged"
    )


# ---------------------------------------------------------------------------
# Scenario — catalog price display matches billed price for a priority key (M12)
#
# NOTE (Build judgment call, TASK.md §5 open question): the frozen §2 scenario reads
# "that tenant's priority-tier key calls the admin catalog listing" — but /v1/models
# (catalog_router.list_models) authenticates via JWT session identity only
# (get_current_identity), which carries tenant_id, NEVER a per-key tier (tier is a
# per-API-KEY override, resolved only at admission — §1 M1). There is no existing HTTP
# surface where an API KEY calls the catalog listing. This test therefore proves the
# zero-drift composition at the USE-CASE level directly (ListModelsForTenantUseCase.
# execute(tier=...), the seam this task deliberately added but did not wire to a route
# — see TASK.md §5 Build notes) against a real record() call for the same (tenant,
# model, tier) — the actual invariant the scenario cares about (no third-site drift),
# without inventing an HTTP flow the frozen contract's route table does not name.
# ---------------------------------------------------------------------------


async def test_catalog_price_matches_billed_price_zero_drift(
    app: Any, db_session: AsyncSession, api_key: dict[str, str], active_model: str
) -> None:
    from gateway.catalog.application.use_cases import ListModelsForTenantUseCase
    from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

    repo = SqlAlchemyCatalogRepository(db_session)
    use_case = ListModelsForTenantUseCase(repo)

    # Seed a pricing snapshot so both catalog AND billing have a real, non-zero rate
    # (active_model fixture seeds no pricing_snapshots row).
    prompt_price = Decimal("0.00001")
    completion_price = Decimal("0.00003")
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, :p, :c, now()) ON CONFLICT (id) DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "m": active_model, "p": prompt_price, "c": completion_price},
    )
    await db_session.commit()

    models = await use_case.execute(tenant_id=uuid.UUID(api_key["tenant_id"]), tier="priority")
    catalog_model = next(m for m in models if m.id == active_model)
    expected_catalog_prompt = prompt_price * Decimal("1.20") * Decimal("1.25")
    assert catalog_model.prompt_per_token == pytest.approx(float(expected_catalog_prompt)), (
        f"catalog price must reflect the 1.25x priority seed: {catalog_model}"
    )

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    from gateway.usage.application.flusher import UsageLedgerFlusher
    from gateway.usage.application.recorder import RecordingUsageRecorder

    redis_client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=active_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "cost": 1.00},
            status=200,
            tier_served="priority",
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
    finally:
        await redis_client.aclose()

    row = (
        await db_session.execute(
            text("SELECT cost_usd FROM usage_records WHERE tenant_id = :t AND model_id = :m"),
            {"t": api_key["tenant_id"], "m": active_model},
        )
    ).fetchone()
    assert row is not None
    billed_cost = Decimal(str(row[0]))
    assert billed_cost == Decimal("1.00") * Decimal("1.20") * Decimal("1.25")

    # No third-site drift: catalog multiplier == billing multiplier.
    catalog_multiplier = catalog_model.prompt_per_token / float(prompt_price)
    billing_multiplier = float(billed_cost) / 1.00
    assert catalog_multiplier == pytest.approx(billing_multiplier), (
        "catalog price and billed cost must resolve the SAME multiplier (no drift)"
    )


# ---------------------------------------------------------------------------
# Scenario — cost recovery on a priority-served request matches the original rate (M12)
# ---------------------------------------------------------------------------


async def test_cost_recovery_matches_priority_served_rate(
    app: Any, db_session: AsyncSession, api_key: dict[str, str]
) -> None:
    """RED reason: cost_recovery.py never multiplies in a tier factor today -> the
    recovered total lands at 2.00 x 1.20 = 2.40, not the contracted
    2.00 x 1.20 x 1.25 = 3.00 — keyed by the STORED tier_served on the anchor row."""
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

    redis_client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/priority-recover-test"
        gid = "gen-tier-recover-1"
        tenant_id = api_key["tenant_id"]
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, 128000, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "priority-recover-test"},
        )
        await db_session.execute(
            text(
                "INSERT INTO usage_records"
                " (id, tenant_id, key_id, model_id, status, raw, cost_usd,"
                "  provider_generation_id, usage_source, tier_served)"
                " VALUES (:id, :t, :k, :m, 200, '{}', 0.00, :gid, 'client_disconnect', 'priority')"
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
    finally:
        await redis_client.aclose()

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
    assert Decimal(str(total_row[0])) == Decimal("2.00") * Decimal("1.20") * Decimal("1.25"), (
        f"recovery must apply the SAME tier multiplier a fresh record() would: {total_row}"
    )
