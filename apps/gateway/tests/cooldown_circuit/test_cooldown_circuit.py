"""Red test suite for cooldown-circuit task.

All tests that use RedisCooldownGate are expected to fail at this phase because
the module does not exist yet. The import is deferred inside each test (via a
helper) so every test gets its own RED failure with a clear reason rather than
one collection-level ImportError.

C8/C8b fail because the Settings fields do not exist yet (extra='ignore' means
ValidationError is NOT raised for unknown fields).
C9 is GREEN-BY-DESIGN once the implementation exists; at this phase it also
fails with the deferred ImportError.

Run only this suite:
  cd apps/gateway && uv run pytest tests/cooldown_circuit/ -q --no-cov -p no:cacheprovider

Red/green breakdown (pre-build):
  C1  RED  — ImportError: No module named 'gateway.proxy.infrastructure.redis_cooldown_gate'
  C2  RED  — ImportError (same)
  C3  RED  — ImportError (same)
  C4  RED  — ImportError (same)
  C5  RED  — ImportError (same)
  C6  RED  — ImportError (same)
  C7  RED  — ImportError (same)
  C8  RED  — Settings.cooldown_failure_threshold field absent → ValidationError NOT raised
  C8b RED  — Settings.cooldown_ttl_s field absent → ValidationError NOT raised
  C9  RED  — ImportError (same as C1–C7)
  C10 RED  — ImportError (same as C1–C7); B1 regression: closed model never claims probes
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError

from gateway.core.config import Settings

from .conftest import FakeRedis

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MODEL_A = "openai/gpt-4o"
MODEL_B = "anthropic/claude-3-5-sonnet"

# SUPERSESSION — tenant-scoped-breaker-cooldown (R9 P0 #3), S8/A20/A31.
# The cooldown gate is now TENANT-PARTITIONED: keys are
# `gateway:cooldown:{kind}:{tenant}:{model_id}` (tenant FIRST — model_id is
# caller-controlled and may contain ":"), and every port method takes a required
# keyword-only `tenant_id`. Each assertion below is the SAME invariant re-keyed
# into one tenant's partition; none is weakened. Cross-TENANT isolation itself is
# proven separately by tests/tenant_breaker_isolation.
TENANT = "t-cooldown-suite"

THRESHOLD = 3
TTL_S = 60
WINDOW_S = 60


# ---------------------------------------------------------------------------
# Deferred import helpers — each raises ImportError clearly per test
# ---------------------------------------------------------------------------


def _import_gate_class() -> type:
    """Import RedisCooldownGate; raises ImportError if module absent."""
    from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate  # type: ignore[import]

    return RedisCooldownGate


def _make_gate(
    redis: FakeRedis,
    threshold: int = THRESHOLD,
    ttl_s: int = TTL_S,
    window_s: int = WINDOW_S,
) -> Any:
    """Build a RedisCooldownGate with a fresh isolated MetricsRegistry."""
    from prometheus_client import CollectorRegistry

    from gateway.observability.metrics import MetricsRegistry

    RedisCooldownGate = _import_gate_class()
    metrics = MetricsRegistry(registry=CollectorRegistry())
    return RedisCooldownGate(
        redis=redis,
        metrics_registry=metrics,
        threshold=threshold,
        ttl_s=ttl_s,
        window_s=window_s,
    )


def _transition_count(gate: Any, model: str, transition: str) -> float:
    """Read the cooldown transitions counter value for a model+transition label pair."""
    try:
        counter = gate._metrics.cooldown_transitions_total
        return counter.labels(model=model, transition=transition)._value.get()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# C1 — Three failures trip the cooldown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc1_three_failures_trip_cooldown(fake_redis: FakeRedis) -> None:
    """CC1: threshold=3 → 3 record_failure calls trip the cooldown.
    is_available returns False; open key present; transitions 'tripped' == 1.
    RED reason: ImportError — gateway.proxy.infrastructure.redis_cooldown_gate absent.
    """
    gate = _make_gate(fake_redis, threshold=3)

    await gate.record_failure(MODEL_A, tenant_id=TENANT)
    await gate.record_failure(MODEL_A, tenant_id=TENANT)
    await gate.record_failure(MODEL_A, tenant_id=TENANT)

    result = await gate.is_available(MODEL_A, tenant_id=TENANT)

    assert result is False, "Expected False after 3 failures at threshold=3"

    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"
    assert await fake_redis.exists(open_key) == 1, "Cooldown open key must be set after trip"

    tripped = _transition_count(gate, MODEL_A, "tripped")
    assert tripped == 1.0, f"Expected tripped=1, got {tripped}"


# ---------------------------------------------------------------------------
# C2 — Success clears counter; third failure after success does NOT trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc2_success_clears_counter_prevents_trip(fake_redis: FakeRedis) -> None:
    """CC2: 2 failures + record_success clears counter; 1 failure after that does not trip.
    RED reason: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(fake_redis, threshold=3)

    await gate.record_failure(MODEL_A, tenant_id=TENANT)
    await gate.record_failure(MODEL_A, tenant_id=TENANT)
    await gate.record_success(MODEL_A, tenant_id=TENANT)
    # Only 1 failure after reset — threshold=3 not reached
    await gate.record_failure(MODEL_A, tenant_id=TENANT)

    result = await gate.is_available(MODEL_A, tenant_id=TENANT)

    assert result is True, "Expected True: counter was cleared by record_success"

    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"
    assert await fake_redis.exists(open_key) == 0, (
        "Open key must NOT exist (threshold not re-reached)"
    )


