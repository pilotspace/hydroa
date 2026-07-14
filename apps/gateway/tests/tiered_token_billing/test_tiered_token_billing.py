"""Failing-first (red) suite for tiered-token-billing (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario (TT1–TT8) + two DB tests for the additive schema.

RED before BUILD — they fail for the right reason:
  * tiered-cost tests: the live `_fetch_latest_pricing` returns a 5-tuple and the
    recorder bills FLAT (tier prices dropped, tier counts ignored) → cost mismatch.
  * event-field tests: the recorder event dict has no `cached_tokens`/`reasoning_tokens`
    keys yet → AssertionError on the missing field.
  * DB tests: `pricing_snapshots`/`usage_records` lack the new columns → Postgres error.

Run only the unit subset (no infra):
  cd apps/gateway && uv run pytest tests/tiered_token_billing/ -q --no-cov \
      -k "not db" -p no:cacheprovider
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest

from tests.tiered_token_billing.conftest import (
    FakeSession,
    FakeSessionFactory,
    StreamCapture,
)
from tests import _redis_env


# ---------------------------------------------------------------------------
# TT1 — Cached input is billed below fresh input  (Must: cached split; exit-1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt1_cached_input_billed_below_fresh_input(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=Decimal("0.0000025"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens": 100,
        },
        status=200,
    )

    # (200×0.00001) + (800×0.0000025) + (100×0.00003) = 0.007
    expected = (
        Decimal("200") * Decimal("0.00001")
        + Decimal("800") * Decimal("0.0000025")
        + Decimal("100") * Decimal("0.00003")
    )
    flat = Decimal("1000") * Decimal("0.00001") + Decimal("100") * Decimal("0.00003")  # 0.013

    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"cached split mismatch: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert Decimal(evt["cost_usd"]) < flat, "cached request must bill strictly less than uncached"
    assert evt.get("cached_tokens") == "800", f"event cached_tokens: {evt.get('cached_tokens')!r}"


# ---------------------------------------------------------------------------
# TT2 — Reasoning tokens billed at the reasoning rate  (Must: reasoning split; exit-2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt2_reasoning_tokens_billed_at_reasoning_rate(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        reasoning_price=Decimal("0.00006"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/o-x",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 500,
            "completion_tokens_details": {"reasoning_tokens": 400},
        },
        status=200,
    )

    # (100×0.00001) + ((500-400)×0.00003) + (400×0.00006) = 0.028
    expected = (
        Decimal("100") * Decimal("0.00001")
        + Decimal("100") * Decimal("0.00003")
        + Decimal("400") * Decimal("0.00006")
    )
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"reasoning split mismatch: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("reasoning_tokens") == "400", (
        f"event reasoning_tokens: {evt.get('reasoning_tokens')!r}"
    )


# ---------------------------------------------------------------------------
# TT3 — No tiers + base-only prices is byte-identical to today  (Must: floor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt3_no_tiers_byte_identical_to_today(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=None,
        reasoning_price=None,
        pricing_unit="per_token",
        markup_pct=Decimal("10"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={"prompt_tokens": 1000, "completion_tokens": 500},
        status=200,
    )

    # ((1000×0.00001)+(500×0.00003))×1.10 = 0.0275 — same operand order as the v6 path
    expected = (Decimal("1000") * Decimal("0.00001") + Decimal("500") * Decimal("0.00003")) * (
        Decimal("1") + Decimal("10") / Decimal("100")
    )
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"byte-identical floor broken: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("cached_tokens") == "0", "no-tier record must carry cached_tokens=0"
    assert evt.get("reasoning_tokens") == "0", "no-tier record must carry reasoning_tokens=0"


# ---------------------------------------------------------------------------
# TT4 — Absent tier price falls back to the base price  (Must: NULL fallback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt4_absent_cached_price_falls_back_to_prompt_price(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=None,  # NULL → fall back to prompt price
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={
            "prompt_tokens": 1000,
            "prompt_tokens_details": {"cached_tokens": 800},
            "completion_tokens": 0,
        },
        status=200,
    )

    # cached priced at the prompt rate → (200+800)×0.00001 = 0.010 (identical to flat)
    expected = Decimal("200") * Decimal("0.00001") + Decimal("800") * Decimal("0.00001")
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"NULL cached-price fallback mismatch: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("cached_tokens") == "800", "cached_tokens recorded even when priced flat"


# ---------------------------------------------------------------------------
# TT6 — cached_tokens > prompt_tokens is clamped, never negative  (Reject: clamp)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt6_cached_exceeds_prompt_is_clamped(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=Decimal("0.0000025"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    with caplog.at_level(logging.WARNING):
        await recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/gpt-x",
            usage={"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 150}},
            status=200,
        )

    # fresh_in clamps to max(0, 100-150)=0 → 0×prompt + 150×0.0000025 = 0.000375
    expected = Decimal("0") * Decimal("0.00001") + Decimal("150") * Decimal("0.0000025")
    evt = stream.last_event
    got = Decimal(evt["cost_usd"])
    assert got == expected, f"cached clamp mismatch: got {got}, expected {expected}"
    assert got >= Decimal("0"), "cost must never be negative"
    # Contracted clamp response: the tier_token_clamped marker must be logged.
    assert "tier_token_clamped" in caplog.text, "clamp must log the tier_token_clamped marker"
    # And unchanged: exactly one record written, recorder did not raise.
    assert len(stream.events) == 1, "recorder must write exactly one record"


# ---------------------------------------------------------------------------
# TT7 — reasoning_tokens > completion_tokens is clamped  (Reject: clamp)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt7_reasoning_exceeds_completion_is_clamped(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        reasoning_price=Decimal("0.00006"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    with caplog.at_level(logging.WARNING):
        await recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/o-x",
            usage={"completion_tokens": 50, "completion_tokens_details": {"reasoning_tokens": 80}},
            status=200,
        )

    # fresh_out clamps to max(0, 50-80)=0 → prompt=0 + 0×completion + 80×0.00006 = 0.0048
    expected = Decimal("80") * Decimal("0.00006")
    evt = stream.last_event
    got = Decimal(evt["cost_usd"])
    assert got == expected, f"reasoning clamp mismatch: got {got}, expected {expected}"
    assert got >= Decimal("0"), "cost must never be negative"
    # Contracted clamp response: the tier_token_clamped marker must be logged.
    assert "tier_token_clamped" in caplog.text, "clamp must log the tier_token_clamped marker"
    assert len(stream.events) == 1, "recorder must write exactly one record"


# ---------------------------------------------------------------------------
# TT8 — malformed *_details falls back to flat billing  (Reject: malformed → absent)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt8_malformed_details_falls_back_to_flat(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        cached_input_price=Decimal("0.0000025"),
        reasoning_price=Decimal("0.00006"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={
            "prompt_tokens": 1000,
            "prompt_tokens_details": "oops",
            "completion_tokens": 200,
            "completion_tokens_details": {"reasoning_tokens": "x"},
        },
        status=200,
    )

    # both tiers absent → flat: (1000×0.00001)+(200×0.00003) = 0.016
    expected = Decimal("1000") * Decimal("0.00001") + Decimal("200") * Decimal("0.00003")
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"malformed details must bill flat: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("cached_tokens") == "0", "malformed cached tier → 0"
    assert evt.get("reasoning_tokens") == "0", "malformed reasoning tier → 0"
    assert len(stream.events) == 1, "recorder did not raise — exactly one record"


# ---------------------------------------------------------------------------
# TT_DB_SCHEMA — pricing_snapshots gains the two nullable tier-price columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt_db_pricing_snapshots_has_tier_price_columns(app: Any, db_session: Any) -> None:
    """pricing_snapshots gains cached_input_usd_per_token + reasoning_usd_per_token (NULL).

    RED reason: columns absent → Postgres error on INSERT/SELECT.
    """
    from sqlalchemy import text

    model_id = "openai/gpt-x-tier-schema-test"
    snap_id = str(uuid.uuid4())

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-x tier schema test"},
    )
    # Insert WITH the new columns set — fails if columns don't exist yet.
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  cached_input_usd_per_token, reasoning_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.00001, 0.00003, 0.0000025, 0.00006, now())"
        ),
        {"id": snap_id, "m": model_id},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text(
                "SELECT cached_input_usd_per_token, reasoning_usd_per_token"
                " FROM pricing_snapshots WHERE id = :id"
            ),
            {"id": snap_id},
        )
    ).fetchone()
    assert row is not None
    assert Decimal(str(row[0])) == Decimal("0.0000025")
    assert Decimal(str(row[1])) == Decimal("0.00006")


# ---------------------------------------------------------------------------
# TT5 — Tier counts persist on the ledger row through the flusher  (Must: persist; DB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tt5_db_tier_counts_persist_through_flusher(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """After flush, usage_records row carries cached_tokens=300 and reasoning_tokens=50.

    RED reason: usage_records lacks cached_tokens/reasoning_tokens columns → Postgres error.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openai/gpt-x-tier-persist"
        snap_id = str(uuid.uuid4())
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "GPT-x tier persist"},
        )
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
                "  cached_input_usd_per_token, reasoning_usd_per_token, captured_at)"
                " VALUES (:id, :m, 0.00001, 0.00003, 0.0000025, 0.00006, now())"
            ),
            {"id": snap_id, "m": model_id},
        )
        await db_session.commit()

        recorder = RecordingUsageRecorder(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={
                "prompt_tokens": 1000,
                "prompt_tokens_details": {"cached_tokens": 300},
                "completion_tokens": 200,
                "completion_tokens_details": {"reasoning_tokens": 50},
            },
            status=200,
        )

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        row = (
            await db_session.execute(
                text(
                    "SELECT cached_tokens, reasoning_tokens FROM usage_records"
                    " WHERE tenant_id = :tid"
                ),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchone()
        assert row is not None, "no usage row flushed"
        assert int(row[0]) == 300, f"cached_tokens: {row[0]!r}"
        assert int(row[1]) == 50, f"reasoning_tokens: {row[1]!r}"
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()
