"""RED — file_search grounding meters exactly ONE `per_query` billable unit.

Contract under test (file-search-tool PLAN.md §3, DRAFT):
  - `per_query` is added to the recorder's `_known_units` (BOTH occurrences), so a usage
    record with pricing_unit="per_query" prices via the generic non-token branch:
        cost = quantity x unit_usd_per_unit x (1 + markup_pct/100)   (tokens stay 0)
    exactly as per_image / per_second do today.
  - quantity = number of file_search invocations (1 per search, NEVER per chunk).
  - The stream event carries pricing_unit="per_query" and quantity="1".

RED reason: "per_query" is NOT in `_known_units` today -> the recorder falls back to the
per_token branch (resolved_pricing_unit="per_token"), ignores unit_usd_per_unit, and with
zero tokens bills $0 and emits pricing_unit="per_token". Real behavior red on the live,
imported RecordingUsageRecorder -- not an import/harness error.

Run only this file:
  cd apps/gateway && GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_file_search_tool \
    uv run pytest tests/file_search_tool/test_per_query_metering.py -q --override-ini="addopts="
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests.file_search_tool.conftest import FakeSession, FakeSessionFactory, StreamCapture

pytestmark = pytest.mark.asyncio


async def test_per_query_priced_via_unit_rate_tokens_zero(
    snapshot_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """per_query: cost = 1 x unit_usd_per_unit x (1+markup); prompt/completion tokens = 0.

    Expected: 1 x 0.0025 x 1.20 = 0.00300000.
    """
    from gateway.usage.application.recorder import RecordingUsageRecorder

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0"),
        completion_price=Decimal("0"),
        pricing_unit="per_query",
        unit_usd_per_unit=Decimal("0.0025"),
        markup_pct=Decimal("20"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="file_search",
        usage=None,
        status=200,
        pricing_unit="per_query",
        quantity=Decimal("1"),
    )

    expected = Decimal("1") * Decimal("0.0025") * (Decimal("1") + Decimal("20") / Decimal("100"))
    evt = stream.last_event
    assert Decimal(evt["cost_usd"]) == expected, (
        f"per_query cost mismatch: got {evt['cost_usd']!r}, expected {expected}. "
        "Recorder likely fell back to the per_token branch (per_query not in _known_units)."
    )
    assert evt.get("pricing_unit") == "per_query", (
        f"event must carry pricing_unit='per_query', got {evt.get('pricing_unit')!r}"
    )
    assert evt.get("quantity") == "1", f"event must carry quantity='1', got {evt.get('quantity')!r}"
    assert evt["prompt_tokens"] == "0"
    assert evt["completion_tokens"] == "0"


async def test_per_query_single_bill_one_record_per_search(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
) -> None:
    """Exactly ONE per_query record is emitted per file_search invocation (never per chunk)."""
    from gateway.usage.application.recorder import RecordingUsageRecorder

    session = FakeSession(
        pricing_unit="per_query",
        unit_usd_per_unit=Decimal("0.0025"),
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    # One search that returned, say, 5 chunks -> still exactly one billable per_query row.
    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="file_search",
        usage=None,
        status=200,
        pricing_unit="per_query",
        quantity=Decimal("1"),
    )

    per_query_events = [e for e in stream.events if e.get("pricing_unit") == "per_query"]
    assert len(per_query_events) == 1, (
        f"expected exactly ONE per_query event per search, got {len(per_query_events)}: "
        f"{stream.events!r}"
    )
    assert per_query_events[0].get("quantity") == "1", (
        "quantity must be 1 per search, NOT the chunk count"
    )