# ---------------------------------------------------------------------------
# C3 — threshold=0 → gate disabled; zero Redis commands
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc3_threshold_zero_no_redis_traffic(fake_redis: FakeRedis) -> None:
    """CC3: threshold=0 (default off) → is_available always True; zero Redis commands.
    RED reason: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(fake_redis, threshold=0)

    results = [await gate.is_available(MODEL_A, tenant_id=TENANT) for _ in range(10)]
    for _ in range(5):
        await gate.record_failure(MODEL_A, tenant_id=TENANT)

    assert all(results), "All is_available calls must return True when disabled"
    assert len(fake_redis.command_log) == 0, (
        f"Expected zero Redis commands when threshold=0, "
        f"got {len(fake_redis.command_log)}: {fake_redis.command_log}"
    )


# ---------------------------------------------------------------------------
# C4 — Half-open: first caller claims probe token; second gets False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc4_half_open_first_caller_gets_probe_second_gets_false(
    fake_redis: FakeRedis,
) -> None:
    """CC4: open key absent (expired), half marker present (half-open window active),
    probe key absent → first is_available claims probe (True); concurrent second caller
    gets False. transitions 'probe' == 1.
    RED reason: ImportError — redis_cooldown_gate module absent.

    The half marker distinguishes HALF_OPEN from CLOSED (B1 fix): the gate only
    enters probe machinery when the half marker is present. Without it a healthy
    CLOSED model would incorrectly claim probe tokens.

    asyncio.gather submits both coroutines concurrently on the single-threaded
    event loop. Because FakeRedis.set() is synchronous within each await boundary,
    SET NX is effectively atomic: the first task to reach 'await fake_redis.set(...,nx=True)'
    wins the token.
    """
    gate = _make_gate(fake_redis, threshold=3, ttl_s=TTL_S)

    # Pre-condition: model is in half-open window
    # open key absent (TTL expired), half marker present, probe key absent
    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"
    half_key = f"gateway:cooldown:half:{TENANT}:{MODEL_A}"
    probe_key = f"gateway:cooldown:probe:{TENANT}:{MODEL_A}"
    assert await fake_redis.exists(open_key) == 0, "Precondition: open key must be absent"
    assert await fake_redis.exists(probe_key) == 0, "Precondition: probe key must be absent"
    # Arrange half-open window by setting the half marker directly
    await fake_redis.set(half_key, "1", ex=2 * TTL_S)
    assert await fake_redis.exists(half_key) == 1, "Precondition: half marker must be present"

    results = await asyncio.gather(
        gate.is_available(MODEL_A, tenant_id=TENANT),
        gate.is_available(MODEL_A, tenant_id=TENANT),
    )

    true_count = sum(1 for r in results if r is True)
    false_count = sum(1 for r in results if r is False)

    assert true_count == 1, f"Expected exactly 1 True (probe claimed), got {results}"
    assert false_count == 1, f"Expected exactly 1 False (probe blocked), got {results}"

    assert await fake_redis.exists(probe_key) == 1, "Probe key must exist after one probe claimed"

    probe_transitions = _transition_count(gate, MODEL_A, "probe")
    assert probe_transitions == 1.0, f"Expected probe transition count=1, got {probe_transitions}"


# ---------------------------------------------------------------------------
# C5 — Probe failure → immediate re-trip with full TTL; transitions "reopened"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc5_probe_failure_retrips_with_full_ttl(fake_redis: FakeRedis) -> None:
    """CC5: half-open state (half marker present, probe token set, open key absent) →
    record_failure re-trips. Open key SET with full TTL; half marker refreshed (still
    present); probe key DELeted; transitions 'reopened' == 1.
    RED reason: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(fake_redis, threshold=3, ttl_s=TTL_S)

    half_key = f"gateway:cooldown:half:{TENANT}:{MODEL_A}"
    probe_key = f"gateway:cooldown:probe:{TENANT}:{MODEL_A}"
    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"

    # Pre-state: half marker present, probe token claimed, open key absent
    await fake_redis.set(half_key, "1", ex=2 * TTL_S)
    await fake_redis.set(probe_key, "1", ex=TTL_S)
    assert await fake_redis.exists(open_key) == 0, "Precondition: open key must be absent"

    await gate.record_failure(MODEL_A, tenant_id=TENANT)

    # Open key must be re-SET with full TTL
    assert await fake_redis.exists(open_key) == 1, "Open key must be re-set after probe failure"
    open_ttl = await fake_redis.ttl(open_key)
    assert 0 < open_ttl <= TTL_S, f"Open key TTL must be in (0, {TTL_S}], got {open_ttl}"

    # Half marker must be refreshed (still present)
    assert await fake_redis.exists(half_key) == 1, (
        "Half marker must be refreshed (still present) after re-trip"
    )
    half_ttl = await fake_redis.ttl(half_key)
    assert 0 < half_ttl <= 2 * TTL_S, f"Half marker TTL must be in (0, {2 * TTL_S}], got {half_ttl}"

    # Probe key must be deleted
    assert await fake_redis.exists(probe_key) == 0, "Probe key must be DELeted after probe failure"

    reopened = _transition_count(gate, MODEL_A, "reopened")
    assert reopened == 1.0, f"Expected reopened=1, got {reopened}"


