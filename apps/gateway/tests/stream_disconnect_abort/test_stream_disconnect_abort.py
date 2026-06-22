"""Red suite for disconnect-provider-cost (v30 t5) — TASK.md §4.

On a mid-stream CLIENT DISCONNECT (GeneratorExit) or request-task CANCELLATION
(CancelledError), `_wrapped()` must DETERMINISTICALLY close the upstream generator
(`await gen.aclose()`) before re-raising — so the provider connection is torn down
NOW (TCP FIN -> provider stops generating/billing) instead of being left to the
event-loop async-gen finalizer (GC). The close is best-effort: a failure during
teardown must never mask the original disconnect, and the single flagged billing
record (v28 behavior) still fires.

Fast: reuses the streaming_resilience / stream_disconnect_billing fakes — no DB,
Redis, or server. A ClosureTrackingUpstream records the GeneratorExit its generator
receives, which is the observable proof of the deterministic abort.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.stream_disconnect_billing.conftest import COMPLETE_USAGE, MarkerSpyRecorder
from tests.streaming_resilience.conftest import (
    ALIAS,
    CAND_A,
    CAND_B,
    FakeAuthenticator,
    FakeModelChecker,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase

pytestmark = pytest.mark.asyncio


class ClosureTrackingUpstream:
    """Fake CompletionUpstream whose stream generator records the GeneratorExit it
    receives on close — the observable signal that the upstream was aborted."""

    def __init__(
        self,
        plan: list[bytes],
        *,
        close_error: type[BaseException] | None = None,
    ) -> None:
        self._plan = plan
        # An exception class the adapter raises DURING teardown (close), to prove the
        # best-effort close never masks the original disconnect. None = clean close.
        self._close_error = close_error
        self.closed = False
        self.stream_calls: list[str] = []

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(str(payload.get("model", "")))
        plan = list(self._plan)
        tracker = self

        async def _gen() -> AsyncIterator[bytes]:
            try:
                for item in plan:
                    yield item
            except GeneratorExit:
                tracker.closed = True
                if tracker._close_error is not None:
                    # A misbehaving adapter that errors during teardown.
                    raise tracker._close_error("boom on close") from None
                raise

        return _gen()

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {}


async def _open(
    upstream: ClosureTrackingUpstream,
    recorder: MarkerSpyRecorder,
    *,
    use_router: bool,
) -> Any:
    """Return the live _wrapped() generator (NOT drained) for the chosen path."""
    uc = CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=use_router,
    )
    router = (
        FallbackModelRouter(
            upstream=upstream,  # type: ignore[arg-type]
            model_groups={ALIAS: [CAND_A, CAND_B]},
            stream_resilience_enabled=True,
        )
        if use_router
        else None
    )
    return await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=router,
    )


_A = b"data: a\n\n"
_B = b"data: b\n\n"
_C = b"data: c\n\n"


async def _settle() -> None:
    """Let the fire-and-forget usage record (asyncio.ensure_future) run."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Deterministic abort
# ---------------------------------------------------------------------------


async def test_disconnect_closes_upstream() -> None:
    upstream = ClosureTrackingUpstream([_A, _B, _C])
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    first = await wrapped.__anext__()  # consume one chunk, leave the gen suspended
    assert first == _A
    assert upstream.closed is False  # not yet — still streaming

    await wrapped.aclose()  # client disconnect
    await _settle()

    assert upstream.closed is True  # the upstream received its close, deterministically
    assert recorder.call_count == 1  # exactly one billing record


async def test_disconnect_closes_upstream_via_resilient_router() -> None:
    # Same guarantee on the production resilient path (gen == the underlying upstream
    # generator advanced by the peeked first chunk).
    upstream = ClosureTrackingUpstream([_A, _B, _C])
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=True)

    first = await wrapped.__anext__()
    assert first == _A
    await wrapped.aclose()
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1


async def test_cancel_closes_upstream() -> None:
    upstream = ClosureTrackingUpstream([_A, _B, _C])
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    await wrapped.__anext__()
    with pytest.raises(asyncio.CancelledError):  # cancel still propagates (not swallowed)
        await wrapped.athrow(asyncio.CancelledError)
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1


# ---------------------------------------------------------------------------
# Billing on disconnect (v28 regression — still exactly one flagged record)
# ---------------------------------------------------------------------------


async def test_frame_present_bills_real_and_closes() -> None:
    upstream = ClosureTrackingUpstream([_A, COMPLETE_USAGE, _C])
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    await wrapped.__anext__()  # _A
    await wrapped.__anext__()  # COMPLETE_USAGE — authoritative frame now in `collected`
    await wrapped.aclose()
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1
    call = recorder.last_call
    assert call["usage_source"] == "frame"
    assert call["usage"]["total_tokens"] == 15  # the REAL amount


async def test_no_frame_bills_flagged_and_closes() -> None:
    upstream = ClosureTrackingUpstream([_A, _B, _C])
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    await wrapped.__anext__()
    await wrapped.aclose()
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1
    assert recorder.last_call["usage_source"] == "client_disconnect"


# ---------------------------------------------------------------------------
# Best-effort close — teardown failure must not mask the disconnect
# ---------------------------------------------------------------------------


async def test_aclose_failure_does_not_mask_disconnect() -> None:
    upstream = ClosureTrackingUpstream([_A, _B, _C], close_error=RuntimeError)
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    await wrapped.__anext__()
    # The upstream raises during close; aclose() of the wrapped gen must still complete
    # cleanly (GeneratorExit not masked by the upstream's RuntimeError).
    await wrapped.aclose()
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1


async def test_aclose_baseexception_does_not_mask_disconnect() -> None:
    # The teardown error is a BaseException (CancelledError) — what httpx's async cleanup
    # raises when the task is being cancelled. A `suppress(Exception)` would let it ESCAPE
    # and mask the original disconnect; the close must suppress BaseException too.
    upstream = ClosureTrackingUpstream([_A, _B, _C], close_error=asyncio.CancelledError)
    recorder = MarkerSpyRecorder()
    wrapped = await _open(upstream, recorder, use_router=False)

    await wrapped.__anext__()
    # Must NOT raise CancelledError out of aclose() — the cleanup error is swallowed.
    await wrapped.aclose()
    await _settle()

    assert upstream.closed is True
    assert recorder.call_count == 1
