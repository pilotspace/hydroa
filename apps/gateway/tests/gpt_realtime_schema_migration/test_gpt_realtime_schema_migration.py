"""Failing-first (red) suite for gpt-realtime-schema-migration (contract FROZEN @ v1, TASK.md §4).

One test per §2 scenario (GSM1-GSM3). GSM4 (full-suite regression) is verified at VERIFY time by
running the whole suite, not a single test here — matching the tiered-token-billing/
minimax-catalog-seed precedent for schema-touching tasks.

RED before BUILD — they fail for the right reason: pricing_snapshots/usage_records lack the new
audio_* columns -> Postgres UndefinedColumnError on INSERT.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text


# ---------------------------------------------------------------------------
# GSM1 — pricing_snapshots audio price columns are nullable, NULL by default
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gsm1_pricing_snapshot_audio_columns_null_by_default(
    app: Any, db_session: Any
) -> None:
    """A PricingSnapshotRow inserted without audio_* kwargs reads back all 3 as NULL.

    RED reason: the 3 audio_* columns don't exist yet -> Postgres error on INSERT/SELECT.
    """
    model_id = "openai/gpt-realtime-schema-test-1"
    snap_id = str(uuid.uuid4())

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-Realtime schema test 1"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:id, :m, 0.000004, 0.000016, now())"
        ),
        {"id": snap_id, "m": model_id},
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text(
                "SELECT audio_prompt_usd_per_token, audio_completion_usd_per_token,"
                " audio_cached_usd_per_token FROM pricing_snapshots WHERE id = :id"
            ),
            {"id": snap_id},
        )
    ).fetchone()

    assert row is not None
    assert row.audio_prompt_usd_per_token is None
    assert row.audio_completion_usd_per_token is None
    assert row.audio_cached_usd_per_token is None


# ---------------------------------------------------------------------------
# GSM2 — usage_records audio count columns are NOT NULL, default 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gsm2_usage_record_audio_columns_zero_by_default(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """A UsageRecordRow inserted without audio_* kwargs reads back all 3 as 0.

    RED reason: the 3 audio_* columns don't exist yet -> Postgres error on INSERT/SELECT.
    """
    record_id = str(uuid.uuid4())

    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw)"
            " VALUES (:id, :t, :k, :m, 10, 5, 0.001, 200, '{}'::jsonb)"
        ),
        {
            "id": record_id,
            "t": api_key["tenant_id"],
            "k": api_key["key_id"],
            "m": "openai/gpt-realtime-schema-test-2",
        },
    )
    await db_session.commit()

    row = (
        await db_session.execute(
            text(
                "SELECT audio_prompt_tokens, audio_completion_tokens, audio_cached_tokens"
                " FROM usage_records WHERE id = :id"
            ),
            {"id": record_id},
        )
    ).fetchone()

    assert row is not None
    assert row.audio_prompt_tokens == 0
    assert row.audio_completion_tokens == 0
    assert row.audio_cached_tokens == 0


# ---------------------------------------------------------------------------
# GSM3 — all 6 new columns round-trip exact explicit values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gsm3_audio_columns_round_trip_explicit_values(
    app: Any, db_session: Any, api_key: dict[str, str]
) -> None:
    """Explicit non-default values for all 6 new columns round-trip exactly.

    RED reason: the 6 audio_* columns don't exist yet -> Postgres error on INSERT.
    """
    model_id = "openai/gpt-realtime-schema-test-3"
    snap_id = str(uuid.uuid4())
    record_id = str(uuid.uuid4())

    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, NULL, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-Realtime schema test 3"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at,"
            "  audio_prompt_usd_per_token, audio_completion_usd_per_token, audio_cached_usd_per_token)"
            " VALUES (:id, :m, 0.000004, 0.000016, now(), 0.000032, 0.000064, 0.0000004)"
        ),
        {"id": snap_id, "m": model_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO usage_records"
            " (id, tenant_id, key_id, model_id, prompt_tokens, completion_tokens,"
            "  cost_usd, status, raw, audio_prompt_tokens, audio_completion_tokens,"
            "  audio_cached_tokens)"
            " VALUES (:id, :t, :k, :m, 10, 5, 0.001, 200, '{}'::jsonb, 300, 150, 64)"
        ),
        {"id": record_id, "t": api_key["tenant_id"], "k": api_key["key_id"], "m": model_id},
    )
    await db_session.commit()

    snap_row = (
        await db_session.execute(
            text(
                "SELECT audio_prompt_usd_per_token, audio_completion_usd_per_token,"
                " audio_cached_usd_per_token FROM pricing_snapshots WHERE id = :id"
            ),
            {"id": snap_id},
        )
    ).fetchone()
    assert snap_row is not None
    assert snap_row.audio_prompt_usd_per_token == Decimal("0.000032")
    assert snap_row.audio_completion_usd_per_token == Decimal("0.000064")
    assert snap_row.audio_cached_usd_per_token == Decimal("0.0000004")

    usage_row = (
        await db_session.execute(
            text(
                "SELECT audio_prompt_tokens, audio_completion_tokens, audio_cached_tokens"
                " FROM usage_records WHERE id = :id"
            ),
            {"id": record_id},
        )
    ).fetchone()
    assert usage_row is not None
    assert usage_row.audio_prompt_tokens == 300
    assert usage_row.audio_completion_tokens == 150
    assert usage_row.audio_cached_tokens == 64
