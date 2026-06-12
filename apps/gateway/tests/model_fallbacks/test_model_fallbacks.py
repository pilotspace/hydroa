"""Red test suite for the model-fallbacks task.

All tests in this suite MUST fail for the right reason before BUILD:
  - FAILED (not skipped) when FallbackModelRouter does not yet exist — _make_router()
    calls pytest.fail("RED: FallbackModelRouter not yet implemented — build pending") (A3).
  - AssertionError when the existing code lacks the fallback loop / Settings fields.

Run ONLY this suite (do NOT run the global test suite):
  cd apps/gateway && uv run pytest tests/model_fallbacks/ -q --no-cov -p no:cacheprovider

F1–F10 are RED-by-design (implementation absent); zero skips, zero passes before BUILD.
F11 is GREEN-by-design (asserts ABSENCE of stream fallback) but will also FAIL red before
    BUILD because _make_router() calls pytest.fail when the module is absent (A3). Once the
    router is built with trivial stream delegation, F11 turns green (correct v6 behavior).
"""

from __future__ import annotations

import pytest

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError

from .conftest import (
    ALIAS_FAST,
    BODY_4XX,
    BODY_A,
    BODY_B,
    CANDIDATE_A,
    CANDIDATE_B,
    AlwaysAvailableGate,
    FakeUsageRecorder,
    RecordingFakeGate,
    SelectiveGate,
    SequencedFakeUpstream,
    make_payload,
)

# ---------------------------------------------------------------------------
# Import the not-yet-existing module — causes red ImportError until BUILD.
# ---------------------------------------------------------------------------
# We wrap the import in a try/except to produce a cleaner pytest-collection
# error that surfaces the reason clearly in the red report.
try:
    from gateway.proxy.application.fallback_router import FallbackModelRouter  # noqa: F401

    _ROUTER_AVAILABLE = True
except ImportError:
    _ROUTER_AVAILABLE = False

# ModelHealthGate protocol — must be added to domain/ports.py
try:
    from gateway.proxy.domain.ports import ModelHealthGate  # noqa: F401

    _GATE_AVAILABLE = True
except ImportError:
    _GATE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Helper: build a router; skip with clear message if source not yet written.
# ---------------------------------------------------------------------------


def _make_router(
    upstream: SequencedFakeUpstream,
    model_groups: dict[str, list[str]] | None = None,
    health_gate: object | None = None,
) -> object:
    """Construct FallbackModelRouter; pytest.fail (red) if module does not exist yet."""
    if not _ROUTER_AVAILABLE:
        pytest.fail("RED: FallbackModelRouter not yet implemented — build pending")
    from gateway.proxy.application.fallback_router import FallbackModelRouter

    return FallbackModelRouter(
        upstream=upstream,
        model_groups=model_groups or {ALIAS_FAST: [CANDIDATE_A, CANDIDATE_B]},
        health_gate=health_gate,
    )


# ===========================================================================
# F1 — first candidate exhausts (UpstreamUnavailableError), second serves
# ===========================================================================