# ---------------------------------------------------------------------------
# C6 — Probe success → fully closed; all callers True; transitions "closed"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc6_probe_success_fully_closes(fake_redis: FakeRedis) -> None:
    """CC6: half-open state (half marker present, probe token set, open key absent) →
    record_success DELs probe+fails+half; is_available True for all; transitions 'closed' == 1.
    RED reason: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(fake_redis, threshold=3, ttl_s=TTL_S)

    half_key = f"gateway:cooldown:half:{TENANT}:{MODEL_A}"
    probe_key = f"gateway:cooldown:probe:{TENANT}:{MODEL_A}"
    fails_key = f"gateway:cooldown:fails:{TENANT}:{MODEL_A}"
    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"

    # Pre-state: half marker present, probe token set, residual fail counter, open key absent
    await fake_redis.set(half_key, "1", ex=2 * TTL_S)
    await fake_redis.set(probe_key, "1", ex=TTL_S)
    await fake_redis.set(fails_key, "2", ex=WINDOW_S)
    assert await fake_redis.exists(open_key) == 0, "Precondition: open key must be absent"

    await gate.record_success(MODEL_A, tenant_id=TENANT)

    assert await fake_redis.exists(probe_key) == 0, "Probe key must be DELeted after success"
    assert await fake_redis.exists(fails_key) == 0, "Fails key must be DELeted after success"
    assert await fake_redis.exists(half_key) == 0, "Half marker must be DELeted after success"

    for i in range(3):
        available = await gate.is_available(MODEL_A, tenant_id=TENANT)
        assert available is True, f"is_available call {i + 1} must return True after probe success"

    closed = _transition_count(gate, MODEL_A, "closed")
    assert closed == 1.0, f"Expected closed=1, got {closed}"


# ---------------------------------------------------------------------------
# C10 (B1 regression) — CLOSED model never claims probes; is_available read-only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc10_closed_model_no_probe_no_writes(fake_redis: FakeRedis) -> None:
    """CC10 (B1 regression): a model with NO history (no keys at all) in CLOSED state.

    Two concurrent is_available calls MUST both return True.
    The probe key MUST NOT be created.
    The half marker MUST NOT be created.
    Zero SET commands must appear in the command log (read-only check).

    This is the regression that catches the original B1 defect: before the half
    marker fix, is_available treated "open key absent" as HALF_OPEN and would
    claim a probe token, throttling healthy traffic to ~1 request per TTL.

    RED reason: ImportError — gateway.proxy.infrastructure.redis_cooldown_gate absent.
    """
    gate = _make_gate(fake_redis, threshold=3, ttl_s=TTL_S)

    # Precondition: model has absolutely no Redis history
    open_key = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"
    half_key = f"gateway:cooldown:half:{TENANT}:{MODEL_A}"
    probe_key = f"gateway:cooldown:probe:{TENANT}:{MODEL_A}"
    assert await fake_redis.exists(open_key) == 0, "Precondition: open key absent"
    assert await fake_redis.exists(half_key) == 0, "Precondition: half marker absent"
    assert await fake_redis.exists(probe_key) == 0, "Precondition: probe key absent"

    # Clear command log after the EXISTS precondition checks
    fake_redis.command_log.clear()

    results = await asyncio.gather(
        gate.is_available(MODEL_A, tenant_id=TENANT),
        gate.is_available(MODEL_A, tenant_id=TENANT),
    )

    assert all(r is True for r in results), (
        f"Both is_available calls must return True for a CLOSED model, got {results}"
    )
    assert await fake_redis.exists(probe_key) == 0, (
        "Probe key MUST NOT be created for a CLOSED (no-history) model"
    )
    assert await fake_redis.exists(half_key) == 0, (
        "Half marker MUST NOT be created by is_available on a CLOSED model"
    )
    set_commands = [cmd for cmd in fake_redis.command_log if cmd == "SET"]
    assert len(set_commands) == 0, (
        f"Zero SET commands expected for CLOSED model is_available (read-only), "
        f"got {fake_redis.command_log}"
    )


# ---------------------------------------------------------------------------
# C7 — Redis ConnectionError → fail-OPEN; record_failure no-op; WARNING logged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc7_redis_error_fail_open(
    error_redis: FakeRedis,
    log_capture: Any,
) -> None:
    """CC7: every Redis command raises ConnectionError → is_available True (fail-OPEN);
    record_failure is a no-op (no exception raised); at least one WARNING logged.
    RED reason: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(error_redis, threshold=3)

    # record_failure must be a no-op (must not raise)
    for _ in range(5):
        await gate.record_failure(MODEL_A, tenant_id=TENANT)

    # is_available must return True (fail-OPEN)
    for _ in range(3):
        result = await gate.is_available(MODEL_A, tenant_id=TENANT)
        assert result is True, "Fail-OPEN: is_available must return True when Redis is unavailable"

    # At least one WARNING must have been emitted
    assert log_capture.warning_count >= 1, (
        "Expected at least one WARNING log on Redis ConnectionError, "
        f"got {log_capture.warning_count}"
    )

    # B3: model_id MAY appear in log fields (it is a public catalog id).
    # Assert that no Redis key string or payload material leaked into the log.
    for event in log_capture.events:
        for field_value in event.values():
            if isinstance(field_value, str):
                assert "gateway:cooldown:" not in field_value, (
                    f"Redis key string must not appear in log fields, found: {field_value!r}"
                )


