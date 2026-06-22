"""Red suite for openrouter-cost-recovery (v30 t6.2b) — TASK.md §4.

The out-of-band recovery CORE: given a flushed client-disconnect row carrying a
provider_generation_id, fetch OpenRouter's authoritative cost and append ONE signed
delta correction row so total billed reaches total_cost*(1+markup). Idempotent at
the DB via a deterministic uuid5 row id honoured by the flusher.

Inline wiring (t6.2c) + the periodic sweep (t6.3) both call recover().
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from gateway.proxy.infrastructure.openrouter_upstream import GenerationCost
from gateway.usage.application.cost_recovery import (
    OpenRouterCostRecoveryService,
    RecoveryOutcome,
)
from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


def _cost(total: str) -> GenerationCost:
    return GenerationCost(
        total_cost=Decimal(total),
        upstream_inference_cost=Decimal(total),
        native_tokens_prompt=0,
        native_tokens_completion=0,
        native_tokens_cached=0,
    )


class FakeGenerationUpstream:
    """get_generation returns a scripted sequence; the last value repeats."""

    def __init__(self, results: list[GenerationCost | None]) -> None:
        self._results = list(results)
        self.calls = 0

    async def get_generation(self, generation_id: str) -> GenerationCost | None:
        idx = min(self.calls, len(self._results) - 1)
        self.calls += 1
        return self._results[idx]


class FakeClock:
    """Deterministic monotonic clock advanced by the injected sleep — no real waits."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


def _service(
    *,
    app: Any,
    upstream: FakeGenerationUpstream,
    redis_client: Any,
    clock: FakeClock,
    ready_deadline_s: float = 5.0,
    anchor_wait_s: float = 5.0,
) -> OpenRouterCostRecoveryService:
    recorder = RecordingUsageRecorder(redis=redis_client, session_factory=app.state.sessionmaker)
    return OpenRouterCostRecoveryService(
        upstream=upstream,  # type: ignore[arg-type]
        recorder=recorder,
        session_factory=app.state.sessionmaker,
        credential_resolver=None,
        ready_deadline_s=ready_deadline_s,
        ready_poll_interval_s=0.5,
        anchor_wait_s=anchor_wait_s,
        anchor_poll_interval_s=0.5,
        sleep=clock.sleep,
        monotonic=clock.now,
    )


async def _seed_anchor(
    db_session: Any,
    *,
    tenant_id: str,
    key_id: str,
    gid: str,
    cost_usd: str,
    model_id: str = "openrouter/recover-test",
) -> None:
    """Insert a flushed client-disconnect anchor row directly into the ledger."""
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, status, raw, cost_usd,"
            "  provider_generation_id, usage_source)"
            " VALUES (:id, :t, :k, :m, 200, '{}', :c, :gid, 'client_disconnect')"
        ),
        {
            "id": str(uuid.uuid4()),
            "t": tenant_id,
            "k": key_id,
            "m": model_id,
            "c": cost_usd,
            "gid": gid,
        },
    )
    await db_session.commit()


async def _set_markup(db_session: Any, tenant_id: str, pct: str) -> None:
    await db_session.execute(
        text("UPDATE tenants SET markup_pct = :p WHERE id = :t"),
        {"p": pct, "t": tenant_id},
    )
    await db_session.commit()


async def _recovered_rows(db_session: Any, tenant_id: str, gid: str) -> list[Any]:
    return (
        await db_session.execute(
            text(
                "SELECT cost_usd, provider_cost, cost_basis FROM usage_records"
                " WHERE tenant_id = :t AND provider_generation_id = :g"
                " AND usage_source = 'openrouter_recovered'"
            ),
            {"t": tenant_id, "g": gid},
        )
    ).fetchall()


async def _sum_cost(db_session: Any, tenant_id: str, gid: str) -> Decimal:
    row = (
        await db_session.execute(
            text(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records"
                " WHERE tenant_id = :t AND provider_generation_id = :g"
            ),
            {"t": tenant_id, "g": gid},
        )
    ).fetchone()
    return Decimal(str(row[0]))


# ---------------------------------------------------------------------------
# DB-backed service behaviour
# ---------------------------------------------------------------------------