@pytest.mark.asyncio
async def test_f1_first_candidate_exhausts_second_serves() -> None:
    """F1 RED: alias ["model-A", "model-B"]; A raises UpstreamUnavailableError, B returns 200.

    Expected: (200, BODY_B, "model-B") 3-tuple; exactly 2 complete() calls in order A→B.
    A2: gate.record_failure("model-A") and gate.record_success("model-B") each called once.
    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream(
        [
            UpstreamUnavailableError("A exhausted"),
            (200, BODY_B),
        ]
    )
    gate = RecordingFakeGate()
    router = _make_router(upstream, health_gate=gate)

    status, body, served_model = await router.complete(make_payload(ALIAS_FAST))

    assert status == 200
    assert body == BODY_B
    assert served_model == CANDIDATE_B, (
        f"served_model_id (3rd element) must be {CANDIDATE_B!r}, got {served_model!r}"
    )
    assert upstream.call_count == 2, (
        f"Expected exactly 2 complete() calls, got {upstream.call_count}"
    )
    # Verify order: A attempted first, then B
    assert upstream.calls[0]["model"] == CANDIDATE_A, (
        f"First call should use {CANDIDATE_A!r}, got {upstream.calls[0]['model']!r}"
    )
    assert upstream.calls[1]["model"] == CANDIDATE_B, (
        f"Second call should use {CANDIDATE_B!r}, got {upstream.calls[1]['model']!r}"
    )
    # A2: recording hooks
    assert gate.failure_calls == [CANDIDATE_A], (
        f"record_failure must be called once with {CANDIDATE_A!r}, got {gate.failure_calls!r}"
    )
    assert gate.success_calls == [CANDIDATE_B], (
        f"record_success must be called once with {CANDIDATE_B!r}, got {gate.success_calls!r}"
    )


# ===========================================================================
# F2 — first candidate serves immediately, no fallback
# ===========================================================================


@pytest.mark.asyncio
async def test_f2_first_candidate_serves_no_fallback() -> None:
    """F2 RED: alias ["model-A", "model-B"]; A returns 200 immediately.

    Expected: (200, BODY_A, "model-A") 3-tuple; exactly 1 complete() call with model-A.
    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream([(200, BODY_A)])
    router = _make_router(upstream)

    status, body, served_model = await router.complete(make_payload(ALIAS_FAST))

    assert status == 200
    assert body == BODY_A
    assert served_model == CANDIDATE_A, (
        f"served_model_id (3rd element) must be {CANDIDATE_A!r}, got {served_model!r}"
    )
    assert upstream.call_count == 1, (
        f"Expected exactly 1 complete() call, got {upstream.call_count}"
    )
    assert upstream.calls[0]["model"] == CANDIDATE_A, (
        f"Only call should use {CANDIDATE_A!r}, got {upstream.calls[0]['model']!r}"
    )


# ===========================================================================
# F3 — plain model id (not an alias) bypasses fallback, v5 pin
# ===========================================================================


@pytest.mark.asyncio
async def test_f3_plain_model_id_bypasses_router() -> None:
    """F3 RED: model="openai/gpt-4o" is not an alias → exactly 1 call, unchanged model string.

    A1: served_model_id (3rd element) must equal the original payload model string "openai/gpt-4o".
    Red reason before build: ImportError on FallbackModelRouter.
    """
    plain_model = "openai/gpt-4o"
    plain_body = {"id": "gen-plain", "model": plain_model, "choices": [], "usage": {}}
    upstream = SequencedFakeUpstream([(200, plain_body)])
    router = _make_router(
        upstream,
        model_groups={ALIAS_FAST: [CANDIDATE_A, CANDIDATE_B]},
    )

    status, body, served_model = await router.complete(make_payload(plain_model))

    assert status == 200
    assert upstream.call_count == 1, (
        f"Expected exactly 1 call for plain model id, got {upstream.call_count}"
    )
    assert upstream.calls[0]["model"] == plain_model, (
        f"Plain model id must be passed through unchanged, got {upstream.calls[0]['model']!r}"
    )
    assert served_model == plain_model, (
        f"served_model_id (3rd element) must equal original payload model string "
        f"{plain_model!r}, got {served_model!r}"
    )


# ===========================================================================
# F4 — all candidates exhaust → UpstreamUnavailableError after len(candidates) calls
# ===========================================================================


@pytest.mark.asyncio
async def test_f4_all_candidates_exhaust_raises_upstream_unavailable() -> None:
    """F4 RED: both model-A and model-B raise UpstreamUnavailableError.

    Expected: UpstreamUnavailableError after exactly 2 calls.
    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream(
        [
            UpstreamUnavailableError("A exhausted"),
            UpstreamUnavailableError("B exhausted"),
        ]
    )
    router = _make_router(upstream)

    with pytest.raises(UpstreamUnavailableError):
        await router.complete(make_payload(ALIAS_FAST))

    assert upstream.call_count == 2, (
        f"Expected exactly 2 complete() calls, got {upstream.call_count}"
    )
    assert upstream.calls[0]["model"] == CANDIDATE_A
    assert upstream.calls[1]["model"] == CANDIDATE_B


# ===========================================================================
# F5 — 4xx passthrough returned immediately, B never called
# ===========================================================================


@pytest.mark.asyncio
async def test_f5_4xx_passthrough_no_fallback() -> None:
    """F5 RED: model-A returns (400, error_body) → returned immediately; model-B never called.

    A1: 3-tuple returned; served_model_id == "model-A".
    A2: gate.record_success("model-A") called — 4xx proves the upstream path is healthy.
    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream([(400, BODY_4XX)])
    gate = RecordingFakeGate()
    router = _make_router(upstream, health_gate=gate)

    status, body, served_model = await router.complete(make_payload(ALIAS_FAST))

    assert status == 400
    assert body == BODY_4XX
    assert served_model == CANDIDATE_A, (
        f"served_model_id (3rd element) must be {CANDIDATE_A!r} on 4xx path, got {served_model!r}"
    )
    assert upstream.call_count == 1, (
        f"Expected exactly 1 call (4xx passthrough, no fallback), got {upstream.call_count}"
    )
    # Ensure model-B was never attempted
    attempted_models = [c["model"] for c in upstream.calls]
    assert CANDIDATE_B not in attempted_models, (
        f"model-B must not be called on 4xx passthrough; calls: {attempted_models}"
    )
    # A2: 4xx proves upstream path healthy → record_success called
    assert gate.success_calls == [CANDIDATE_A], (
        f"record_success must be called with {CANDIDATE_A!r} on 4xx passthrough "
        f"(upstream path proven healthy), got {gate.success_calls!r}"
    )
    assert gate.failure_calls == [], (
        f"record_failure must NOT be called on 4xx passthrough, got {gate.failure_calls!r}"
    )


