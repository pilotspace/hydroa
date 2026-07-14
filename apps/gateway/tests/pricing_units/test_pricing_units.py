"""Failing-first (red) suite for pricing-units (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/pricing-units/TASK.md §2 (PU1–PU10).
All tests in this file are RED before BUILD — they fail for the right reason
(missing columns / missing dispatch branches / missing TypedDict fields), not
for import errors or skips.

Run only this suite:
  cd apps/gateway && uv run pytest tests/pricing_units/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any, ClassVar

import pytest

from tests.pricing_units.conftest import (
    FakeSession,
    FakeSessionFactory,
    SpyRecorder,
    StreamCapture,
)
from tests import _redis_env


# ---------------------------------------------------------------------------
# PU1 — per_token snapshot: cost == v6 two-term formula (byte-identical pin)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu1_per_token_cost_byte_identical_to_v6(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Per_token path must produce the same Decimal result as the v6 formula.

    Expected: (100×0.0000025 + 50×0.00001) × 1.20 = 0.00090000

    Also asserts the event carries the new pricing_unit field (='per_token').
    RED reason: the event does not yet carry a 'pricing_unit' field — the recorder
    does not emit it until the per-unit dispatch is implemented. This assertion
    is what makes PU1 red: the cost math passes (v6 formula is unchanged) but the
    new contract field is absent from the stream event.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.0000025"),
        completion_price=Decimal("0.00001"),
        pricing_unit="per_token",
        unit_usd_per_unit=None,
        markup_pct=Decimal("20"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(
        redis=stream,
        session_factory=FakeSessionFactory(session),
    )

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        status=200,
        # No pricing_unit or quantity extras — chat path
    )

    expected_cost = (Decimal("100") * Decimal("0.0000025") + Decimal("50") * Decimal("0.00001")) * (
        Decimal("1") + Decimal("20") / Decimal("100")
    )

    assert stream.events, "No event pushed to Redis Stream"
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected_cost, (
        f"cost_usd mismatch: got {evt['cost_usd']!r}, expected {expected_cost}"
    )
    assert evt["prompt_tokens"] == "100"
    assert evt["completion_tokens"] == "50"
    # NEW CONTRACT: event must carry pricing_unit field (absent until dispatch lands)
    assert evt.get("pricing_unit") == "per_token", (
        f"Event missing 'pricing_unit' field or wrong value: {evt.get('pricing_unit')!r}. "
        "The recorder must emit pricing_unit='per_token' in the stream event."
    )


# ---------------------------------------------------------------------------
# PU2 — per_image snapshot + quantity=2 via extras → cost = 2×unit×(1+markup); tokens=0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu2_per_image_cost_and_zero_tokens(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Per_image: cost = quantity × unit_usd_per_unit × (1+markup); tokens stay 0.

    Expected: 2 × 0.04 × 1.20 = 0.09600000
    RED reason: per_image dispatch branch absent in _record_internal; also
    _fetch_latest_pricing does not return unit_usd_per_unit yet.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0"),
        completion_price=Decimal("0"),
        pricing_unit="per_image",
        unit_usd_per_unit=Decimal("0.04"),
        markup_pct=Decimal("20"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(
        redis=stream,
        session_factory=FakeSessionFactory(session),
    )

    # Per the typed-extras seam: pricing_unit + quantity flow through supported_extras
    # The recorder's record() signature accepts **extras forwarded by _dispatch_record.
    # For this unit test, we call record() directly with the expected extended signature.
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/dall-e-3",
        usage=None,
        status=200,
        pricing_unit="per_image",
        quantity=Decimal("2"),
    )

    expected_cost = Decimal("2") * Decimal("0.04") * Decimal("1.20")

    assert stream.events, "No event pushed"
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected_cost, (
        f"per_image cost mismatch: got {evt['cost_usd']!r}, expected {expected_cost}"
    )
    assert evt["prompt_tokens"] == "0", "prompt_tokens must be 0 for per_image"
    assert evt["completion_tokens"] == "0", "completion_tokens must be 0 for per_image"
    assert evt.get("pricing_unit") == "per_image", "event must carry pricing_unit field"
    # quantity should be encoded as string in the event
    assert evt.get("quantity") == "2", f"event quantity field mismatch: {evt.get('quantity')!r}"


# ---------------------------------------------------------------------------
# PU3 — per_second + quantity=12.5 → cost = 12.5×unit×(1+markup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu3_per_second_cost_fractional_quantity(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Per_second: cost = 12.5 × 0.006 × 1.00 = 0.075000.

    RED reason: per_second dispatch branch absent.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0"),
        completion_price=Decimal("0"),
        pricing_unit="per_second",
        unit_usd_per_unit=Decimal("0.006"),
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(
        redis=stream,
        session_factory=FakeSessionFactory(session),
    )

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/whisper-1",
        usage=None,
        status=200,
        pricing_unit="per_second",
        quantity=Decimal("12.5"),
    )

    expected_cost = Decimal("12.5") * Decimal("0.006") * Decimal("1.00")

    assert stream.events, "No event pushed"
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected_cost, (
        f"per_second cost mismatch: got {evt['cost_usd']!r}, expected {expected_cost}"
    )
    assert evt["prompt_tokens"] == "0"
    assert evt["completion_tokens"] == "0"


# ---------------------------------------------------------------------------
# PU4 — per_character + quantity=480 → cost = 480×unit×(1+markup)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu4_per_character_cost(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Per_character: cost = 480 × 0.000015 × 1.10 = 0.007920.

    RED reason: per_character dispatch branch absent.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0"),
        completion_price=Decimal("0"),
        pricing_unit="per_character",
        unit_usd_per_unit=Decimal("0.000015"),
        markup_pct=Decimal("10"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(
        redis=stream,
        session_factory=FakeSessionFactory(session),
    )

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/tts-1",
        usage=None,
        status=200,
        pricing_unit="per_character",
        quantity=Decimal("480"),
    )

    expected_cost = (
        Decimal("480") * Decimal("0.000015") * (Decimal("1") + Decimal("10") / Decimal("100"))
    )

    assert stream.events, "No event pushed"
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected_cost, (
        f"per_character cost mismatch: got {evt['cost_usd']!r}, expected {expected_cost}"
    )
    assert evt["prompt_tokens"] == "0"
    assert evt["completion_tokens"] == "0"


# ---------------------------------------------------------------------------
# PU5 — markup applied uniformly across all non-token units (markup=20 → ×1.2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu5_markup_applied_uniformly_across_all_units(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Each non-token unit: cost = unit_usd_per_unit × quantity × (1 + 20/100).

    Tests per_image, per_second, per_character each with quantity=1 and markup=20.
    RED reason: non-token dispatch branches absent.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    unit_configs = [
        ("per_image", Decimal("0.04")),
        ("per_second", Decimal("0.006")),
        ("per_character", Decimal("0.000015")),
    ]

    for pricing_unit_val, unit_price in unit_configs:
        session = FakeSession(
            snapshot_id=uuid.uuid4(),
            prompt_price=Decimal("0"),
            completion_price=Decimal("0"),
            pricing_unit=pricing_unit_val,
            unit_usd_per_unit=unit_price,
            markup_pct=Decimal("20"),
        )
        stream = StreamCapture()
        recorder = RecordingUsageRecorder(
            redis=stream,
            session_factory=FakeSessionFactory(session),
        )

        await recorder.record(
            tenant_id=tenant_id,
            key_id=key_id,
            model="test-model",
            usage=None,
            status=200,
            pricing_unit=pricing_unit_val,
            quantity=Decimal("1"),
        )

        expected = unit_price * Decimal("1.20")
        assert stream.events, f"No event for {pricing_unit_val}"
        got = Decimal(stream.last_event["cost_usd"])
        assert got == expected, (
            f"Markup not applied uniformly for {pricing_unit_val}: got {got}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# PU6 — usage_records row carries pricing_unit and quantity columns (DB test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu6_usage_records_row_carries_pricing_unit_and_quantity(
    app: Any,
    db_session: Any,
    api_key: dict[str, str],
) -> None:
    """After flush, usage_records row has pricing_unit='per_image', quantity=3.

    RED reason: usage_records table lacks pricing_unit and quantity columns
    (migration not yet applied — OperationalError from Postgres).

    This test requires the shared conftest app fixture (real Postgres + Redis).
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await redis_client.flushdb()

    try:
        # Insert the model row so pricing snapshot FK resolves
        snapshot_id = str(uuid.uuid4())
        model_id = "openai/dall-e-3"
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true)"
                " ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "DALL-E 3"},
        )
        # Insert a per_image pricing snapshot
        # RED: pricing_snapshots table lacks pricing_unit column → OperationalError
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
                "  pricing_unit, unit_usd_per_unit, captured_at)"
                " VALUES (:id, :m, 0, 0, 'per_image', 0.04, now())"
            ),
            {"id": snapshot_id, "m": model_id},
        )
        await db_session.commit()

        recorder = RecordingUsageRecorder(
            redis=redis_client,
            session_factory=app.state.sessionmaker,
        )

        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage=None,
            status=200,
            pricing_unit="per_image",
            quantity=Decimal("3"),
        )

        flusher = UsageLedgerFlusher(
            redis=redis_client,
            session_factory=app.state.sessionmaker,
        )
        await flusher.flush_once()

        rows = (
            await db_session.execute(
                text(
                    "SELECT pricing_unit, quantity, prompt_tokens, completion_tokens"
                    " FROM usage_records WHERE tenant_id = :tid"
                ),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchall()

        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        row = rows[0]
        assert str(row[0]) == "per_image", f"pricing_unit: {row[0]!r}"
        assert Decimal(str(row[1])) == Decimal("3"), f"quantity: {row[1]!r}"
        assert int(row[2]) == 0, "prompt_tokens must be 0"
        assert int(row[3]) == 0, "completion_tokens must be 0"
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# PU7 — pricing_snapshots carries pricing_unit; existing rows backfill per_token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu7_pricing_snapshots_has_pricing_unit_column_backfilled_per_token(
    app: Any,
    db_session: Any,
) -> None:
    """pricing_snapshots gains pricing_unit NOT NULL DEFAULT 'per_token'.

    Inserts a row WITHOUT specifying pricing_unit (simulates a pre-migration row)
    and asserts the column is present with value 'per_token' and
    unit_usd_per_unit IS NULL.

    RED reason: pricing_snapshots table lacks pricing_unit column → OperationalError.
    """
    from sqlalchemy import text

    model_id = "openai/gpt-4o-pricing-unit-test"
    snap_id = str(uuid.uuid4())

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o pricing test"},
    )
    # Insert WITHOUT pricing_unit → server DEFAULT 'per_token' must apply
    # This INSERT will fail with OperationalError if the column doesn't exist yet
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": snap_id, "m": model_id},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text("SELECT pricing_unit, unit_usd_per_unit FROM pricing_snapshots WHERE id = :id"),
            {"id": snap_id},
        )
    ).fetchone()

    assert row is not None, "Row not found"
    assert str(row[0]) == "per_token", (
        f"Expected pricing_unit='per_token' (DEFAULT), got {row[0]!r}"
    )
    assert row[1] is None, f"Expected unit_usd_per_unit=NULL for a per_token row, got {row[1]!r}"


