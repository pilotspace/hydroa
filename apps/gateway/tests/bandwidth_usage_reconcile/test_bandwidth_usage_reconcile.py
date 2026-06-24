"""RED suite — estimate→real bandwidth reconciliation at close (TASK.md §4, §3 FROZEN @ v1).

Task 2 paces by debiting ESTIMATED tokens (chars//4). This task reconciles that estimate to the
REAL usage at close so the bucket carries true debt:
  - STREAM clean close: reconcile(consumed=Σ paced estimate, real=usage total_tokens).
  - NON-STREAM: reconcile(consumed=pre-flight estimate, real=body usage total_tokens).
  - DISCONNECT (Tin): reconcile(consumed=estimate-so-far, real=PARTIAL usage total_tokens) when present.
  - SHED: NO reconcile (deliberately over-budget; debit final).
  - missing usage / disabled pacing: NO reconcile.

reconcile is FIRE-AND-FORGET (asyncio.ensure_future), so tests await a couple event-loop ticks
before asserting. RED until BUILD wires _fire_bandwidth_reconcile into the close paths.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from tests.streaming_resilience.conftest import (
    A0,
    A1,
    ALIAS,
    CAND_A,
    CAND_B,
    DONE,
    KEY_ID,
    USAGE_B,  # data: {"usage":{...,"total_tokens":42}}
    FakeAuthenticator,
    FakeModelChecker,
    FakeUsageRecorder,
    PlanStreamUpstream,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthGrant

_MAX_WAIT = 4.0
_USAGE_TOTAL = 42  # USAGE_B total_tokens


class RecordingBandwidthBucket:
    """Records acquire estimates AND reconcile(consumed, real) calls. raise_on_call sheds."""

    def __init__(self, *, raise_on_call: int | None = None) -> None:
        self.acquire_estimates: list[int] = []
        self.reconcile_calls: list[tuple[uuid.UUID, int, int]] = []
        self._raise_on = raise_on_call

    async def acquire(
        self, key_id: uuid.UUID, estimated_tokens: int, max_wait_s: float
    ) -> BandwidthGrant:
        self.acquire_estimates.append(estimated_tokens)
        if self._raise_on is not None and len(self.acquire_estimates) == self._raise_on:
            raise BandwidthExhaustedError(
                key_id=str(key_id), requested=estimated_tokens, retry_after_s=3
            )
        return BandwidthGrant(key_id=key_id, consumed=max(0, estimated_tokens), waited_s=0.0)

    async def try_consume(self, key_id: uuid.UUID, tokens: int) -> Any:
        raise AssertionError("try_consume not used by the reconcile seam")

    async def reconcile(self, key_id: uuid.UUID, grant: BandwidthGrant, real_tokens: int) -> None:
        self.reconcile_calls.append((key_id, grant.consumed, real_tokens))

    async def level(self, key_id: uuid.UUID) -> int:
        return 0


class _UsageCompleteUpstream(PlanStreamUpstream):
    """PlanStreamUpstream whose complete() returns a usage body (non-stream reconcile test)."""

    def __init__(self, total_tokens: int) -> None:
        super().__init__({CAND_A: []})
        self._tt = total_tokens

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(str(payload.get("model", "")))
        return 200, {"usage": {"total_tokens": self._tt}}


def _make_uc(bucket: RecordingBandwidthBucket | None = None) -> CompletionUseCase:
    kwargs: dict[str, Any] = {"stream_resilience_enabled": True}
    if bucket is not None:
        kwargs["bandwidth_bucket"] = bucket
        kwargs["bandwidth_max_wait_s"] = _MAX_WAIT
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        **kwargs,
    )


def _router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )


async def _drain(uc: CompletionUseCase, upstream: PlanStreamUpstream) -> list[bytes]:
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=FakeUsageRecorder(),  # type: ignore[arg-type]
        model_router=_router(upstream),
    )
    chunks = [c async for c in gen]
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return chunks


# ── stream clean close reconciles estimate → real (over-estimate / refund) ───
async def test_stream_reconciles_estimate_to_real() -> None:
    bucket = RecordingBandwidthBucket()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, USAGE_B, DONE]})
    uc = _make_uc(bucket)
    await _drain(uc, upstream)
    # Pin the ABSOLUTE estimates INDEPENDENTLY of the bucket's own record: the paced chunks are
    # everything after the TTFB peek (A1, USAGE_B, DONE), each charged max(1, len//4). This catches a
    # systematic estimate-scaling bug that a sum(bucket.acquire_estimates) recompute would mirror.
    expected_per_chunk = [max(1, len(c) // 4) for c in (A1, USAGE_B, DONE)]
    assert (
        bucket.acquire_estimates == expected_per_chunk
    )  # acquire charged chars//4 per paced chunk
    assert bucket.reconcile_calls == [(KEY_ID, sum(expected_per_chunk), _USAGE_TOTAL)]


# ── under-estimate is reconciled the same way (delta sign differs only) ───────
async def test_stream_underestimate_debits() -> None:
    # Force a tiny real total so estimate > real is impossible to confuse with a no-op.
    big_real = b'data: {"usage":{"total_tokens":99999}}\n\n'
    bucket = RecordingBandwidthBucket()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, big_real, DONE]})
    uc = _make_uc(bucket)
    await _drain(uc, upstream)
    expected_estimate = sum(bucket.acquire_estimates)
    assert bucket.reconcile_calls == [(KEY_ID, expected_estimate, 99999)]


# ── non-stream pre-flight estimate reconciled to body usage ──────────────────
async def test_nonstream_preflight_reconciled() -> None:
    bucket = RecordingBandwidthBucket()
    upstream = _UsageCompleteUpstream(total_tokens=100)
    uc = _make_uc(bucket)
    status, _body, _x = await uc.complete(
        raw_key="sk-test",
        body={**make_payload(ALIAS), "max_tokens": 50},
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=FakeUsageRecorder(),  # type: ignore[arg-type]
        model_router=_router(upstream),
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert status == 200
    pre_flight_estimate = bucket.acquire_estimates[0]
    assert bucket.reconcile_calls == [(KEY_ID, pre_flight_estimate, 100)]


# ── missing usage frame → no reconcile (REJECTION) ───────────────────────────
async def test_missing_usage_no_reconcile() -> None:
    bucket = RecordingBandwidthBucket()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})  # no usage frame
    uc = _make_uc(bucket)
    await _drain(uc, upstream)
    assert bucket.reconcile_calls == []


# ── mid-stream shed → no reconcile (REJECTION) ───────────────────────────────
async def test_shed_no_reconcile() -> None:
    bucket = RecordingBandwidthBucket(raise_on_call=1)  # A1 sheds
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, USAGE_B, DONE]})
    uc = _make_uc(bucket)
    await _drain(uc, upstream)
    assert bucket.reconcile_calls == []


# ── disconnect WITH partial usage → reconcile toward the partial ─────────────
async def test_disconnect_reconciles_partial() -> None:
    bucket = RecordingBandwidthBucket()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, USAGE_B, DONE]})
    uc = _make_uc(bucket)
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=FakeUsageRecorder(),  # type: ignore[arg-type]
        model_router=_router(upstream),
    )
    it = gen.__aiter__()
    await it.__anext__()  # A0 (TTFB)
    await it.__anext__()  # A1 (paced)
    await it.__anext__()  # USAGE_B (paced) — collected now carries total_tokens=42
    await gen.aclose()  # client disconnect
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    expected_estimate = sum(bucket.acquire_estimates)
    assert bucket.reconcile_calls == [(KEY_ID, expected_estimate, _USAGE_TOTAL)]


# ── disconnect with NO partial usage → no reconcile (REJECTION) ──────────────
async def test_disconnect_no_usage_no_reconcile() -> None:
    bucket = RecordingBandwidthBucket()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})  # no usage before drop
    uc = _make_uc(bucket)
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=FakeUsageRecorder(),  # type: ignore[arg-type]
        model_router=_router(upstream),
    )
    it = gen.__aiter__()
    await it.__anext__()  # A0
    await it.__anext__()  # A1 (paced) — no usage frame yet
    await gen.aclose()  # disconnect before any usage
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert bucket.reconcile_calls == []


# ── disabled (Passthrough) → no reconcile, byte-identical (REJECTION) ─────────
async def test_disabled_passthrough_no_reconcile() -> None:
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, USAGE_B, DONE]})
    uc = _make_uc()  # no bucket → Passthrough → gate false
    chunks = await _drain(uc, upstream)
    assert chunks == [A0, A1, USAGE_B, DONE]  # byte-identical, no extra behavior
