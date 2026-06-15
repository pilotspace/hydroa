"""RED suite — CompletionUseCase.stream resilience wiring (v19 task 3 §3-C).

End-to-end at the use-case layer: the flag-gated peek drives candidate fallover
BEFORE StreamingResponse commits; single-bill on the served attempt only; a total
pre-first-byte failure surfaces as HTTP 502 before any byte; a mid-stream failure
keeps today's behavior (no replay); flag-off is byte-identical to today.

Fails RED until the use-case ctor flag + the flag-gated peek path are built.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from gateway.core.errors import ProblemError
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    A0,
    ALIAS,
    B0,
    CAND_A,
    CAND_B,
    DONE,
    USAGE_B,
    FakeAuthenticator,
    FakeModelChecker,
    FakeUsageRecorder,
    PlanStreamUpstream,
    make_payload,
)


def _make_use_case(*, resilience: bool) -> CompletionUseCase:
    try:
        return CompletionUseCase(
            FakeAuthenticator(),  # type: ignore[arg-type]
            FakeModelChecker(),  # type: ignore[arg-type]
            stream_resilience_enabled=resilience,
        )
    except TypeError:
        pytest.fail("RED: CompletionUseCase has no stream_resilience_enabled kwarg — build pending")


def _make_router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    try:
        return FallbackModelRouter(
            upstream=upstream,
            model_groups={ALIAS: [CAND_A, CAND_B]},
            stream_resilience_enabled=True,
        )
    except TypeError:
        pytest.fail(
            "RED: FallbackModelRouter has no stream_resilience_enabled kwarg — build pending"
        )


async def _settle() -> None:
    # Let fire-and-forget usage records run.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def _run_stream(uc: CompletionUseCase, up: PlanStreamUpstream, rec: FakeUsageRecorder) -> Any:
    return await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=up,  # type: ignore[arg-type]
        usage_recorder=rec,  # type: ignore[arg-type]
        model_router=_make_router(up),
    )


async def test_fallover_serves_second_candidate_single_bill() -> None:
    up = PlanStreamUpstream(
        {CAND_A: [UpstreamUnavailableError("down")], CAND_B: [B0, USAGE_B, DONE]}
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(resilience=True)
    gen = await _run_stream(uc, up, rec)
    chunks = [c async for c in gen]
    await _settle()
    assert chunks == [B0, USAGE_B, DONE]  # the WHOLE served stream incl. the peeked first chunk
    assert up.stream_calls == [CAND_A, CAND_B]  # A failed pre-byte, B served
    # Single-bill: exactly one usage record, status 200, for the SERVED attempt with its tokens.
    assert rec.call_count == 1
    assert rec.calls[0]["status"] == 200
    assert rec.calls[0]["usage"]["total_tokens"] == 42


async def test_all_candidates_fail_raises_502_before_response() -> None:
    up = PlanStreamUpstream(
        {CAND_A: [UpstreamUnavailableError("a")], CAND_B: [UpstreamUnavailableError("b")]}
    )
    rec = FakeUsageRecorder()
    uc = _make_use_case(resilience=True)
    with pytest.raises(ProblemError) as ei:
        await _run_stream(uc, up, rec)  # the peek raises BEFORE a generator is returned
    assert ei.value.status == 502
    assert ei.value.code == "ERR_UPSTREAM_UNAVAILABLE"
    assert up.stream_calls == [CAND_A, CAND_B]  # both attempted pre-byte


async def test_mid_stream_failure_after_first_byte_commits_no_replay() -> None:
    # A serves the first chunk then fails mid-stream; B must NEVER be attempted (committed).
    up = PlanStreamUpstream({CAND_A: [A0, UpstreamUnavailableError("mid")], CAND_B: [B0]})
    rec = FakeUsageRecorder()
    uc = _make_use_case(resilience=True)
    gen = await _run_stream(uc, up, rec)
    chunks = [c async for c in gen]
    await _settle()
    assert chunks == [A0]  # the committed prefix; stream stops at the mid-stream error
    assert up.stream_calls == [CAND_A]  # no fallover to B after commit (no replay)
    # Today's mid-stream behavior: a status=502 usage record is fired internally.
    assert any(c["status"] == 502 for c in rec.calls)


async def test_flag_off_is_byte_identical_no_fallover() -> None:
    # Resilience disabled → use case uses the OLD sync stream() path: first candidate only.
    up = PlanStreamUpstream({CAND_A: [UpstreamUnavailableError("down")], CAND_B: [B0]})
    rec = FakeUsageRecorder()
    uc = _make_use_case(resilience=False)
    gen = await _run_stream(uc, up, rec)
    chunks = [c async for c in gen]
    await _settle()
    assert chunks == []  # A fails on first iteration; today's behavior = empty stream, no fallover
    assert up.stream_calls == [CAND_A]  # B NEVER attempted with the flag off (byte-identical)
