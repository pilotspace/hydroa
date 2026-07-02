"""RED suite for stream-alias-billing (B1 revenue leak — TASK.md §4).

A STREAMING chat request through a model-group alias currently records usage keyed
on the ALIAS string (use_cases.stream sets _stream_model_id = model_id = alias and
_fire_record_with_raw(..., model=model_id, ...) at the clean-close + disconnect
sites). An alias has no pricing_snapshots row → cost resolves to $0, silently.

The complete() path already bills the served candidate (frozen F7). This suite
extends that invariant to streaming.

RED at write time: billing records `model == ALIAS` ("fast"), so every assertion
that the billed model equals the SERVED candidate fails for the right reason.

Run ONLY this suite (fakes — no DB/Redis):
  cd apps/gateway && uv run pytest tests/stream_alias_billing/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    ALIAS,
    B0,
    CAND_A,
    CAND_B,
    DONE,
    USAGE_B,
    FakeBandwidthBucket,
    FixedOrderStrategy,
    MarkerSpyRecorder,
    PlanStreamUpstream,
    billed_records,
    run_stream,
    settle,
)

# ---------------------------------------------------------------------------
# B1a — default stream() path (Ordered): bill the served candidate, not the alias
# ---------------------------------------------------------------------------


async def test_default_stream_bills_served_candidate_not_alias() -> None:
    """RED: default (Ordered) alias stream routes to CAND_A; usage must key on CAND_A."""
    up = PlanStreamUpstream({CAND_A: [B0, USAGE_B, DONE]})
    rec = MarkerSpyRecorder()

    chunks = await run_stream(up, rec, resilient=False)
    await settle()

    assert chunks == [B0, USAGE_B, DONE]
    assert up.stream_calls == [CAND_A], f"expected routing to {CAND_A!r}, got {up.stream_calls!r}"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200) usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"billed on {billed[-1]['model']!r}; must be the SERVED candidate {CAND_A!r}, "
        f"never the alias {ALIAS!r} (alias has no pricing snapshot → $0 leak)"
    )
    assert billed[-1]["model"] != ALIAS


# ---------------------------------------------------------------------------
# B1b — reordering strategy: bill the ROUTED primary, not candidates_for[0]
# ---------------------------------------------------------------------------


async def test_default_stream_bills_routed_primary_under_reordering() -> None:
    """RED + regression guard: a strategy that routes to CAND_B (not the raw-first CAND_A)
    must bill CAND_B. Proves the fix uses the CAPTURED routed id, not candidates_for(alias)[0]
    (which would wrongly yield CAND_A) and not the alias.
    """
    strategy = FixedOrderStrategy([CAND_B, CAND_A])  # routes to CAND_B
    up = PlanStreamUpstream({CAND_B: [B0, USAGE_B, DONE]})
    rec = MarkerSpyRecorder()

    await run_stream(up, rec, resilient=False, strategy=strategy)
    await settle()

    assert up.stream_calls == [CAND_B], f"expected routing to {CAND_B!r}, got {up.stream_calls!r}"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200) usage record"
    assert billed[-1]["model"] == up.stream_calls[0], (
        f"billed on {billed[-1]['model']!r}; must equal the model actually streamed "
        f"({up.stream_calls[0]!r}) — the captured routed id, not candidates_for[0] or the alias"
    )
    assert billed[-1]["model"] not in (ALIAS, CAND_A)


# ---------------------------------------------------------------------------
# B1c — resilient path: bill the COMMITTED candidate after pre-first-byte fallover
# ---------------------------------------------------------------------------


async def test_resilient_stream_bills_committed_candidate_after_fallover() -> None:
    """RED: CAND_A fails pre-first-byte → CAND_B commits; usage must key on CAND_B."""
    up = PlanStreamUpstream(
        {
            CAND_A: [UpstreamUnavailableError("A down pre-first-byte")],
            CAND_B: [B0, USAGE_B, DONE],
        }
    )
    rec = MarkerSpyRecorder()

    chunks = await run_stream(up, rec, resilient=True)
    await settle()

    assert chunks == [B0, USAGE_B, DONE]
    assert up.stream_calls == [CAND_A, CAND_B], (
        f"expected fallover {CAND_A!r}→{CAND_B!r}, got {up.stream_calls!r}"
    )

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200) usage record"
    assert billed[-1]["model"] == CAND_B, (
        f"billed on {billed[-1]['model']!r}; must be the COMMITTED candidate {CAND_B!r} "
        f"(not the failed {CAND_A!r}, not the alias {ALIAS!r})"
    )
    assert billed[-1]["model"] not in (ALIAS, CAND_A)


# ---------------------------------------------------------------------------
# B1d — mid-stream bandwidth-shed: the CHARGED truncation record bills served
# ---------------------------------------------------------------------------


async def test_bandwidth_shed_bills_served_candidate_not_alias() -> None:
    """RED: a mid-stream bandwidth shed fires a CHARGED (status=200) truncation-billing
    record for the streamed prefix (use_cases.stream ~L1830). This third charged
    _fire_record_with_raw site — beyond the clean-close + disconnect sites the contract
    enumerated — must ALSO key on the SERVED candidate (CAND_A), not the alias. The
    existing tests/stream_bandwidth_pacing shed test asserts only call_count, so this
    $0-on-alias leak was invisible.
    """
    # Ordered alias routes to CAND_A; B0 commits (TTFB, unpaced), the first PACED chunk
    # trips the bucket → shed branch bills the collected prefix.
    bucket = FakeBandwidthBucket(raise_on_call=1)
    up = PlanStreamUpstream({CAND_A: [B0, USAGE_B, DONE]})
    rec = MarkerSpyRecorder()

    chunks = await run_stream(up, rec, resilient=False, bucket=bucket)
    await settle()

    # PIN to the shed path: the terminal bandwidth frame MUST be present. Without this, a
    # future refactor that stopped the bucket from raising would clean-close and bill CAND_A
    # from the OTHER (clean-close) site — the test would stay green while no longer guarding
    # the shed site (L1830). This assertion ties the green to the shed record specifically.
    assert any(b"ERR_BANDWIDTH_EXHAUSTED" in c for c in chunks), (
        "expected the mid-stream bandwidth-shed terminal frame — the test must exercise the "
        "shed billing site, not fall through to clean-close"
    )
    assert chunks[0] == B0
    assert up.stream_calls == [CAND_A], f"expected routing to {CAND_A!r}, got {up.stream_calls!r}"

    billed = billed_records(rec)
    assert billed, "expected exactly one billed (status=200) shed usage record"
    assert billed[-1]["model"] == CAND_A, (
        f"shed billed on {billed[-1]['model']!r}; must be the SERVED candidate {CAND_A!r}, "
        f"never the alias {ALIAS!r} (alias has no pricing snapshot → $0 leak)"
    )
    assert billed[-1]["model"] != ALIAS