# ---------------------------------------------------------------------------
# PU8 — UsageRecordExtras has typed pricing_unit + quantity fields;
#        supported_extras includes them; typed-seam filtering pin
# ---------------------------------------------------------------------------


def test_pu8_typed_seam_filtering_pricing_unit_and_quantity() -> None:
    """UsageRecordExtras TypedDict must declare pricing_unit and quantity.

    Asserts:
      1. UsageRecordExtras has 'pricing_unit' and 'quantity' in __annotations__
      2. RecordingUsageRecorder.supported_extras contains both
      3. _dispatch_record filter passes only declared supported keys
      4. A v1-Protocol fake (no supported_extras) receives only base kwargs

    RED reason: UsageRecordExtras lacks pricing_unit/quantity fields →
    AssertionError on __annotations__ check. supported_extras also absent.
    """

    from gateway.proxy.domain.ports import UsageRecordExtras  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    # 1. TypedDict must declare the new fields
    annotations = UsageRecordExtras.__annotations__
    assert "pricing_unit" in annotations, (
        f"UsageRecordExtras missing 'pricing_unit'; has: {list(annotations)}"
    )
    assert "quantity" in annotations, (
        f"UsageRecordExtras missing 'quantity'; has: {list(annotations)}"
    )

    # 2. supported_extras on the recorder must include both
    supported = RecordingUsageRecorder.supported_extras
    assert "pricing_unit" in supported, f"supported_extras missing 'pricing_unit'; has: {supported}"
    assert "quantity" in supported, f"supported_extras missing 'quantity'; has: {supported}"

    # 3. Filtering: a full extras dict with an unknown key — only declared keys pass
    from gateway.proxy.application.use_cases import _dispatch_record  # type: ignore[import]

    class SpyWithExtras:
        """Spy recorder that declares the full supported_extras."""

        supported_extras: frozenset[str] = RecordingUsageRecorder.supported_extras
        received_kwargs: ClassVar[dict[str, Any]] = {}

        async def record(self, **kwargs: Any) -> None:
            SpyWithExtras.received_kwargs = kwargs

    spy = SpyWithExtras()
    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()

    extras: UsageRecordExtras = {
        "pricing_unit": "per_image",  # type: ignore[typeddict-item]
        "quantity": Decimal("2"),  # type: ignore[typeddict-item]
    }
    # Add an unknown key that must be filtered out
    raw_extras: dict[str, Any] = dict(extras)
    raw_extras["unknown_key"] = True

    # Run _dispatch_record in an event loop
    async def _run() -> None:
        _dispatch_record(
            spy,
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/dall-e-3",
            usage=None,
            status=200,
            extras=raw_extras,  # type: ignore[arg-type]
        )
        # Give the scheduled coroutine a chance to run
        await asyncio.sleep(0)

    asyncio.run(_run())

    # pricing_unit and quantity must have been forwarded
    assert "pricing_unit" in SpyWithExtras.received_kwargs, (
        "pricing_unit was filtered out but should have been forwarded"
    )
    assert "quantity" in SpyWithExtras.received_kwargs, (
        "quantity was filtered out but should have been forwarded"
    )
    # unknown_key must NOT have been forwarded
    assert "unknown_key" not in SpyWithExtras.received_kwargs, (
        "unknown_key was incorrectly forwarded"
    )

    # 4. v1-Protocol fake (no supported_extras attr) receives only base kwargs
    class V1FakeRecorder:
        """Mimics a v1-Protocol test fake with no supported_extras."""

        received_kwargs: ClassVar[dict[str, Any]] = {}

        async def record(self, **kwargs: Any) -> None:
            V1FakeRecorder.received_kwargs = kwargs

    v1_fake = V1FakeRecorder()
    # V1FakeRecorder has no supported_extras — getattr returns frozenset()
    assert not hasattr(v1_fake, "supported_extras")

    async def _run_v1() -> None:
        _dispatch_record(
            v1_fake,  # type: ignore[arg-type]
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            status=200,
            extras=raw_extras,  # type: ignore[arg-type]
        )
        await asyncio.sleep(0)

    asyncio.run(_run_v1())

    # v1 fake must NOT receive any extras (all filtered by empty supported set)
    assert "pricing_unit" not in V1FakeRecorder.received_kwargs, (
        "pricing_unit was forwarded to a v1 fake without supported_extras"
    )
    assert "quantity" not in V1FakeRecorder.received_kwargs, (
        "quantity was forwarded to a v1 fake without supported_extras"
    )
    assert "unknown_key" not in V1FakeRecorder.received_kwargs, (
        "unknown_key was forwarded to a v1 fake"
    )