async def test_recover_tops_up_partial_to_authoritative_total(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-topup-1"
        await _set_markup(db_session, api_key["tenant_id"], "0")
        await _seed_anchor(
            db_session,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            gid=gid,
            cost_usd="0.20",
        )
        clock = FakeClock()
        svc = _service(
            app=app,
            upstream=FakeGenerationUpstream([_cost("1.00")]),
            redis_client=redis_client,
            clock=clock,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id=gid,
        )
        assert outcome.status == "recovered", outcome
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        rows = await _recovered_rows(db_session, api_key["tenant_id"], gid)
        assert len(rows) == 1, f"want one recovered row; got {rows}"
        assert Decimal(str(rows[0][0])) == Decimal("0.80")
        assert Decimal(str(rows[0][1])) == Decimal("1.00")
        assert rows[0][2] == "provider"
        assert await _sum_cost(db_session, api_key["tenant_id"], gid) == Decimal("1.00")
    finally:
        await redis_client.aclose()


async def test_markup_applied_to_authoritative_cost(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-markup-2"
        await _set_markup(db_session, api_key["tenant_id"], "50")
        await _seed_anchor(
            db_session,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            gid=gid,
            cost_usd="0.00",
        )
        clock = FakeClock()
        svc = _service(
            app=app,
            upstream=FakeGenerationUpstream([_cost("2.00")]),
            redis_client=redis_client,
            clock=clock,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id=gid,
        )
        assert outcome.status == "recovered", outcome
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
        rows = await _recovered_rows(db_session, api_key["tenant_id"], gid)
        assert Decimal(str(rows[0][0])) == Decimal("3.00"), rows  # 2.00 * 1.50 - 0
        assert Decimal(str(rows[0][1])) == Decimal("2.00")
    finally:
        await redis_client.aclose()


async def test_negative_delta_when_partial_overestimated(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-neg-3"
        await _set_markup(db_session, api_key["tenant_id"], "0")
        await _seed_anchor(
            db_session,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            gid=gid,
            cost_usd="1.50",
        )
        clock = FakeClock()
        svc = _service(
            app=app,
            upstream=FakeGenerationUpstream([_cost("1.00")]),
            redis_client=redis_client,
            clock=clock,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id=gid,
        )
        assert outcome.status == "recovered", outcome
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
        rows = await _recovered_rows(db_session, api_key["tenant_id"], gid)
        assert Decimal(str(rows[0][0])) == Decimal("-0.50"), rows
        assert await _sum_cost(db_session, api_key["tenant_id"], gid) == Decimal("1.00")
    finally:
        await redis_client.aclose()


async def test_idempotent_second_recover_writes_no_row(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-idem-4"
        await _set_markup(db_session, api_key["tenant_id"], "0")
        await _seed_anchor(
            db_session,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            gid=gid,
            cost_usd="0.10",
        )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)

        def fresh_svc() -> OpenRouterCostRecoveryService:
            return _service(
                app=app,
                upstream=FakeGenerationUpstream([_cost("1.00")]),
                redis_client=redis_client,
                clock=FakeClock(),
            )

        kwargs = {
            "tenant_id": uuid.UUID(api_key["tenant_id"]),
            "key_id": uuid.UUID(api_key["key_id"]),
            "model": "openrouter/recover-test",
            "provider_generation_id": gid,
        }
        first = await fresh_svc().recover(**kwargs)
        assert first.status == "recovered", first
        await flusher.flush_once()

        second = await fresh_svc().recover(**kwargs)
        assert second.status == "skipped:already_recovered", second
        await flusher.flush_once()

        rows = await _recovered_rows(db_session, api_key["tenant_id"], gid)
        assert len(rows) == 1, f"second recover must not write a row; got {rows}"
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# Skip / defer outcomes (no DB write)
# ---------------------------------------------------------------------------


async def test_empty_generation_id_skipped(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        upstream = FakeGenerationUpstream([_cost("1.00")])
        svc = _service(app=app, upstream=upstream, redis_client=redis_client, clock=FakeClock())
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id="",
        )
        assert outcome.status == "skipped:no_generation_id", outcome
        assert upstream.calls == 0
        assert await redis_client.xlen("usage:events") == 0
    finally:
        await redis_client.aclose()


async def test_not_settled_is_deferred(app: Any, db_session: Any, api_key: dict[str, str]) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-pending-5"
        await _seed_anchor(
            db_session,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            gid=gid,
            cost_usd="0.20",
        )
        svc = _service(
            app=app,
            upstream=FakeGenerationUpstream([None]),  # never settles
            redis_client=redis_client,
            clock=FakeClock(),
            ready_deadline_s=2.0,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id=gid,
        )
        assert outcome.status == "deferred:not_settled", outcome
        assert len(await _recovered_rows(db_session, api_key["tenant_id"], gid)) == 0
    finally:
        await redis_client.aclose()


async def test_anchor_not_flushed_is_deferred(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        gid = "gen-noanchor-6"  # no anchor row seeded
        svc = _service(
            app=app,
            upstream=FakeGenerationUpstream([_cost("1.00")]),
            redis_client=redis_client,
            clock=FakeClock(),
            anchor_wait_s=2.0,
        )
        outcome = await svc.recover(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model="openrouter/recover-test",
            provider_generation_id=gid,
        )
        assert outcome.status == "deferred:anchor_not_flushed", outcome
        assert len(await _recovered_rows(db_session, api_key["tenant_id"], gid)) == 0
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# record_correction + flusher explicit-id — additive paths
# ---------------------------------------------------------------------------


class _CapturingRedis:
    def __init__(self) -> None:
        self.events: list[dict[bytes | str, Any]] = []
        self.incrs: list[tuple[str, float]] = []
        self._keys: set[str] = set()

    async def xadd(self, key: str, fields: dict[str, str]) -> str:
        self.events.append(dict(fields))
        return "1-0"

    async def incrbyfloat(self, key: str, amount: float) -> float:
        self.incrs.append((key, amount))
        return amount

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> Any:
        if nx and key in self._keys:
            return None
        self._keys.add(key)
        return True


async def test_record_correction_emits_signed_cost_with_explicit_id(app: Any) -> None:
    redis = _CapturingRedis()
    recorder = RecordingUsageRecorder(redis=redis, session_factory=app.state.sessionmaker)
    event_id = uuid.uuid4()
    await recorder.record_correction(
        event_id=event_id,
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        model="openrouter/x",
        cost_usd=Decimal("-0.50"),
        provider_cost=Decimal("1.00"),
        provider_generation_id="gen-corr",
    )
    assert len(redis.events) == 1
    ev = redis.events[0]
    assert ev["id"] == str(event_id)
    assert Decimal(ev["cost_usd"]) == Decimal("-0.50")
    assert ev["cost_basis"] == "provider"
    assert Decimal(ev["provider_cost"]) == Decimal("1.00")
    assert ev["usage_source"] == "openrouter_recovered"
    assert ev["provider_generation_id"] == "gen-corr"
    # spend counter moved by the signed delta (negative allowed)
    assert any(amount == -0.50 for _, amount in redis.incrs)


async def test_flusher_honors_explicit_id(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        event_id = uuid.uuid4()
        # Emit the SAME explicit id twice → the flusher must collapse to ONE row.
        for _ in range(2):
            await recorder.record_correction(
                event_id=event_id,
                tenant_id=uuid.UUID(api_key["tenant_id"]),
                key_id=uuid.UUID(api_key["key_id"]),
                model="openrouter/explicit-id",
                cost_usd=Decimal("0.25"),
                provider_cost=Decimal("0.25"),
                provider_generation_id="gen-explicit",
            )
        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
        rows = (
            await db_session.execute(
                text("SELECT id FROM usage_records WHERE provider_generation_id = 'gen-explicit'")
            )
        ).fetchall()
        assert len(rows) == 1, f"explicit id must dedup to one row; got {rows}"
        assert str(rows[0][0]) == str(event_id)
    finally:
        await redis_client.aclose()


async def test_concurrent_double_fire_increments_counter_once(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """Inline recovery racing the sweep: both fire the SAME deterministic event_id with
    no flush between. The DB dedups to one row AND the advisory per-key budget counter
    must move exactly ONCE (INCRBYFLOAT is not idempotent — guarded by SET NX)."""
    import asyncio
    import datetime

    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=True)
    await redis_client.flushdb()
    try:
        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        event_id = uuid.uuid4()
        key_id = api_key["key_id"]
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")

        async def fire() -> None:
            await recorder.record_correction(
                event_id=event_id,
                tenant_id=uuid.UUID(api_key["tenant_id"]),
                key_id=uuid.UUID(key_id),
                model="openrouter/concurrent",
                cost_usd=Decimal("0.40"),
                provider_cost=Decimal("0.40"),
                provider_generation_id="gen-concurrent",
            )

        await asyncio.gather(fire(), fire())  # the race: two recoveries, no flush between

        # Two stream entries exist (both xadd'd) — the flusher will collapse them — but
        # the per-key spend counter moved by the delta exactly ONCE, not twice. The
        # counter is an advisory IEEE-754 float (INCRBYFLOAT), so compare with tolerance:
        # ~0.40 = one move; ~0.80 would be the double-move bug.
        counter = await redis_client.get(f"usage:spend:key:{key_id}:{yyyymm}")
        assert abs(float(counter) - 0.40) < 1e-9, f"counter double-moved: {counter}"

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()
        rows = (
            await db_session.execute(
                text("SELECT id FROM usage_records WHERE provider_generation_id = 'gen-concurrent'")
            )
        ).fetchall()
        assert len(rows) == 1, f"concurrent double-fire must dedup to one row; got {rows}"
    finally:
        await redis_client.aclose()


async def test_recovery_outcome_is_frozen() -> None:
    import dataclasses

    out = RecoveryOutcome(status="recovered", delta_usd=Decimal("1"), provider_cost=Decimal("1"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        out.status = "x"  # type: ignore[misc]
