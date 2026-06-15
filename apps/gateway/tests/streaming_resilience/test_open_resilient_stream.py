"""RED suite — open_resilient_stream helper (v19 task 3 §3-A).

The pre-first-byte fallover primitive: try each attempt, peek the FIRST chunk;
a pre-first-byte transport error falls over to the next attempt; the first chunk
COMMITS (later errors propagate to the drain loop — NO replay). Fails RED until
proxy/application/streaming_resilience.py is built.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError

from .conftest import A0, A1, B0, CAND_A, CAND_B, PlanStreamUpstream

try:
    from gateway.proxy.application.streaming_resilience import (  # noqa: F401
        open_resilient_stream,
    )

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


async def _open(
    upstream: PlanStreamUpstream,
    attempts: list[str],
    on_fallover: Any = None,
) -> tuple[bytes | None, AsyncIterator[bytes]]:
    if not _AVAILABLE:
        pytest.fail("RED: open_resilient_stream not yet implemented — build pending")
    from gateway.proxy.application.streaming_resilience import open_resilient_stream

    def open_stream(model_id: str) -> AsyncIterator[bytes]:
        return upstream.stream({"model": model_id})

    return await open_resilient_stream(
        attempts=attempts, open_stream=open_stream, on_fallover=on_fallover
    )


async def _drain(gen: AsyncIterator[bytes]) -> list[bytes]:
    return [c async for c in gen]


async def test_first_attempt_serves_no_fallover() -> None:
    up = PlanStreamUpstream({CAND_A: [A0, A1]})
    fallovers: list[tuple[str, str]] = []
    first, rest = await _open(up, [CAND_A], on_fallover=lambda f, t: fallovers.append((f, t)))
    assert first == A0
    assert await _drain(rest) == [A1]
    assert fallovers == []  # no fallover when the first attempt serves
    assert up.stream_calls == [CAND_A]


async def test_fallover_on_pre_first_byte_unavailable() -> None:
    up = PlanStreamUpstream({CAND_A: [UpstreamUnavailableError("down")], CAND_B: [B0, A1]})
    fallovers: list[tuple[str, str]] = []
    first, rest = await _open(
        up, [CAND_A, CAND_B], on_fallover=lambda f, t: fallovers.append((f, t))
    )
    assert first == B0  # served from the SECOND candidate
    assert await _drain(rest) == [A1]
    assert fallovers == [(CAND_A, CAND_B)]
    assert up.stream_calls == [CAND_A, CAND_B]  # both attempted, A first


async def test_circuit_open_triggers_fallover() -> None:
    up = PlanStreamUpstream({CAND_A: [CircuitOpenError("open")], CAND_B: [B0]})
    first, rest = await _open(up, [CAND_A, CAND_B])
    assert first == B0
    assert await _drain(rest) == []


async def test_all_attempts_fail_raises() -> None:
    up = PlanStreamUpstream(
        {CAND_A: [UpstreamUnavailableError("a")], CAND_B: [UpstreamUnavailableError("b")]}
    )
    with pytest.raises(UpstreamUnavailableError):
        await _open(up, [CAND_A, CAND_B])
    assert up.stream_calls == [CAND_A, CAND_B]  # every candidate tried before raising


async def test_single_attempt_pre_byte_failure_raises() -> None:
    up = PlanStreamUpstream({CAND_A: [UpstreamUnavailableError("a")]})
    with pytest.raises(UpstreamUnavailableError):
        await _open(up, [CAND_A])  # no next attempt → propagate


async def test_empty_upstream_stream_returns_none_first() -> None:
    up = PlanStreamUpstream({CAND_A: []})  # zero chunks, clean StopAsyncIteration
    first, rest = await _open(up, [CAND_A, CAND_B])
    assert first is None  # committed-empty (degenerate success, NOT a fallover)
    assert await _drain(rest) == []
    assert up.stream_calls == [CAND_A]  # B never attempted — empty is a success


async def test_committed_then_mid_stream_error_propagates_no_replay() -> None:
    # A serves the first chunk THEN fails mid-stream; B must NEVER be attempted.
    up = PlanStreamUpstream({CAND_A: [A0, UpstreamUnavailableError("mid")], CAND_B: [B0]})
    first, rest = await _open(up, [CAND_A, CAND_B])
    assert first == A0  # committed to A
    with pytest.raises(UpstreamUnavailableError):
        await _drain(rest)  # mid-stream error surfaces to the drain loop — not swallowed
    assert up.stream_calls == [CAND_A]  # no fallover after commit (no replay)


async def test_synchronous_circuit_open_falls_over() -> None:
    # The real BoundCircuitBreakerUpstream + provider guard() raise CircuitOpenError
    # SYNCHRONOUSLY at open_stream(), not on __anext__. That must still fall over.
    up = PlanStreamUpstream({CAND_B: [B0]})
    opened: list[str] = []

    def open_stream(model_id: str) -> Any:
        opened.append(model_id)
        if model_id == CAND_A:
            raise CircuitOpenError("circuit open (sync)")  # raised at the CALL, pre-__anext__
        return up.stream({"model": model_id})

    if not _AVAILABLE:
        pytest.fail("RED: open_resilient_stream not yet implemented — build pending")
    from gateway.proxy.application.streaming_resilience import open_resilient_stream

    first, rest = await open_resilient_stream(attempts=[CAND_A, CAND_B], open_stream=open_stream)
    assert first == B0  # synchronous circuit-open on A fell over to B
    assert await _drain(rest) == []
    assert opened == [CAND_A, CAND_B]


async def test_synchronous_raise_on_last_attempt_propagates() -> None:
    def open_stream(model_id: str) -> Any:
        raise CircuitOpenError("circuit open (sync)")

    if not _AVAILABLE:
        pytest.fail("RED: open_resilient_stream not yet implemented — build pending")
    from gateway.proxy.application.streaming_resilience import open_resilient_stream

    with pytest.raises(CircuitOpenError):
        await open_resilient_stream(attempts=[CAND_A], open_stream=open_stream)


async def test_fallover_callback_receives_each_skip_pair() -> None:
    up = PlanStreamUpstream(
        {
            CAND_A: [UpstreamUnavailableError("a")],
            CAND_B: [UpstreamUnavailableError("b")],
            "model-C": [B0],
        }
    )
    fallovers: list[tuple[str, str]] = []
    first, _rest = await _open(
        up, [CAND_A, CAND_B, "model-C"], on_fallover=lambda f, t: fallovers.append((f, t))
    )
    assert first == B0
    assert fallovers == [(CAND_A, CAND_B), (CAND_B, "model-C")]