# ---------------------------------------------------------------------------
# C8 — Settings validation: threshold=101 → ValidationError
# ---------------------------------------------------------------------------


def test_cc8_settings_validation_threshold_101() -> None:
    """CC8: cooldown_failure_threshold=101 raises pydantic ValidationError.

    RED reason: Settings.cooldown_failure_threshold field does not exist yet.
    The current Settings has extra='ignore', so unknown fields are silently
    dropped — ValidationError is NOT raised → test fails because pytest.raises
    block does not catch any exception.
    """
    with pytest.raises(ValidationError):
        Settings(cooldown_failure_threshold=101)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# C8b — Settings validation: cooldown_ttl_s=0 → ValidationError
# ---------------------------------------------------------------------------


def test_cc8b_settings_validation_ttl_zero() -> None:
    """CC8b: cooldown_ttl_s=0 raises pydantic ValidationError (ge=1 constraint).

    RED reason: Settings.cooldown_ttl_s field does not exist yet → same as C8.
    """
    with pytest.raises(ValidationError):
        Settings(cooldown_ttl_s=0)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# C9 (GREEN-BY-DESIGN) — Two model ids cool independently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cc9_two_models_cool_independently(fake_redis: FakeRedis) -> None:
    """CC9: model A trips at threshold=2; model B has only 1 failure → still available.
    GREEN-BY-DESIGN: passes once implementation exists.
    RED reason at this phase: ImportError — redis_cooldown_gate module absent.
    """
    gate = _make_gate(fake_redis, threshold=2)

    # Trip model A
    await gate.record_failure(MODEL_A, tenant_id=TENANT)
    await gate.record_failure(MODEL_A, tenant_id=TENANT)

    # Only one failure for model B
    await gate.record_failure(MODEL_B, tenant_id=TENANT)

    result_a = await gate.is_available(MODEL_A, tenant_id=TENANT)
    result_b = await gate.is_available(MODEL_B, tenant_id=TENANT)

    assert result_a is False, (
        f"Model A must be cooled down (2 failures at threshold=2), got {result_a}"
    )
    assert result_b is True, f"Model B must be available (1 failure at threshold=2), got {result_b}"

    open_key_a = f"gateway:cooldown:open:{TENANT}:{MODEL_A}"
    open_key_b = f"gateway:cooldown:open:{TENANT}:{MODEL_B}"
    assert await fake_redis.exists(open_key_a) == 1, "Open key for model A must exist"
    assert await fake_redis.exists(open_key_b) == 0, "Open key for model B must NOT exist"
