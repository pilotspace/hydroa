"""Suite-local fixtures for stream-alias-billing tests (B1 revenue leak).

Reuses the proven streaming_resilience + stream_usage_completeness harness (fast,
no DB/Redis/server) to drive CompletionUseCase.stream directly. The point under
test: a STREAMING request through a model-group alias must record usage keyed on
the SERVED candidate (the catalog model actually routed/committed to), not the
alias string — for both the default stream() path and the opt-in resilient path,
and for a strategy that REORDERS candidates (so the served id is not candidates[0]).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

# Proven fakes (module-level, importable across suites).
from tests.streaming_resilience.conftest import (  # noqa: F401
    ALIAS,
    B0,
    CAND_A,
    CAND_B,
    DONE,
    USAGE_B,
    FakeAuthenticator,
    FakeModelChecker,
    PlanStreamUpstream,
    make_payload,
)
from tests.stream_usage_completeness.conftest import MarkerSpyRecorder  # noqa: F401

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase
from gateway.rate_limits.domain.errors import BandwidthExhaustedError
from gateway.rate_limits.domain.ports import BandwidthGrant


class FakeBandwidthBucket:
    """Controllable BandwidthBucket: grants by default, sheds on the Nth acquire().

    Mirrors tests/stream_bandwidth_pacing's fake. `raise_on_call=N` (1-based) makes the
    Nth acquire() raise BandwidthExhaustedError — driving the mid-stream truncation-billing
    shed branch in CompletionUseCase.stream (a CHARGED status=200 record). Used here to
    prove that shed record keys on the SERVED candidate, not the alias.
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


class FixedOrderStrategy:
    """Deterministic RoutingStrategy stub: returns `order` verbatim.

    Used to simulate a non-Ordered strategy (simple-shuffle / least-busy / latency)
    that routes to a candidate OTHER than the raw group's first entry — so a fix that
    recomputes candidates_for(alias)[0] would bill the WRONG model. Must be a
    permutation of the candidate list (the router guards this).
    """

    def __init__(self, order: list[str]) -> None:
        self._order = list(order)

    def order(self, alias: str, candidates: list[str], deployments: Any) -> list[str]:
        return list(self._order)


def make_use_case(
    *, resilient: bool, bucket: FakeBandwidthBucket | None = None
) -> CompletionUseCase:
    kwargs: dict[str, Any] = {"stream_resilience_enabled": resilient}
    if bucket is not None:
        kwargs["bandwidth_bucket"] = bucket
        kwargs["bandwidth_max_wait_s"] = 4.0
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        **kwargs,
    )


def make_router(
    upstream: PlanStreamUpstream,
    *,
    resilient: bool,
    strategy: Any = None,
) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,
        model_groups={ALIAS: [CAND_A, CAND_B]},
        strategy=strategy,
        stream_resilience_enabled=resilient,
    )


async def settle() -> None:
    """Let the fire-and-forget usage-record task run before asserting on it."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


async def run_stream(
    upstream: PlanStreamUpstream,
    recorder: MarkerSpyRecorder,
    *,
    resilient: bool,
    strategy: Any = None,
    bucket: FakeBandwidthBucket | None = None,
) -> list[bytes]:
    """Drive a full alias stream through the use case; return the yielded chunks."""
    uc = make_use_case(resilient=resilient, bucket=bucket)
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=make_router(upstream, resilient=resilient, strategy=strategy),
    )
    return [chunk async for chunk in gen]


def billed_records(recorder: MarkerSpyRecorder) -> list[dict[str, Any]]:
    """The status==200 usage records captured (the billed rows)."""
    return [c for c in recorder.calls if c.get("status") == 200]
