"""RED suite — per-key bandwidth pacing wired into the completion path (§2 / §4).

One test per §2 SCENARIO. RED until BUILD wires the frozen BandwidthBucket (task 1) into
CompletionUseCase:
  - __init__ gains `bandwidth_bucket` + `bandwidth_max_wait_s` (default Passthrough / 0.0).
  - stream()._wrapped paces each `async for` chunk via acquire() BEFORE yield (first peeked
    chunk UNPACED); a mid-stream BandwidthExhaustedError → ERR_BANDWIDTH_EXHAUSTED SSE frame
    + [DONE] + a usage record for the streamed prefix.
  - complete() PRE-FLIGHT acquires BEFORE the upstream call; BandwidthExhaustedError →
    ProblemError 503 + Retry-After, upstream NEVER called.

Construction mirrors tests/stream_upstream_error_frame (the v35 analog). asyncio_mode=auto.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from tests.streaming_resilience.conftest import (
    A0,
    A1,
    ALIAS,
    CAND_A,
    CAND_B,
    DONE,
    KEY_ID,
    FakeAuthenticator,
    FakeModelChecker,
    FakeUsageRecorder,
    PlanStreamUpstream,
    make_payload,
)

from gateway.core.errors import ProblemError
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthGrant

_CODE = "ERR_BANDWIDTH_EXHAUSTED"
_MAX_WAIT = 4.0


class FakeBandwidthBucket:
    """Controllable BandwidthBucket: records acquire calls; optionally raises on the Nth.

    raise_on_call=N → the Nth acquire() (1-based) raises BandwidthExhaustedError.
    None → always grants. Mirrors the frozen task-1 Protocol surface.
    """

    def __init__(self, *, raise_on_call: int | None = None) -> None:
        self.acquire_calls: list[tuple[uuid.UUID, int, float]] = []
        self._raise_on = raise_on_call

    async def acquire(
        self, key_id: uuid.UUID, estimated_tokens: int, max_wait_s: float
    ) -> BandwidthGrant:
        self.acquire_calls.append((key_id, estimated_tokens, max_wait_s))
        if self._raise_on is not None and len(self.acquire_calls) == self._raise_on:
            raise BandwidthExhaustedError(
                key_id=str(key_id), requested=estimated_tokens, retry_after_s=7
            )
        return BandwidthGrant(key_id=key_id, consumed=max(0, estimated_tokens), waited_s=0.0)

    async def try_consume(self, key_id: uuid.UUID, tokens: int) -> Any:
        raise AssertionError("try_consume not used by the pacing seam")

    async def reconcile(self, key_id: uuid.UUID, grant: Any, real_tokens: int) -> None:
        return

    async def level(self, key_id: uuid.UUID) -> int:
        return 0


def _make_uc(bucket: FakeBandwidthBucket | None = None) -> CompletionUseCase:
    kwargs: dict[str, Any] = {"stream_resilience_enabled": True}
    if bucket is not None:
        kwargs["bandwidth_bucket"] = bucket
        kwargs["bandwidth_max_wait_s"] = _MAX_WAIT
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        **kwargs,
    )


def _make_router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )


async def _drain_stream(
    uc: CompletionUseCase, upstream: PlanStreamUpstream, recorder: FakeUsageRecorder
) -> list[bytes]:
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )
    chunks: list[bytes] = []
    async for chunk in gen:
        chunks.append(chunk)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return chunks


def _error_code(chunks: list[bytes]) -> str | None:
    for c in chunks:
        if c.startswith(b"data: "):
            try:
                obj = json.loads(c[len(b"data: ") :].strip())
            except json.JSONDecodeError:
                continue
            if "error" in obj:
                return obj["error"].get("code")
    return None


# ── default-OFF stream is byte-identical ─────────────────────────────────────
async def test_default_off_stream_byte_identical() -> None:
    # No bandwidth_bucket → PassthroughBandwidthBucket default → no pacing, output unchanged.
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    uc = _make_uc()  # no bucket
    chunks = await _drain_stream(uc, upstream, recorder)
    assert chunks == [A0, A1, DONE]  # byte-identical, in order


# ── stream paces each chunk after the first (TTFB unpaced) ───────────────────
async def test_stream_paces_each_chunk_first_unpaced() -> None:
    bucket = FakeBandwidthBucket()
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    uc = _make_uc(bucket)
    chunks = await _drain_stream(uc, upstream, recorder)
    assert chunks == [A0, A1, DONE]  # content unchanged
    # resilience peek yields A0 UNPACED; A1 + DONE go through the paced async-for loop.
    assert len(bucket.acquire_calls) == 2
    for key_id, estimate, max_wait in bucket.acquire_calls:
        assert key_id == KEY_ID
        assert estimate >= 1  # max(1, len(chunk)//4)
        assert max_wait == _MAX_WAIT
    # estimate tracks chunk size (A1 ~ len//4)
    assert bucket.acquire_calls[0][1] == max(1, len(A1) // 4)


# ── bounded-wait exhausted mid-stream → terminal frame + DONE (REJECTION) ────
async def test_midstream_shed_emits_bandwidth_frame_and_done() -> None:
    bucket = FakeBandwidthBucket(raise_on_call=1)  # first paced chunk (A1) sheds
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    uc = _make_uc(bucket)
    chunks = await _drain_stream(uc, upstream, recorder)
    # A0 (TTFB) reached the client; then the shed frame; then [DONE]; A1 never forwarded.
    assert chunks[0] == A0
    assert _error_code(chunks) == _CODE
    assert chunks[-1] == DONE
    assert A1 not in chunks
    # EXACTLY one usage record for the streamed prefix (no silent truncation, no double-bill)
    assert recorder.call_count == 1


# ── disconnect DURING the shed's frame/[DONE] yields must not double-bill ─────
async def test_midstream_shed_disconnect_does_not_double_bill() -> None:
    # The shed branch fires its record, then yields the error frame + [DONE] from inside the
    # outer try body. A client drop during those yields raises GeneratorExit into the disconnect
    # handler — which must NOT bill a second time (the shed already billed). Regression guard.
    bucket = FakeBandwidthBucket(raise_on_call=1)  # A1 sheds
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    uc = _make_uc(bucket)
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )
    it = gen.__aiter__()
    first = await it.__anext__()  # A0 (TTFB, unpaced)
    assert first == A0
    frame = await it.__anext__()  # acquire raises → record fired (1) → error frame yielded
    assert _error_code([frame]) == _CODE
    # generator now suspended at the error-frame yield — inject the client disconnect here.
    await gen.aclose()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert recorder.call_count == 1  # the shed record only — the disconnect handler was gated


# ── Redis fail-open is task-1's job: the use-case only catches BandwidthExhausted ─
async def test_stream_admits_when_bucket_grants() -> None:
    # A bucket that always grants (fail-open simulated) never interrupts the stream.
    bucket = FakeBandwidthBucket()
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: [A0, A1, DONE]})
    uc = _make_uc(bucket)
    chunks = await _drain_stream(uc, upstream, recorder)
    assert chunks == [A0, A1, DONE]


# ── non-stream pre-flight acquire admits under budget ────────────────────────
async def test_nonstream_preflight_admits_and_calls_upstream() -> None:
    bucket = FakeBandwidthBucket()
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: []})
    uc = _make_uc(bucket)
    body = {**make_payload(ALIAS), "max_tokens": 100}
    status, _body, _x = await uc.complete(
        raw_key="sk-test",
        body=body,
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )
    assert status == 200
    # exactly one pre-flight acquire, keyed by the api key, estimate includes max_tokens
    assert len(bucket.acquire_calls) == 1
    key_id, estimate, max_wait = bucket.acquire_calls[0]
    assert key_id == KEY_ID
    assert estimate >= 100  # prompt//4 + max_tokens
    assert max_wait == _MAX_WAIT
    assert upstream.complete_calls == [CAND_A]  # upstream WAS called


# ── non-stream pre-flight shed → 503, upstream never called (REJECTION) ──────
async def test_nonstream_preflight_shed_503_no_upstream() -> None:
    bucket = FakeBandwidthBucket(raise_on_call=1)
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: []})
    uc = _make_uc(bucket)
    body = {**make_payload(ALIAS), "max_tokens": 100}
    with pytest.raises(ProblemError) as exc:
        await uc.complete(
            raw_key="sk-test",
            body=body,
            upstream=upstream,  # type: ignore[arg-type]
            usage_recorder=recorder,  # type: ignore[arg-type]
            model_router=_make_router(upstream),
        )
    assert exc.value.status == 503
    assert exc.value.code == _CODE
    assert exc.value.headers.get("Retry-After") == "7"
    assert upstream.complete_calls == []  # upstream NEVER paid


# ── disabled non-stream is byte-identical (no acquire) ───────────────────────
async def test_default_off_nonstream_no_acquire() -> None:
    recorder = FakeUsageRecorder()
    upstream = PlanStreamUpstream({CAND_A: []})
    uc = _make_uc()  # no bucket → Passthrough
    status, _body, _x = await uc.complete(
        raw_key="sk-test",
        body={**make_payload(ALIAS), "max_tokens": 100},
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=_make_router(upstream),
    )
    assert status == 200
    assert upstream.complete_calls == [CAND_A]
