"""RED suite — error-aware model fallback on the FallbackModelRouter (v19 task 2 §3).

The router exists (v6); the NEW `fallback_on_error` ctor param + the error-trigger
fallover branch do not. `_make_router(...)` pytest.fail()s (clean RED) until BUILD
adds the param. Default-off, billing-accuracy, and v6-unchanged paths are asserted.

Run ONLY this suite:
  cd apps/gateway && uv run pytest tests/error_aware_fallback/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from typing import Any

import pytest

from gateway.proxy.domain.errors import UpstreamUnavailableError

from .conftest import (
    ALIAS_FAST,
    AUTH_ERROR_BODY,
    BODY_A_CONTENT_FILTER_200,
    BODY_B,
    CANDIDATE_A,
    CANDIDATE_B,
    CONTENT_FILTER_BODY,
    CTX_ERROR_BODY,
    AlwaysAvailableGate,
    RecordingFakeGate,
    SequencedFakeUpstream,
    fallback_counter,
    make_metrics,
    make_payload,
)


def _make_router(
    upstream: SequencedFakeUpstream,
    *,
    fallback_on_error: bool = True,
    model_groups: dict[str, list[str]] | None = None,
    health_gate: Any = None,
    metrics_registry: Any = None,
) -> Any:
    """Construct FallbackModelRouter with the new flag; clean RED until BUILD adds it."""
    from gateway.proxy.application.fallback_router import FallbackModelRouter

    try:
        return FallbackModelRouter(
            upstream=upstream,
            model_groups=model_groups or {ALIAS_FAST: [CANDIDATE_A, CANDIDATE_B]},
            health_gate=health_gate,
            metrics_registry=metrics_registry,
            fallback_on_error=fallback_on_error,
        )
    except TypeError as exc:
        pytest.fail(
            f"RED: FallbackModelRouter.fallback_on_error not yet implemented — build pending ({exc})"
        )


# ---------------------------------------------------------------------------
# Fallover on the two new triggers
# ---------------------------------------------------------------------------


async def test_context_window_falls_over_and_serves() -> None:
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY), (200, BODY_B)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (200, CANDIDATE_B)
    assert body == BODY_B
    assert [c["model"] for c in up.calls] == [CANDIDATE_A, CANDIDATE_B]


async def test_content_policy_falls_over_and_serves() -> None:
    up = SequencedFakeUpstream([(400, CONTENT_FILTER_BODY), (200, BODY_B)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (200, CANDIDATE_B)


async def test_exhausted_error_returns_last_4xx_not_502() -> None:
    # Both candidates return a context-window 400 — client must get the real 400, not a 502.
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY), (400, CTX_ERROR_BODY)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert status == 400
    assert body == CTX_ERROR_BODY
    assert served == CANDIDATE_B


async def test_trigger_then_upstream_unavailable_returns_last_error() -> None:
    # A trigger-4xx then a transport failure on the last candidate → return the real 4xx, not 502.
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY), UpstreamUnavailableError("boom")])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert status == 400
    assert body == CTX_ERROR_BODY
    assert served == CANDIDATE_A


# ---------------------------------------------------------------------------
# Health-gate + metrics semantics
# ---------------------------------------------------------------------------


async def test_fallen_candidate_recorded_alive() -> None:
    gate = RecordingFakeGate()
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY), (200, BODY_B)])
    router = _make_router(up, health_gate=gate)
    await router.complete(make_payload())
    assert CANDIDATE_A in gate.success_calls  # answered → ALIVE
    assert CANDIDATE_A not in gate.failure_calls  # never cooled for a request-specific 4xx


async def test_metrics_trigger_and_served() -> None:
    reg = make_metrics()
    up = SequencedFakeUpstream([(400, CONTENT_FILTER_BODY), (200, BODY_B)])
    router = _make_router(up, health_gate=AlwaysAvailableGate(), metrics_registry=reg)
    await router.complete(make_payload())
    assert (
        fallback_counter(
            reg,
            alias=ALIAS_FAST,
            from_model=CANDIDATE_A,
            to_model=CANDIDATE_B,
            outcome="content_policy",
        )
        == 1.0
    )
    assert (
        fallback_counter(
            reg, alias=ALIAS_FAST, from_model=CANDIDATE_A, to_model=CANDIDATE_B, outcome="served"
        )
        == 1.0
    )


async def test_billing_returns_only_served_tuple() -> None:
    # The router returns EXACTLY the served (200, B) tuple; the use case bills that once.
    # A's discarded 400 is never returned → never billed.
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY), (200, BODY_B)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    result = await router.complete(make_payload())
    assert result == (200, BODY_B, CANDIDATE_B)
    assert up.call_count == 2  # A WAS attempted, but its 4xx is not the returned/served result


# ---------------------------------------------------------------------------
# v6-unchanged + default-off + passthrough paths
# ---------------------------------------------------------------------------


async def test_retry_exhausted_unchanged() -> None:
    gate = RecordingFakeGate()
    up = SequencedFakeUpstream([UpstreamUnavailableError("down"), (200, BODY_B)])
    router = _make_router(up, health_gate=gate)
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (200, CANDIDATE_B)
    assert (
        CANDIDATE_A in gate.failure_calls
    )  # v6 byte-identical: record_failure on UpstreamUnavailable


async def test_default_off_passthrough_4xx() -> None:
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY)])
    router = _make_router(up, fallback_on_error=False, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (400, CANDIDATE_A)
    assert body == CTX_ERROR_BODY
    assert up.call_count == 1  # B never called — v6 byte-identical


async def test_non_classifying_4xx_passthrough() -> None:
    up = SequencedFakeUpstream([(401, AUTH_ERROR_BODY)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (401, CANDIDATE_A)
    assert up.call_count == 1


async def test_malformed_4xx_passthrough() -> None:
    up = SequencedFakeUpstream([(400, {})])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (400, CANDIDATE_A)
    assert body == {}
    assert up.call_count == 1


async def test_200_content_filter_served_not_trigger() -> None:
    up = SequencedFakeUpstream([(200, BODY_A_CONTENT_FILTER_200)])
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload())
    assert (status, served) == (200, CANDIDATE_A)
    assert body == BODY_A_CONTENT_FILTER_200
    assert up.call_count == 1  # served as-is, B never called


async def test_plain_model_id_unaffected() -> None:
    up = SequencedFakeUpstream([(400, CTX_ERROR_BODY)])
    router = _make_router(up, model_groups={}, health_gate=AlwaysAvailableGate())
    status, body, served = await router.complete(make_payload(model="vendor/x"))
    assert (status, served) == (400, "vendor/x")
    assert up.call_count == 1


async def test_stream_untouched() -> None:
    up = SequencedFakeUpstream([])  # stream() does not consume the complete() outcomes
    router = _make_router(up, health_gate=AlwaysAvailableGate())
    gen = router.stream(make_payload())
    # Drain one chunk to confirm delegation; the primary candidate was resolved.
    async for _chunk in gen:
        break
    assert up.stream_call_count == 1
    assert up.stream_calls[0]["model"] == CANDIDATE_A  # strategy primary only, no error-fallback


# ---------------------------------------------------------------------------
# Config knob
# ---------------------------------------------------------------------------


def test_settings_default_off() -> None:
    from gateway.core.config import Settings

    assert getattr(Settings(), "upstream_fallback_on_error", None) is False


def test_settings_env_enable() -> None:
    from gateway.core.config import Settings

    assert (
        getattr(Settings(upstream_fallback_on_error=True), "upstream_fallback_on_error", None)
        is True
    )
