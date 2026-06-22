"""Red suite for openrouter-cost-recovery-wiring (v30 t6.2c) — TASK.md §4.

Wire OpenRouterCostRecoveryService.recover() as a fire-and-forget task from the stream
disconnect handler: only when the service is wired, a generation id was captured, AND the
disconnected stream's provider is openrouter. Never blocks or masks the disconnect re-raise.
Default-OFF knob ⇒ byte-identical when the service is unwired.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.streaming_resilience.conftest import (
    ALIAS,
    CAND_A,
    CAND_B,
    FakeAuthenticator,
    FakeModelChecker,
    PlanStreamUpstream,
    make_payload,
)

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.application.use_cases import CompletionUseCase

pytestmark = pytest.mark.asyncio

_ID_CHUNK = b'data: {"id":"gen-xyz","choices":[{"delta":{"content":"hi"}}]}\n\n'
_NO_ID_CHUNK = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'


async def _settle() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


class _SpyRecorder:
    supported_extras: frozenset[str] = frozenset(
        {"team_id", "pii_masked", "usage_source", "provider_generation_id"}
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


class _FakeProviderResolver:
    def __init__(self, provider: str) -> None:
        self._provider = provider

    async def provider_for(self, model_id: str) -> str:
        return self._provider


class _SpyRecovery:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def recover(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


class _SyncRaisingRecovery:
    """recover() raises SYNCHRONOUSLY (before returning a coroutine) — proves the
    scheduling suppress guard never lets it mask the disconnect re-raise."""

    def __init__(self) -> None:
        self.scheduled = False

    def recover(self, **kwargs: Any) -> Any:
        self.scheduled = True
        raise RuntimeError("scheduling boom")


class _AsyncRaisingRecovery:
    """recover() returns a coroutine that raises ASYNCHRONOUSLY when it runs — proves the
    scheduled task's exception is retrieved (done_callback), not left to escape as
    'Task exception was never retrieved' noise, and never masks the disconnect."""

    def __init__(self) -> None:
        self.ran = False

    async def recover(self, **kwargs: Any) -> None:
        self.ran = True
        raise RuntimeError("async recover boom")


async def _run_disconnect(
    *,
    chunks: list[bytes],
    cost_recovery: Any,
    provider: str,
    recorder: _SpyRecorder,
) -> None:
    upstream = PlanStreamUpstream({CAND_A: chunks})
    uc = CompletionUseCase(
        FakeAuthenticator(),  # type: ignore[arg-type]
        FakeModelChecker(),  # type: ignore[arg-type]
        stream_resilience_enabled=True,
        provider_resolver=_FakeProviderResolver(provider),  # type: ignore[arg-type]
        cost_recovery=cost_recovery,
    )
    router = FallbackModelRouter(
        upstream=upstream,  # type: ignore[arg-type]
        model_groups={ALIAS: [CAND_A, CAND_B]},
        stream_resilience_enabled=True,
    )
    gen = await uc.stream(
        raw_key="sk-test",
        body=make_payload(ALIAS),
        upstream=upstream,  # type: ignore[arg-type]
        usage_recorder=recorder,  # type: ignore[arg-type]
        model_router=router,
    )
    await gen.__anext__()  # consume the first chunk, leave the stream suspended
    await gen.aclose()  # client disconnect
    await _settle()


async def test_inline_recovery_scheduled_on_openrouter_disconnect() -> None:
    recorder = _SpyRecorder()
    spy = _SpyRecovery()
    await _run_disconnect(
        chunks=[_ID_CHUNK, b"more\n\n"],
        cost_recovery=spy,
        provider="openrouter",
        recorder=recorder,
    )
    assert len(spy.calls) == 1, f"want one recover() call; got {spy.calls}"
    assert spy.calls[0]["provider_generation_id"] == "gen-xyz"
    # disconnect record still fired (t6.2 behaviour unchanged)
    assert recorder.calls and recorder.calls[-1].get("usage_source") == "client_disconnect"
    # recovery is keyed to the SAME model the disconnect row billed (the requested model_id)
    assert spy.calls[0]["model"] == recorder.calls[-1]["model"]


async def test_not_scheduled_when_service_unwired() -> None:
    recorder = _SpyRecorder()
    await _run_disconnect(
        chunks=[_ID_CHUNK, b"more\n\n"],
        cost_recovery=None,
        provider="openrouter",
        recorder=recorder,
    )
    assert recorder.calls and recorder.calls[-1].get("provider_generation_id") == "gen-xyz"


async def test_not_scheduled_when_no_generation_id() -> None:
    recorder = _SpyRecorder()
    spy = _SpyRecovery()
    await _run_disconnect(
        chunks=[_NO_ID_CHUNK, b"more\n\n"],
        cost_recovery=spy,
        provider="openrouter",
        recorder=recorder,
    )
    assert spy.calls == [], f"no gen id → must not schedule; got {spy.calls}"
    assert recorder.calls and recorder.calls[-1].get("usage_source") == "client_disconnect"


async def test_not_scheduled_for_non_openrouter_provider() -> None:
    recorder = _SpyRecorder()
    spy = _SpyRecovery()
    await _run_disconnect(
        chunks=[_ID_CHUNK, b"more\n\n"],
        cost_recovery=spy,
        provider="anthropic",
        recorder=recorder,
    )
    assert spy.calls == [], f"non-openrouter → must not schedule; got {spy.calls}"
    assert recorder.calls and recorder.calls[-1].get("usage_source") == "client_disconnect"


async def test_scheduling_never_masks_disconnect() -> None:
    recorder = _SpyRecorder()
    raising = _SyncRaisingRecovery()
    # The disconnect must complete cleanly even though recover() raises synchronously.
    await _run_disconnect(
        chunks=[_ID_CHUNK, b"more\n\n"],
        cost_recovery=raising,
        provider="openrouter",
        recorder=recorder,
    )
    assert raising.scheduled, "recover() should have been attempted"
    # The disconnect record still fired → the handler ran to completion, unmasked.
    assert recorder.calls and recorder.calls[-1].get("usage_source") == "client_disconnect"


async def test_async_recover_exception_is_retrieved_not_masking(
    recwarn: pytest.WarningsRecorder,
) -> None:
    recorder = _SpyRecorder()
    raising = _AsyncRaisingRecovery()
    # The scheduled recover() coroutine runs and raises asynchronously; the disconnect
    # must still complete cleanly and the task exception must be consumed (done_callback).
    await _run_disconnect(
        chunks=[_ID_CHUNK, b"more\n\n"],
        cost_recovery=raising,
        provider="openrouter",
        recorder=recorder,
    )
    assert raising.ran, "the scheduled recover() coroutine should have run"
    assert recorder.calls and recorder.calls[-1].get("usage_source") == "client_disconnect"
    # No "coroutine was never awaited" warning — it was scheduled as a task and consumed.
    assert not any("never awaited" in str(w.message) for w in recwarn.list)


async def test_app_builds_with_recovery_disabled_by_default(app: Any) -> None:
    # No GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED in the test env → feature off.
    assert getattr(app.state, "cost_recovery_service", "MISSING") is None
