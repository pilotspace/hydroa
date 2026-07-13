"""RED suite for stream-usage-completeness (TASK.md §4, scenarios SU1–SU7).

When a chat stream's upstream omits/partials the terminal `usage` SSE frame, the
single billed record must stop being a SILENT $0: it is stamped
`usage_source="stream_fallback"` + a `stream_usage_frame_missing` WARN, while a
complete frame stays byte-identical (`usage_source="frame"`, no warn). A new
pure predicate `stream_usage_is_complete` decides which.

RED at write time: `stream_usage_is_complete` does not exist (module-level import
fails) and `_wrapped` does not pass a `usage_source` kwarg yet.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import pytest

# The unit under test — does not exist yet → ImportError is the RED reason.
from gateway.usage.domain.extractor import stream_usage_is_complete

from .conftest import (
    A0,
    COMPLETE_USAGE,
    DONE,
    PARTIAL_USAGE,
    TIERED_USAGE,
    CAND_A,
    MarkerSpyRecorder,
    PlanStreamUpstream,
    run_stream,
)
from tests import _redis_env

# pytest is configured asyncio_mode=auto (pyproject.toml): `async def test_*` runs
# on the loop without a marker, and the sync SU7 table stays sync. No file-level
# pytest.mark.asyncio (it would warn on the sync parametrized test).


async def _settle() -> None:
    # Let the fire-and-forget usage record run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# SU1 — complete frame bills unchanged, usage_source="frame", no warn
# ---------------------------------------------------------------------------


async def test_su1_complete_frame_bills_unchanged_source_frame() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, COMPLETE_USAGE, DONE]})
    rec = MarkerSpyRecorder()

    chunks = await run_stream(up, rec)
    await _settle()

    assert chunks == [A0, COMPLETE_USAGE, DONE]
    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}"
    call = rec.last_call
    assert call["status"] == 200
    assert call["usage"]["total_tokens"] == 15  # frame passed through unchanged
    assert call.get("usage_source") == "frame", f"usage_source: {call.get('usage_source')!r}"


# ---------------------------------------------------------------------------
# SU2 — missing terminal frame → flagged stream_fallback + WARN, never silent $0
# ---------------------------------------------------------------------------


async def test_su2_missing_frame_flagged_not_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    up = PlanStreamUpstream({CAND_A: [A0, DONE]})  # NO usage frame
    rec = MarkerSpyRecorder()

    with caplog.at_level("WARNING"):
        chunks = await run_stream(up, rec)
        await _settle()

    assert chunks == [A0, DONE]
    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}"
    call = rec.last_call
    assert call["status"] == 200
    assert call.get("usage") is None  # no frame extracted
    assert call.get("usage_source") == "stream_fallback", (
        f"usage_source: {call.get('usage_source')!r}"
    )
    assert "stream_usage_frame_missing" in caplog.text, (
        f"expected stream_usage_frame_missing warning, caplog: {caplog.text!r}"
    )


# ---------------------------------------------------------------------------
# SU3 — partial frame (no positive token count) → treated as fallback
# ---------------------------------------------------------------------------


async def test_su3_partial_frame_treated_as_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    up = PlanStreamUpstream({CAND_A: [A0, PARTIAL_USAGE, DONE]})
    rec = MarkerSpyRecorder()

    with caplog.at_level("WARNING"):
        await run_stream(up, rec)
        await _settle()

    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}"
    assert rec.last_call.get("usage_source") == "stream_fallback", (
        f"partial frame must flag fallback, got {rec.last_call.get('usage_source')!r}"
    )
    assert "stream_usage_frame_missing" in caplog.text


# ---------------------------------------------------------------------------
# SU4 — tiered usage survives the tee on a complete frame
# ---------------------------------------------------------------------------


async def test_su4_tiered_usage_survives_tee() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, TIERED_USAGE, DONE]})
    rec = MarkerSpyRecorder()

    await run_stream(up, rec)
    await _settle()

    assert rec.call_count == 1
    usage = rec.last_call["usage"]
    assert usage["prompt_tokens_details"]["cached_tokens"] == 4, "cached tier must survive"
    assert usage["completion_tokens_details"]["reasoning_tokens"] == 2, "reasoning tier survives"
    assert rec.last_call.get("usage_source") == "frame"


# ---------------------------------------------------------------------------
# SU5 — single-bill invariant on a missing-frame stream (exactly one record)
# ---------------------------------------------------------------------------


async def test_su5_single_bill_on_missing_frame() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, DONE]})
    rec = MarkerSpyRecorder()

    await run_stream(up, rec)
    await _settle()

    assert rec.call_count == 1, f"missing-frame stream must single-bill, got {rec.call_count}"
    assert rec.last_call.get("usage_source") == "stream_fallback"


# ---------------------------------------------------------------------------
# SU6 (DB) — the usage_source column is additive + a stream_fallback row persists $0
# ---------------------------------------------------------------------------


async def test_su6_usage_source_column_additive_and_fallback_persists(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """A stream_fallback record persists usage_source='stream_fallback' + cost_usd=0;
    a record WITHOUT a usage_source defaults to 'frame' AND bills its real tokens (cost>0)
    — the additive, byte-identical floor for every non-stream caller.

    Schema note: the `app` fixture builds the test schema via Base.metadata.create_all
    (reflecting the ORM column), so this test exercises the recorder→flusher→column
    chain. The MIGRATION FILE b8e4f1a7c2d5 itself (upgrade-from-empty + autogenerate
    empty-diff) is verified by the dedicated tests/migrations/ suite.

    RED reason: usage_records lacks the usage_source column → Postgres error on flush.
    """
    import redis.asyncio as aioredis  # type: ignore[import-untyped]
    from sqlalchemy import text

    from gateway.usage.application.flusher import UsageLedgerFlusher  # type: ignore[import]
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    redis_client = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await redis_client.flushdb()
    try:
        model_id = "openrouter/stream-usage-persist"
        snap_id = str(uuid.uuid4())
        await db_session.execute(
            text(
                "INSERT INTO models (id, name, context_length, active)"
                " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
            ),
            {"i": model_id, "n": "Stream usage persist"},
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
        # A stream that lost its usage frame: usage=None, flagged stream_fallback.
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage=None,
            status=200,
            usage_source="stream_fallback",
        )
        # A normal record with NO usage_source → defaults 'frame'.
        await recorder.record(
            tenant_id=uuid.UUID(api_key["tenant_id"]),
            key_id=uuid.UUID(api_key["key_id"]),
            model=model_id,
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            status=200,
        )

        flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
        await flusher.flush_once()

        rows = (
            await db_session.execute(
                text(
                    "SELECT usage_source, cost_usd FROM usage_records"
                    " WHERE tenant_id = :tid ORDER BY usage_source"
                ),
                {"tid": api_key["tenant_id"]},
            )
        ).fetchall()
        sources = {r[0]: r[1] for r in rows}
        assert "frame" in sources, f"default-frame row missing; rows={rows}"
        assert "stream_fallback" in sources, f"fallback row missing; rows={rows}"
        assert Decimal(str(sources["stream_fallback"])) == Decimal("0"), (
            f"stream_fallback must persist cost 0, got {sources['stream_fallback']!r}"
        )
        # A 'frame'-source row bills its real tokens (guards against a complete-frame $0 regression).
        assert Decimal(str(sources["frame"])) > Decimal("0"), (
            f"frame row must bill its tokens (cost>0), got {sources['frame']!r}"
        )
    finally:
        await redis_client.flushdb()
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# SU7 — stream_usage_is_complete is a pure, total predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (None, False),
        ({}, False),
        ({"total_tokens": 0}, False),
        ({"prompt_tokens": 0, "completion_tokens": 0}, False),
        ({"prompt_tokens": 5}, True),
        ({"total_tokens": 15}, True),
        ({"completion_tokens": 3}, True),
        ("not-a-dict", False),
        ({"prompt_tokens": True}, False),  # bool is not a positive token count
        ({"total_tokens": 15.0}, False),  # float is not int → not a token count
        ({"prompt_tokens": -1}, False),  # negative is not strictly positive
        ({"prompt_tokens": True, "completion_tokens": 5}, True),  # bool ignored, real int saves it
    ],
)
def test_su7_stream_usage_is_complete_table(usage: Any, expected: bool) -> None:
    assert stream_usage_is_complete(usage) is expected, f"usage={usage!r}"