# ===========================================================================
# F6 — CircuitOpenError aborts immediately, model-B never called
# ===========================================================================


@pytest.mark.asyncio
async def test_f6_circuit_open_aborts_no_fallback() -> None:
    """F6 RED: model-A raises CircuitOpenError → propagated immediately; model-B never called.

    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream([CircuitOpenError("breaker open")])
    router = _make_router(upstream)

    with pytest.raises(CircuitOpenError):
        await router.complete(make_payload(ALIAS_FAST))

    # model-B must never be called
    attempted_models = [c["model"] for c in upstream.calls]
    assert CANDIDATE_B not in attempted_models, (
        f"model-B must not be called on CircuitOpenError; calls: {attempted_models}"
    )


# ===========================================================================
# F7 — served-model billing uses the candidate that answered, not the alias
# ===========================================================================


@pytest.mark.asyncio
async def test_f7_served_model_billing_uses_candidate_not_alias() -> None:
    """F7 RED: after fallthrough A → B, served_model_id (3rd tuple element) == "model-B".

    A1: router.complete() returns (status, body, served_model_id); the use case uses
    served_model_id (3rd element) for billing — NOT body["model"] which may differ from
    the catalog candidate id (OpenRouter format variants). Body is returned untouched.

    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream(
        [
            UpstreamUnavailableError("A exhausted"),
            (200, BODY_B),  # BODY_B has model=CANDIDATE_B
        ]
    )
    router = _make_router(upstream)

    status, body, served_model = await router.complete(make_payload(ALIAS_FAST))

    assert status == 200
    # A1: served_model_id is the 3rd tuple element — the catalog candidate id we routed to
    assert served_model == CANDIDATE_B, (
        f"served_model_id (3rd element) must be {CANDIDATE_B!r} (candidate that answered), "
        f"got {served_model!r}"
    )
    assert served_model != ALIAS_FAST, f"served_model_id must NOT be the alias {ALIAS_FAST!r}"
    assert served_model != CANDIDATE_A, (
        f"served_model_id must NOT be {CANDIDATE_A!r} (the candidate that fell through)"
    )
    # Body is returned untouched — tenant-visible; body["model"] carries whatever upstream set
    assert body == BODY_B, "Response body must be returned untouched from upstream"


# ===========================================================================
# F8 — health_gate marks first candidate unavailable; gate=None tries A first
# ===========================================================================


