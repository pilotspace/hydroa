"""Failing-first (red) suite for provider-cost-reconciliation (contract FROZEN @ v1, §4).

One test per §2 scenario (PC1–PC12).

RED before BUILD — they fail for the right reason:
  * provider-basis tests: the recorder never reads usage["cost"], so it bills on
    catalog math and emits no cost_basis/provider_cost event fields → KeyError /
    mismatch.
  * catalog/byte-identical tests: cost_basis defaults are absent in the event dict.
  * DB tests: usage_records lacks cost_basis/provider_cost columns → Postgres error.
  * knob tests: complete() does not read _usage_accounting yet, so the injected
    usage key is missing (PC12 RED; PC11 green-by-design — floor).

Run only the unit subset (no infra):
  cd apps/gateway && uv run pytest tests/provider_cost_reconciliation/ -q --no-cov \
      -k "not db" -p no:cacheprovider
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any

import pytest

from tests.provider_cost_reconciliation.conftest import (
    CaptureTransport,
    FakeSession,
    FakeSessionFactory,
    StreamCapture,
    make_openrouter_upstream,
)


def _markup(pct: str) -> Decimal:
    return Decimal("1") + Decimal(pct) / Decimal("100")


# ---------------------------------------------------------------------------
# PC1 — provider cost present → billed on the provider basis  (Must)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc1_provider_cost_billed_on_provider_basis(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("10"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openrouter/some-model",
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.0042},
        status=200,
    )

    evt = stream.last_event
    assert evt.get("cost_basis") == "provider", f"cost_basis: {evt.get('cost_basis')!r}"
    assert evt.get("provider_cost") == "0.0042", f"provider_cost: {evt.get('provider_cost')!r}"
    expected = Decimal("0.0042") * _markup("10")  # 0.00462
    assert Decimal(evt["cost_usd"]) == expected, (
        f"provider-basis cost mismatch: got {evt['cost_usd']!r}, expected {expected}"
    )


# ---------------------------------------------------------------------------
# PC2 — no provider cost → catalog basis, byte-identical  (Must: floor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc2_no_provider_cost_is_catalog_byte_identical(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("10"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={"prompt_tokens": 1000, "completion_tokens": 500},  # NO cost field
        status=200,
    )

    # exact v6/t1 catalog math: ((1000×0.00001)+(500×0.00003))×1.10
    expected = (Decimal("1000") * Decimal("0.00001") + Decimal("500") * Decimal("0.00003")) * (
        _markup("10")
    )
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"byte-identical floor broken: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("cost_basis") == "catalog", f"cost_basis: {evt.get('cost_basis')!r}"
    assert evt.get("provider_cost", "") == "", "catalog row must carry NULL (empty) provider_cost"


# ---------------------------------------------------------------------------
# PC3 — provider cost 0 is authoritative, NOT catalog  (Must: free-model zero)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc3_zero_provider_cost_is_authoritative(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    # catalog prices are non-zero, so if catalog were consulted the row would bill > 0.
    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("10"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openrouter/free-model",
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0},
        status=200,
    )

    evt = stream.last_event
    assert evt.get("cost_basis") == "provider", (
        "zero provider cost is authoritative (provider basis)"
    )
    assert Decimal(evt["cost_usd"]) == Decimal("0"), (
        f"free model must bill $0, not catalog: got {evt['cost_usd']!r}"
    )
    assert evt.get("provider_cost") == "0", f"provider_cost: {evt.get('provider_cost')!r}"


# ---------------------------------------------------------------------------
# PC4 — malformed provider cost → catalog fallback, never raises  (Reject)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc4_malformed_provider_cost_falls_back_to_catalog(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "cost": "free"},
        status=200,
    )

    expected = Decimal("1000") * Decimal("0.00001") + Decimal("200") * Decimal("0.00003")
    evt = stream.last_event
    assert evt.get("cost_basis") == "catalog", "malformed cost → catalog basis"
    assert Decimal(evt["cost_usd"]) == expected, (
        f"malformed cost must bill catalog: got {evt['cost_usd']!r}, expected {expected}"
    )
    assert evt.get("provider_cost", "") == "", "malformed → NULL provider_cost"
    assert len(stream.events) == 1, "recorder did not raise — exactly one record"


# ---------------------------------------------------------------------------
# PC5 — negative provider cost → rejected to catalog + warning  (Reject)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc5_negative_provider_cost_rejected_with_warning(
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
            usage={"prompt_tokens": 1000, "completion_tokens": 200, "cost": -1.5},
            status=200,
        )

    evt = stream.last_event
    assert evt.get("cost_basis") == "catalog", "negative cost → catalog basis"
    assert evt.get("provider_cost", "") == "", "negative → NULL provider_cost"
    assert Decimal(evt["cost_usd"]) >= Decimal("0"), "billed cost must never be negative"
    assert "provider_cost_rejected" in caplog.text, "negative cost must log provider_cost_rejected"
    assert len(stream.events) == 1, "recorder did not raise — exactly one record"


# ---------------------------------------------------------------------------
# PC6 — provider cost on a cache hit is ignored  (Reject)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc6_provider_cost_on_cache_hit_ignored(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(snapshot_id=snapshot_id, markup_pct=Decimal("0"))
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openrouter/some-model",
        usage={"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.99},
        status=200,
        cached=True,
    )

    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == Decimal("0"), "cache hit costs 0 regardless of provider cost"
    assert evt.get("cost_basis") == "catalog", "cache hit → catalog basis (no upstream call)"
    assert evt.get("provider_cost", "") == "", "cache hit → NULL provider_cost"


# ---------------------------------------------------------------------------
# PC7 — a bool cost is not a number  (Reject: bool excluded)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc7_bool_cost_not_treated_as_number(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.00001"),
        completion_price=Decimal("0.00003"),
        pricing_unit="per_token",
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-x",
        usage={"prompt_tokens": 100, "completion_tokens": 100, "cost": True},
        status=200,
    )

    evt = stream.last_event
    assert evt.get("cost_basis") == "catalog", "bool cost is not a valid number → catalog"
    assert evt.get("provider_cost", "") == "", "bool → NULL provider_cost"


# ---------------------------------------------------------------------------
# PC8 — _safe_provider_cost unit table  (Reject coverage at the extractor)
# ---------------------------------------------------------------------------


def test_pc8_safe_provider_cost_unit_table() -> None:
    from gateway.usage.application.recorder import _safe_provider_cost  # type: ignore[import]

    assert _safe_provider_cost(None) is None
    assert _safe_provider_cost({}) is None
    assert _safe_provider_cost({"cost": None}) is None
    assert _safe_provider_cost({"cost": "free"}) is None
    assert _safe_provider_cost({"cost": True}) is None
    assert _safe_provider_cost({"cost": False}) is None
    assert _safe_provider_cost({"cost": -0.01}) is None
    assert _safe_provider_cost({"cost": 0}) == Decimal("0")
    assert _safe_provider_cost({"cost": 0.0042}) == Decimal("0.0042")
    assert _safe_provider_cost({"cost": 5}) == Decimal("5")


# ---------------------------------------------------------------------------
# PC9 (DB) — provider-basis row persists cost_basis + provider_cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc9_db_provider_basis_row_persisted(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """After flush, the usage_records row carries cost_basis='provider', provider_cost=0.0042.

    RED reason: usage_records lacks cost_basis/provider_cost columns → Postgres error.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/provider-cost-persist"
        snap_id = str(uuid.uuid4())
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "Provider cost persist"},
        )
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
                " VALUES (:id, :m, 0.00001, 0.00003, now())"
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
            usage={"prompt_tokens": 1000, "completion_tokens": 500, "cost": 0.0042},
            status=200,
        )

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        row = (
            await db_session.execute(
                text("SELECT cost_basis, provider_cost FROM usage_records WHERE tenant_id = :tid"),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchone()
        assert row is not None, "no usage row flushed"
        assert row[0] == "provider", f"cost_basis: {row[0]!r}"
        assert Decimal(str(row[1])) == Decimal("0.0042"), f"provider_cost: {row[1]!r}"
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# PC10 (DB) — catalog-basis row persists cost_basis='catalog', provider_cost NULL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc10_db_catalog_basis_row_persisted(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """After flush, a no-provider-cost row has cost_basis='catalog' and provider_cost IS NULL.

    RED reason: usage_records lacks the two columns → Postgres error.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openai/catalog-basis-persist"
        snap_id = str(uuid.uuid4())
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "Catalog basis persist"},
        )
        await db_session.execute(
            text(
                "INSERT INTO pricing_snapshots"
                " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
                " VALUES (:id, :m, 0.00001, 0.00003, now())"
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
            usage={"prompt_tokens": 1000, "completion_tokens": 500},  # NO cost
            status=200,
        )

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        row = (
            await db_session.execute(
                text("SELECT cost_basis, provider_cost FROM usage_records WHERE tenant_id = :tid"),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchone()
        assert row is not None, "no usage row flushed"
        assert row[0] == "catalog", f"cost_basis: {row[0]!r}"
        assert row[1] is None, f"catalog row must have NULL provider_cost: {row[1]!r}"
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# PC11 — knob OFF (default) → outbound payload byte-identical (no usage key)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc11_openrouter_knob_off_payload_unchanged(
    _inject_test_credential: None,
) -> None:
    transport = CaptureTransport()
    upstream = make_openrouter_upstream(transport, usage_accounting=False)

    payload = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    status, _ = await upstream.complete(payload)

    assert status == 200
    sent = transport.last_body
    assert "usage" not in sent, f"knob OFF must not inject usage accounting: {sent!r}"


# ---------------------------------------------------------------------------
# PC12 — knob ON → outbound payload carries usage={"include": true}, non-destructive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc12_openrouter_knob_on_injects_usage_accounting(
    _inject_test_credential: None,
) -> None:
    transport = CaptureTransport()
    upstream = make_openrouter_upstream(transport, usage_accounting=True)

    payload = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    status, _ = await upstream.complete(payload)

    assert status == 200
    sent = transport.last_body
    assert sent.get("usage") == {"include": True}, (
        f"knob ON must inject usage={{'include': true}}: {sent!r}"
    )

    # Non-destructive: a caller-supplied usage key is preserved, not overwritten.
    transport2 = CaptureTransport()
    upstream2 = make_openrouter_upstream(transport2, usage_accounting=True)
    caller_payload = {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "hi"}],
        "usage": {"include": False, "custom": 1},
    }
    await upstream2.complete(caller_payload)
    sent2 = transport2.last_body
    assert sent2.get("usage") == {"include": False, "custom": 1}, (
        f"caller-supplied usage must be preserved: {sent2!r}"
    )


