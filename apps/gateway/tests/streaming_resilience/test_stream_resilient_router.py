"""RED suite — FallbackModelRouter.stream_resilient (v19 task 3 §3-B).

Alias path: pre-first-byte fallover across model-group candidates + a stream_fallover
metric. Plain path: a single attempt (no same-target retry — the documented boundary).
The existing sync stream() must remain byte-identical (delegates to first candidate).
Fails RED until the ctor flag + stream_resilient method are built.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    A0,
    ALIAS,
    B0,
    B1,
    CAND_A,
    CAND_B,
    PLAIN,
    PlanStreamUpstream,
    fallover_counter,
    make_metrics,
    make_payload,
)

try:
    from gateway.proxy.application.fallback_router import FallbackModelRouter

    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False


def _make_router(
    upstream: PlanStreamUpstream,
    *,
    model_groups: dict[str, list[str]] | None = None,
    metrics_registry: Any = None,
) -> Any:
    if not _ROUTER_AVAILABLE:
        pytest.fail("RED: FallbackModelRouter import failed — build pending")
    try:
        return FallbackModelRouter(
            upstream=upstream,
            model_groups=model_groups or {},
            metrics_registry=metrics_registry,
            stream_resilience_enabled=True,
        )
    except TypeError:
        pytest.fail(
            "RED: FallbackModelRouter has no stream_resilience_enabled kwarg — build pending"
        )


async def _stream_resilient(
    router: Any, payload: dict[str, Any], upstream: PlanStreamUpstream
) -> tuple[bytes | None, AsyncIterator[bytes]]:
    fn = getattr(router, "stream_resilient", None)
    if fn is None:
        pytest.fail("RED: FallbackModelRouter.stream_resilient not yet implemented — build pending")
    return await fn(payload, upstream=upstream)


async def _drain(gen: AsyncIterator[bytes]) -> list[bytes]:
    return [c async for c in gen]


async def test_alias_fallover_serves_second_candidate_and_increments_metric() -> None:
    up = PlanStreamUpstream({CAND_A: [UpstreamUnavailableError("down")], CAND_B: [B0, B1]})
    metrics = make_metrics()
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]}, metrics_registry=metrics)
    first, rest = await _stream_resilient(router, make_payload(ALIAS), up)
    assert first == B0
    assert await _drain(rest) == [B1]
    assert up.stream_calls == [CAND_A, CAND_B]
    assert (
        fallover_counter(
            metrics, alias=ALIAS, from_model=CAND_A, to_model=CAND_B, outcome="stream_fallover"
        )
        == 1.0
    )


async def test_alias_first_candidate_serves_no_fallover() -> None:
    up = PlanStreamUpstream({CAND_A: [A0]})
    metrics = make_metrics()
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]}, metrics_registry=metrics)
    first, rest = await _stream_resilient(router, make_payload(ALIAS), up)
    assert first == A0
    assert await _drain(rest) == []
    assert up.stream_calls == [CAND_A]  # B never attempted
    assert (
        fallover_counter(
            metrics, alias=ALIAS, from_model=CAND_A, to_model=CAND_B, outcome="stream_fallover"
        )
        == 0.0
    )


async def test_plain_model_single_attempt_no_retry() -> None:
    up = PlanStreamUpstream({PLAIN: [UpstreamUnavailableError("down")]})
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]})
    with pytest.raises(UpstreamUnavailableError):
        await _stream_resilient(router, make_payload(PLAIN), up)
    assert up.stream_calls == [PLAIN]  # exactly one attempt — no same-target retry


async def test_all_candidates_fail_raises() -> None:
    up = PlanStreamUpstream(
        {CAND_A: [UpstreamUnavailableError("a")], CAND_B: [UpstreamUnavailableError("b")]}
    )
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]})
    with pytest.raises(UpstreamUnavailableError):
        await _stream_resilient(router, make_payload(ALIAS), up)
    assert up.stream_calls == [CAND_A, CAND_B]


async def test_alias_fallover_on_synchronous_circuit_open() -> None:
    # End-to-end with a fake that raises CircuitOpenError SYNCHRONOUSLY (as the real
    # BoundCircuitBreakerUpstream does): the router must still fall over to candidate B.
    from .conftest import SyncCircuitUpstream

    up = SyncCircuitUpstream(open_models={CAND_A}, plans={CAND_B: [B0]})
    metrics = make_metrics()
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]}, metrics_registry=metrics)
    first, rest = await _stream_resilient(router, make_payload(ALIAS), up)
    assert first == B0
    assert await _drain(rest) == []
    assert up.stream_calls == [CAND_A, CAND_B]
    assert (
        fallover_counter(
            metrics, alias=ALIAS, from_model=CAND_A, to_model=CAND_B, outcome="stream_fallover"
        )
        == 1.0
    )


def test_sync_stream_unchanged_resolves_to_first_candidate() -> None:
    # Even with resilience enabled, the OLD sync stream() is byte-identical: first candidate only.
    up = PlanStreamUpstream({CAND_A: [A0]})
    router = _make_router(up, model_groups={ALIAS: [CAND_A, CAND_B]})
    gen = router.stream(make_payload(ALIAS))
    assert gen is not None
    assert up.stream_calls == [CAND_A]  # delegated to the first candidate synchronously
    assert len(up.stream_calls) == 1  # B never attempted on the sync path
