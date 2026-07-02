"""Red suite: audio-tier pricing lookup + cost math (TASK.md §2, scenarios 5-8).

RED before BUILD:
  * PU/T5: `_fetch_latest_pricing` returns an 8-tuple; the FakeSession here returns an
    11-value row → `_record_internal`'s unpack (still 8 names) raises ValueError.
  * T6/T7: `compute_per_token_cost_usd` has no audio_* kwargs yet → TypeError.
  * T8: `_record_internal` has no audio_*_tokens reads yet → event has no such keys.

Mirrors tests/tiered_token_billing/test_tiered_token_billing.py's style: drive
RecordingUsageRecorder.record() through FakeSession + StreamCapture, assert on the
captured Redis Stream event_fields; call compute_per_token_cost_usd directly for
pure-function cases (mirrors tests/prompt_cache_passthrough's style).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from tests.gpt_realtime_relay_billing.conftest import FakeSession, FakeSessionFactory, StreamCapture


# ---------------------------------------------------------------------------
# Scenario 5 — Pricing lookup includes the 3 audio tiers
# ---------------------------------------------------------------------------


async def test_pricing_lookup_includes_audio_tiers_for_realtime_model(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.000004"),
        completion_price=Decimal("0.000016"),
        audio_prompt_price=Decimal("0.000032"),
        audio_completion_price=Decimal("0.000064"),
        audio_cached_price=Decimal("0.0000004"),
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-realtime",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "input_token_details": {"audio_tokens": 80, "cached_tokens": 0},
            "output_token_details": {"audio_tokens": 40},
        },
        status=200,
    )

    evt = stream.last_event
    # Audio-tier prices were actually consumed (cost > the text-only-rate cost would be).
    text_only = Decimal("100") * Decimal("0.000004") + Decimal("50") * Decimal("0.000016")
    assert Decimal(evt["cost_usd"]) > text_only, "audio-tier price must contribute to cost"


# ---------------------------------------------------------------------------
# Scenario 6 — Cost math is additive and byte-identical for non-realtime models
# ---------------------------------------------------------------------------


def test_compute_cost_byte_identical_when_audio_args_default() -> None:
    from gateway.usage.application.recorder import compute_per_token_cost_usd  # type: ignore[import]

    prompt_price = Decimal("0.0000025")
    completion_price = Decimal("0.00001")

    # Call WITHOUT any audio kwargs — must match the pre-task flat-path expression exactly.
    cost = compute_per_token_cost_usd(
        prompt_tokens=1000,
        completion_tokens=200,
        cached_tokens=0,
        reasoning_tokens=0,
        prompt_price=prompt_price,
        completion_price=completion_price,
        cached_price=None,
        reasoning_price=None,
        markup_pct=Decimal("0"),
    )
    expected = Decimal("1000") * prompt_price + Decimal("200") * completion_price
    assert cost == expected, (
        f"non-realtime cost must be byte-identical: got {cost}, want {expected}"
    )


# ---------------------------------------------------------------------------
# Scenario 7 — Cost math includes audio tiers for a realtime turn
# ---------------------------------------------------------------------------


def test_compute_cost_includes_audio_tier_contribution() -> None:
    """audio_prompt_tokens/audio_completion_tokens are a BREAKDOWN (subset) of
    prompt_tokens/completion_tokens per OpenAI's Realtime usage.input_token_details/
    output_token_details shape — NOT additional to them. So the text-tier term only
    covers the fresh (non-audio, non-cached) portion; the audio portion is billed
    separately at the audio rate. This is the non-double-counting design surfaced as
    the top ⚠ flag at contract-freeze (TASK.md §1/§3)."""
    from gateway.usage.application.recorder import compute_per_token_cost_usd  # type: ignore[import]

    prompt_price = Decimal("0.000004")
    completion_price = Decimal("0.000016")
    audio_prompt_price = Decimal("0.000032")
    audio_completion_price = Decimal("0.000064")
    audio_cached_price = Decimal("0.0000004")

    # prompt_tokens=100 total, of which 80 are audio (10 of THOSE are audio-cached);
    # fresh (text) input = 100-80 = 20. completion_tokens=50 total, of which 40 are
    # audio; fresh (text) output = 50-40 = 10.
    cost = compute_per_token_cost_usd(
        prompt_tokens=100,
        completion_tokens=50,
        cached_tokens=0,
        reasoning_tokens=0,
        audio_prompt_tokens=80,
        audio_completion_tokens=40,
        audio_cached_tokens=10,
        prompt_price=prompt_price,
        completion_price=completion_price,
        cached_price=None,
        reasoning_price=None,
        audio_prompt_price=audio_prompt_price,
        audio_completion_price=audio_completion_price,
        audio_cached_price=audio_cached_price,
        markup_pct=Decimal("0"),
    )

    fresh_text_in = Decimal("20") * prompt_price  # 100 - 80 audio
    fresh_text_out = Decimal("10") * completion_price  # 50 - 40 audio
    fresh_audio_in = Decimal("70") * audio_prompt_price  # 80 audio - 10 audio-cached
    audio_cached = Decimal("10") * audio_cached_price
    audio_out = Decimal("40") * audio_completion_price
    expected = fresh_text_in + fresh_text_out + fresh_audio_in + audio_cached + audio_out
    assert cost == expected, f"audio-tier math mismatch: got {cost}, want {expected}"


# ---------------------------------------------------------------------------
# Scenario 8 — Usage is decomposed into the 6 existing token-count columns
# ---------------------------------------------------------------------------


async def test_usage_decomposed_into_six_token_buckets(
    snapshot_id: uuid.UUID, tenant_id: uuid.UUID, key_id: uuid.UUID
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder  # type: ignore[import]

    session = FakeSession(
        snapshot_id=snapshot_id,
        prompt_price=Decimal("0.000004"),
        completion_price=Decimal("0.000016"),
        audio_prompt_price=Decimal("0.000032"),
        audio_completion_price=Decimal("0.000064"),
        audio_cached_price=Decimal("0.0000004"),
        markup_pct=Decimal("0"),
    )
    stream = StreamCapture()
    recorder = RecordingUsageRecorder(redis=stream, session_factory=FakeSessionFactory(session))

    await recorder.record(
        tenant_id=tenant_id,
        key_id=key_id,
        model="gpt-realtime",
        usage={
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 5},
            "input_token_details": {"audio_tokens": 80, "cached_tokens": 5},
            "output_token_details": {"audio_tokens": 40},
        },
        status=200,
    )

    evt = stream.last_event
    assert evt["prompt_tokens"] == "100"
    assert evt["completion_tokens"] == "50"
    assert evt.get("cached_tokens") == "5"
    assert evt.get("audio_prompt_tokens") == "80", (
        f"expected audio_prompt_tokens=80 in event_fields; got {evt.get('audio_prompt_tokens')!r}"
    )
    assert evt.get("audio_completion_tokens") == "40"
    assert evt.get("audio_cached_tokens") == "5"