# ---------------------------------------------------------------------------
# PU9 — Token request with NO extras bills exactly as v6 (default per_token)
# GREEN-BY-DESIGN once PU1 passes; RED now for the same reason as PU1
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pu9_default_per_token_no_extras_identical_to_v6(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Chat-path invocation (no extras) must produce identical cost to PU1.

    This is the proof that the default-per_token path is byte-identical to v6.
    Also asserts the event carries pricing_unit='per_token' (the new contract field).
    RED reason: event does not yet carry 'pricing_unit' field until dispatch lands
    (same root cause as PU1). GREEN once the per_token dispatch is implemented.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.0000025"),
        completion_price=Decimal("0.00001"),
        pricing_unit="per_token",
        unit_usd_per_unit=None,
        markup_pct=Decimal("20"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(
        redis=stream,
        session_factory=FakeSessionFactory(session),
    )

    # Exactly the chat-path call pattern: NO pricing_unit or quantity extras
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-4o",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        status=200,
    )

    expected_cost = (
        Decimal("100") * Decimal("0.0000025") + Decimal("50") * Decimal("0.00001")
    ) * Decimal("1.20")

    assert stream.events, "No event pushed"
    assert Decimal(stream.last_event["cost_usd"]) == expected_cost, (
        f"Default per_token path cost != v6 formula: {stream.last_event['cost_usd']!r}"
    )
    # NEW CONTRACT: event must carry pricing_unit='per_token' (absent until dispatch lands)
    assert stream.last_event.get("pricing_unit") == "per_token", (
        f"Event missing 'pricing_unit' field or wrong value: "
        f"{stream.last_event.get('pricing_unit')!r}. "
        "The default chat path must emit pricing_unit='per_token'."
    )


# ---------------------------------------------------------------------------
# PU10 — Single-bill: recorder called exactly once for a non-token request
# ---------------------------------------------------------------------------


def test_pu10_single_bill_one_recorded_row_per_non_token_request() -> None:
    """_fire_record_with_raw must schedule exactly one record() call.

    Verifies that the _fire_record_with_raw function accepts pricing_unit and
    quantity kwargs and passes them through to _dispatch_record without
    scheduling additional record() calls.

    RED reason: _fire_record_with_raw does not accept pricing_unit or quantity
    kwargs yet → TypeError on the unexpected keyword argument.
    """

    from gateway.proxy.application.use_cases import _fire_record_with_raw  # type: ignore[import]

    spy = SpyRecorder()
    spy.supported_extras = frozenset({"team_id", "pricing_unit", "quantity"})

    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()

    async def _run() -> None:
        _fire_record_with_raw(
            spy,
            tenant_id=tenant_id,
            key_id=key_id,
            model="openai/dall-e-3",
            usage=None,
            status=200,
            pricing_unit="per_image",  # NEW kwarg — must be accepted
            quantity=Decimal("2"),  # NEW kwarg — must be accepted
        )
        # Let the ensure_future coroutine run
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert spy.call_count == 1, (
        f"Expected exactly 1 record() call (single-bill invariant); got {spy.call_count}"
    )
