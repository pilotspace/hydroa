"""Suite-local fixtures for stream-disconnect-billing tests (TASK.md §4).

Reuses the streaming_resilience harness (fast, no DB/Redis/server) to drive
`CompletionUseCase.stream` directly, then simulates a CLIENT DISCONNECT by
partially draining the returned async generator and calling `gen.aclose()`
(raises GeneratorExit at the suspended yield) — or `gen.athrow(CancelledError)`
for the cancellation sibling. A MarkerSpyRecorder (declaring `usage_source` in
supported_extras, like the v27 stream_usage_completeness suite) captures the
fire-and-forget record so the new disconnect marker survives the typed filter.
"""

from __future__ import annotations

from typing import Any

# Proven streaming_resilience fakes (module-level, importable).
from tests.streaming_resilience.conftest import (
    A0,
    A1,
    ALIAS,
    CAND_A,
    CAND_B,
    DONE,
    FakeAuthenticator,
    FakeModelChecker,
    PlanStreamUpstream,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase

# A COMPLETE terminal usage frame (positive top-level counts) — mirrors v27.
COMPLETE_USAGE = b'data: {"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'

__all__ = [
    "A0",
    "A1",
    "CAND_A",
    "COMPLETE_USAGE",
    "DONE",
    "MarkerSpyRecorder",
    "PlanStreamUpstream",
    "open_stream",
]


class MarkerSpyRecorder:
    """UsageRecorder spy that DECLARES the full extras set incl. usage_source.

    Records every record() call's kwargs for billing-marker assertions.
    """

    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
            "usage_source",
        }
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "MarkerSpyRecorder: no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


def make_use_case() -> CompletionUseCase:
    return CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=True,
    )


def make_router(upstream: PlanStreamUpstream) -> FallbackModelRouter:
    return FallbackModelRouter(
        upstream=upstream,
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )


async def open_stream(upstream: PlanStreamUpstream, recorder: MarkerSpyRecorder) -> Any:
    """Return the live _wrapped() async generator (NOT drained) so a test can
    partial-consume it then aclose()/athrow() to simulate a disconnect."""
    uc = make_use_case()
    return await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=make_router(upstream),
    )