@pytest.mark.asyncio
async def test_f8_health_gate_skips_unavailable_candidate() -> None:
    """F8 RED: gate marks model-A unavailable → A skipped without attempt; B called first.

    A2: gate.record_success("model-B") called after B returns; record_failure NOT called
    (skip is not a failure — no upstream call was made for A).
    Sub-assertion: gate=None default → A attempted first (default-off pin).
    Red reason before build: ImportError on FallbackModelRouter.
    """
    # --- Part A: health gate marks model-A unavailable ---
    upstream_with_gate = SequencedFakeUpstream([(200, BODY_B)])
    gate = RecordingFakeGate(unavailable={CANDIDATE_A})
    router_with_gate = _make_router(upstream_with_gate, health_gate=gate)

    status, body, served_model = await router_with_gate.complete(make_payload(ALIAS_FAST))

    assert status == 200
    assert served_model == CANDIDATE_B, (
        f"served_model_id (3rd element) must be {CANDIDATE_B!r}, got {served_model!r}"
    )
    assert upstream_with_gate.call_count == 1, (
        f"Expected exactly 1 call (model-A skipped by gate), got {upstream_with_gate.call_count}"
    )
    assert upstream_with_gate.calls[0]["model"] == CANDIDATE_B, (
        f"First call should use {CANDIDATE_B!r} (A gated out), "
        f"got {upstream_with_gate.calls[0]['model']!r}"
    )
    # A2: record_success called for the candidate that answered; no record_failure (skip ≠ failure)
    assert gate.success_calls == [CANDIDATE_B], (
        f"record_success must be called with {CANDIDATE_B!r}, got {gate.success_calls!r}"
    )
    assert gate.failure_calls == [], (
        f"record_failure must NOT be called when candidate is skipped by gate "
        f"(no upstream call made), got {gate.failure_calls!r}"
    )

    # --- Part B: gate=None (default) → A attempted first ---
    upstream_no_gate = SequencedFakeUpstream([(200, BODY_A)])
    router_no_gate = _make_router(upstream_no_gate, health_gate=None)

    status_ng, _, served_ng = await router_no_gate.complete(make_payload(ALIAS_FAST))

    assert status_ng == 200
    assert upstream_no_gate.call_count == 1
    assert upstream_no_gate.calls[0]["model"] == CANDIDATE_A, (
        f"With gate=None, first call should be {CANDIDATE_A!r}, "
        f"got {upstream_no_gate.calls[0]['model']!r}"
    )


# ===========================================================================
# F9 — all candidates gated unavailable → UpstreamUnavailableError, zero upstream calls
# ===========================================================================


@pytest.mark.asyncio
async def test_f9_all_candidates_gated_zero_upstream_calls() -> None:
    """F9 RED: gate marks both candidates unavailable → UpstreamUnavailableError, 0 calls.

    Red reason before build: ImportError on FallbackModelRouter.
    """
    upstream = SequencedFakeUpstream([])  # no outcomes needed — should never be called
    gate = SelectiveGate(unavailable={CANDIDATE_A, CANDIDATE_B})
    router = _make_router(upstream, health_gate=gate)

    with pytest.raises(UpstreamUnavailableError):
        await router.complete(make_payload(ALIAS_FAST))

    assert upstream.call_count == 0, (
        f"Expected zero upstream calls when all candidates are gated, got {upstream.call_count}"
    )


# ===========================================================================
# F10 — Settings validation: alias collision and empty candidate list
# ===========================================================================


def test_f10_settings_default_model_groups_empty_dict() -> None:
    """F10c RED: Settings must have a model_groups field defaulting to {}.

    Red reason before build: AttributeError — Settings has no model_groups attribute.
    Once BUILD adds `model_groups: dict[str, list[str]] = Field(default_factory=dict)`,
    this test becomes green and F10a/F10b will then exercise the validators.
    """
    from gateway.core.config import Settings

    s = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    # Field must exist (AttributeError = correct red reason before build)
    assert hasattr(s, "model_groups"), "Settings must have a 'model_groups' field"
    assert s.model_groups == {}, f"Default model_groups must be {{}}, got {s.model_groups!r}"


def test_f10_settings_validation_alias_collides_with_candidate() -> None:
    """F10a RED: alias key is also a candidate id → ValidationError at Settings construction.

    Two-phase red:
      Phase 1 (pre-field): model_groups is not a Settings field; pydantic ignores the
        kwarg (extra="ignore"); assertion fails because s.model_groups does not exist.
        We assert the field exists first (AttributeError is the correct pre-build red reason).
      Phase 2 (field added, validator missing): Settings accepts the collision silently;
        test fails because ValidationError is NOT raised.
      Phase 3 (fully built): validator rejects collision → test green.

    We drive to Phase 2 red here: check field exists, then assert ValidationError is raised.
    """
    from pydantic import ValidationError

    from gateway.core.config import Settings

    # Pre-condition: field must exist before the validator can be tested.
    # If field is absent, this line raises AssertionError (correct red for phase 1).
    probe = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    assert hasattr(probe, "model_groups"), (
        "Settings.model_groups field must exist before alias-collision validator can fire"
    )

    # Now assert the validator fires on collision input.
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            jwt_secret="test-secret-not-for-production-0123456789",
            redis_url="redis://localhost:6380/9",
            environment="test",
            model_groups={CANDIDATE_A: [CANDIDATE_A, CANDIDATE_B]},
        )
    errors_str = str(exc_info.value)
    assert (
        "collision" in errors_str.lower()
        or "alias" in errors_str.lower()
        or "ALIAS_COLLIDES" in errors_str
        or "collide" in errors_str.lower()
    ), f"ValidationError should reference alias collision, got: {errors_str}"