# ---------------------------------------------------------------------------
# PC13 — the stream() path injects the knob too (contract: complete() AND stream())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pc13_openrouter_stream_injects_usage_accounting(
    _inject_test_credential: None,
) -> None:
    # knob ON → stream outbound body carries usage accounting
    transport_on = CaptureTransport()
    upstream_on = make_openrouter_upstream(transport_on, usage_accounting=True)
    payload = {"model": "openai/gpt-4o", "messages": [{"role": "user", "content": "hi"}]}
    async for _ in upstream_on.stream(payload):
        pass
    assert transport_on.last_body.get("usage") == {"include": True}, (
        f"stream() knob ON must inject usage accounting: {transport_on.last_body!r}"
    )

    # knob OFF (default) → stream outbound body unchanged (no usage key)
    transport_off = CaptureTransport()
    upstream_off = make_openrouter_upstream(transport_off, usage_accounting=False)
    async for _ in upstream_off.stream(payload):
        pass
    assert "usage" not in transport_off.last_body, (
        f"stream() knob OFF must not inject usage accounting: {transport_off.last_body!r}"
    )


# ---------------------------------------------------------------------------
# PC14 — wiring: Settings.openrouter_usage_accounting threads to the live upstream
# ---------------------------------------------------------------------------


def test_pc14_settings_knob_threads_into_upstream() -> None:
    """create_app(settings with knob=True) → the raw OpenRouter upstream reads it.

    Pins the production-wiring seam (main.py threads settings.openrouter_usage_accounting
    into OpenRouterCompletionUpstream._usage_accounting). Default-off stays off.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    def _settings(*, usage_accounting: bool) -> Settings:
        return Settings(
            database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            jwt_secret="test-secret-not-for-production-0123456789",
            redis_url="redis://localhost:6380/9",
            openrouter_usage_accounting=usage_accounting,
        )

    app_on = create_app(_settings(usage_accounting=True))
    assert app_on.state.openrouter_completion_upstream._usage_accounting is True

    app_off = create_app(_settings(usage_accounting=False))
    assert app_off.state.openrouter_completion_upstream._usage_accounting is False