def test_f10_settings_validation_empty_candidate_list() -> None:
    """F10b RED: empty candidate list → ValidationError at Settings construction.

    Same two-phase progression as F10a. See that docstring for red-phase description.
    """
    from pydantic import ValidationError

    from gateway.core.config import Settings

    # Pre-condition: field must exist.
    probe = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    assert hasattr(probe, "model_groups"), (
        "Settings.model_groups field must exist before empty-list validator can fire"
    )

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            jwt_secret="test-secret-not-for-production-0123456789",
            redis_url="redis://localhost:6380/9",
            environment="test",
            model_groups={ALIAS_FAST: []},
        )
    errors_str = str(exc_info.value)
    assert (
        "empty" in errors_str.lower()
        or "non-empty" in errors_str.lower()
        or "EMPTY_CANDIDATE" in errors_str
        or "at least" in errors_str.lower()
    ), f"ValidationError should reference empty candidate list, got: {errors_str}"


def test_f10_settings_validation_too_many_candidates() -> None:
    """F10d RED (A4): candidate list longer than 5 → ValidationError at Settings construction.

    Caps the per-request catalog-validation cost: the entry check validates EVERY
    candidate of an alias (§3 CATALOG INTERACTION), so groups are bounded at 5.
    Same two-phase progression as F10a. See that docstring for red-phase description.
    """
    from pydantic import ValidationError

    from gateway.core.config import Settings

    # Pre-condition: field must exist.
    probe = Settings(
        database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        jwt_secret="test-secret-not-for-production-0123456789",
        redis_url="redis://localhost:6380/9",
        environment="test",
    )
    assert hasattr(probe, "model_groups"), (
        "Settings.model_groups field must exist before candidate-cap validator can fire"
    )

    with pytest.raises(ValidationError) as exc_info:
        Settings(
            database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
            jwt_secret="test-secret-not-for-production-0123456789",
            redis_url="redis://localhost:6380/9",
            environment="test",
            model_groups={ALIAS_FAST: [f"vendor/model-{i}" for i in range(6)]},
        )
    errors_str = str(exc_info.value)
    assert (
        "TOO_MANY_CANDIDATES" in errors_str
        or "at most" in errors_str.lower()
        or "exceed" in errors_str.lower()
        or "5" in errors_str
    ), f"ValidationError should reference the 5-candidate cap, got: {errors_str}"


# ===========================================================================
# F11 — stream() with alias → first candidate only, no fallback (GREEN-BY-DESIGN)
# ===========================================================================


def test_f11_stream_resolves_to_first_candidate_no_fallback() -> None:
    """F11 GREEN-BY-DESIGN: stream() with alias resolves to first candidate only.

    Asserts the ABSENCE of stream fallback — correct v6 behavior.
    This test should pass once the router exists with a trivial stream delegation.

    On call: router.stream({"model": "fast"}) should produce a generator that
    calls upstream.stream() with model="model-A" (first candidate).
    On iteration, the fake stream raises UpstreamUnavailableError.
    model-B must NEVER be called.

    Red reason before build: ImportError on FallbackModelRouter.
    (Green once the router delegates stream to first candidate.)
    """
    upstream = SequencedFakeUpstream([])  # stream outcomes tracked separately
    router = _make_router(upstream)

    # Obtain the generator (synchronous step)
    payload = make_payload(ALIAS_FAST)
    gen = router.stream(payload)
    assert gen is not None, "router.stream() must return a generator"

    # Verify the stream call was registered with first candidate
    assert upstream.stream_call_count == 1, (
        f"Expected exactly 1 stream() call, got {upstream.stream_call_count}"
    )
    assert upstream.stream_calls[0]["model"] == CANDIDATE_A, (
        f"Stream call should use {CANDIDATE_A!r} (first candidate), "
        f"got {upstream.stream_calls[0]['model']!r}"
    )

    # model-B must never have been attempted on the stream path
    assert upstream.stream_call_count < 2, (
        "model-B must not be attempted on stream fallback (stream fallback is out-of-scope)"
    )
