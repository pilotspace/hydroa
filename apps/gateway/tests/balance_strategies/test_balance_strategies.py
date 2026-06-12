"""Red-first suite for the balance-strategies task (TASK.md §3 FROZEN @ v1).

Adds two load-aware routing strategies to the v8 routing-strategy seam:
  - LeastBusyStrategy     — async aorder() picks fewest-in-flight primary; sync order() = declared.
  - LatencyStrategy       — async aorder() picks lowest-EWMA-latency primary; unseen = 0.0.
  - DeploymentLoadGate    — @runtime_checkable Protocol in gateway.proxy.domain.ports.
  - RedisDeploymentLoadGate — Redis-backed implementation in gateway.proxy.infrastructure.
  - build_strategy()      — extended: "least-busy" / "latency" map to load-aware strategies.
  - AsyncRoutingStrategy  — async capability Protocol; fallback_router.complete() prefers aorder.
  - load_gate kwarg       — additive on FallbackModelRouter; None → v6 byte-identical.
  - Settings knobs        — loadbal_ewma_alpha (default 0.3), loadbal_inflight_ttl_s (default 60).

Runs RED before build:
  - `from gateway.proxy.application.routing_strategy import LeastBusyStrategy ...` → ImportError.
  - `from gateway.proxy.domain.ports import DeploymentLoadGate`                   → ImportError.
  - `from gateway.proxy.infrastructure.redis_load_gate import RedisDeploymentLoadGate` → ImportError.
  - FallbackModelRouter(load_gate=...) → TypeError (kwarg not yet present).
  - Settings(routing_strategy="least-busy") → ValidationError (not in valid set yet).
  - Settings(loadbal_ewma_alpha=...) → ValidationError / AttributeError (field absent).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.domain.errors import UpstreamUnavailableError

# ── Module-level constants ──────────────────────────────────────────────────────

ALIAS = "fast"
A, B, C = "model-A", "model-B", "model-C"
PAYLOAD: dict[str, Any] = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}

# Test-Settings kwargs — reused verbatim from test_routing_strategy (RS7 idiom).
_SETTINGS_KWARGS: dict[str, Any] = {
    "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
    "jwt_secret": "test-secret-not-for-production-0123456789",
    "redis_url": "redis://localhost:6380/9",
    "environment": "test",
}


# ── Shared response helper ─────────────────────────────────────────────────────


def _body(model: str) -> dict[str, Any]:
    return {"id": f"gen-{model}", "model": model, "choices": [], "usage": {}}


# ── Reused fakes (mirror routing_strategy idiom exactly) ──────────────────────


class _Upstream:
    """Replay a sequence of (status, body) or exceptions; record payloads."""

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self._i = 0
        self.calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        entry = self._outcomes[self._i]
        self._i += 1
        if isinstance(entry, BaseException):
            raise entry
        return entry

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"chunk"

        return _gen()


class _Gate:
    """Always-available health gate — zero cooldown interaction."""

    async def is_available(self, model_id: str) -> bool:
        return True

    async def record_failure(self, model_id: str) -> None:
        pass

    async def record_success(self, model_id: str) -> None:
        pass


def _deployment(model_id: str, weight: int) -> Any:
    from gateway.core.config import Deployment

    return Deployment(model_id=model_id, weight=weight)


# ── FakeLoadGate — self-contained deterministic stand-in for DeploymentLoadGate ──


class FakeLoadGate:
    """Scripted in-memory DeploymentLoadGate fake.

    Constructor args:
      in_flight_map: deployment_id → int  (default 0 for unseen)
      ewma_map:      deployment_id → float (default 0.0 for unseen)
      raise_on:      if True, all read methods (in_flight / latency_ewma) raise ConnectionError
                     to simulate a Redis failure (BS7 fail-OPEN scenario).

    A `calls` spy records every method invocation as (op, deployment_id) tuples
    where op ∈ {"acquire","release","in_flight","record_latency","latency_ewma"}.
    """

    def __init__(
        self,
        *,
        in_flight_map: dict[str, int] | None = None,
        ewma_map: dict[str, float] | None = None,
        raise_on: bool = False,
    ) -> None:
        self._in_flight: dict[str, int] = dict(in_flight_map or {})
        self._ewma: dict[str, float] = dict(ewma_map or {})
        self._raise = raise_on
        # Spy: list of (op, deployment_id) — ordered as calls arrive.
        self.calls: list[tuple[str, str]] = []

    async def acquire(self, deployment_id: str) -> None:
        self.calls.append(("acquire", deployment_id))
        # acquire/release errors are swallowed by the impl; we do NOT raise here.

    async def release(self, deployment_id: str) -> None:
        self.calls.append(("release", deployment_id))

    async def in_flight(self, deployment_id: str) -> int:
        self.calls.append(("in_flight", deployment_id))
        if self._raise:
            raise ConnectionError("FakeLoadGate: simulated Redis read error")
        return self._in_flight.get(deployment_id, 0)

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        self.calls.append(("record_latency", deployment_id))

    async def latency_ewma(self, deployment_id: str) -> float:
        self.calls.append(("latency_ewma", deployment_id))
        if self._raise:
            raise ConnectionError("FakeLoadGate: simulated Redis read error")
        return self._ewma.get(deployment_id, 0.0)

    # ── Convenience query helpers ──────────────────────────────────────────────

    def ops_for(self, deployment_id: str) -> list[str]:
        """Return ordered list of op names recorded for a given deployment_id."""
        return [op for (op, did) in self.calls if did == deployment_id]

    def op_names(self) -> list[str]:
        """Return ordered list of all op names (ignoring deployment_id)."""
        return [op for (op, _) in self.calls]


# ─────────────────────────────────────────────────────────────────────────────
# BS1 — least-busy picks the fewest-in-flight deployment as primary
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs1_least_busy_picks_fewest_inflight() -> None:
    """LeastBusyStrategy selects the lowest in_flight candidate as primary.

    Scenario BS1: in_flight a=5, b=1, c=3 → b must be attempted first.
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    gate = FakeLoadGate(in_flight_map={A: 5, B: 1, C: 3})
    strat = LeastBusyStrategy(load_gate=gate)
    up = _Upstream([(200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1), _deployment(C, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    _status, _body_, served = await router.complete(dict(PAYLOAD))

    # Primary attempt must be B (fewest in-flight), not A (declared-first).
    assert served == B, f"expected B (fewest in-flight) but got {served}"
    assert up.calls[0]["model"] == B

    # The full permutation covers all candidates (BS1 permutation guard).
    aorder_result = await strat.aorder(
        ALIAS, [A, B, C], [_deployment(A, 1), _deployment(B, 1), _deployment(C, 1)]
    )
    assert sorted(aorder_result) == sorted([A, B, C]), "aorder must return a full permutation"
    assert aorder_result[0] == B, "aorder primary must be B"


# ─────────────────────────────────────────────────────────────────────────────
# BS2 — least-busy tie breaks by declared order (deterministic, no RNG)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs2_least_busy_tie_declared_order() -> None:
    """Tie in in_flight → declared-first candidate wins; no randomness.

    Scenario BS2: all in_flight == 2 → primary is ALWAYS a (declared-first).
    Running 50 iterations confirms full determinism.
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    gate = FakeLoadGate(in_flight_map={A: 2, B: 2, C: 2})
    strat = LeastBusyStrategy(load_gate=gate)
    deployments = [_deployment(A, 1), _deployment(B, 1), _deployment(C, 1)]

    primaries: list[str] = []
    for _ in range(50):
        result = await strat.aorder(ALIAS, [A, B, C], deployments)
        primaries.append(result[0])

    assert all(p == A for p in primaries), (
        f"tie-breaking must always produce declared-first (A); got {set(primaries)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BS3 — least-busy acquires/releases in-flight around every attempt
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs3_least_busy_acquire_release_around_attempts() -> None:
    """acquire→<upstream>→release fires for each attempt; no leak on fallthrough.

    Scenario BS3: a fails (UpstreamUnavailableError), b answers 200.
    Expected call sequence per deployment: acquire(a), release(a), acquire(b), release(b).
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    # a has fewest in-flight so it is tried first; it fails; b is tried second.
    gate = FakeLoadGate(in_flight_map={A: 0, B: 1})
    strat = LeastBusyStrategy(load_gate=gate)
    up = _Upstream([UpstreamUnavailableError("a down"), (200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    _status, _body_, served = await router.complete(dict(PAYLOAD))

    assert served == B, f"expected B to serve after A fails; got {served}"
    # Each attempted candidate must be acquired then released exactly once and BALANCED
    # (acquire count == release count) — proving no in-flight leak on the fallthrough path.
    # ops_for() also contains the selection-time in_flight read and (for B) record_latency;
    # we assert on the lifecycle ops only, and that acquire precedes release per deployment.
    for dep in (A, B):
        ops = gate.ops_for(dep)
        assert ops.count("acquire") == 1, f"{dep} acquire count: {ops}"
        assert ops.count("release") == 1, f"{dep} release count (no leak): {ops}"
        assert ops.index("acquire") < ops.index("release"), (
            f"{dep} must acquire BEFORE release; ops: {ops}"
        )
    # A failed (UpstreamUnavailableError) → no latency recorded; B answered → latency recorded.
    assert "record_latency" not in gate.ops_for(A), "no latency on a failed attempt"
    assert "record_latency" in gate.ops_for(B), "latency recorded after B answers"


# ─────────────────────────────────────────────────────────────────────────────
# BS4 — least-busy releases in-flight even when all candidates exhaust (raise path)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs4_least_busy_release_on_exhausted_raise() -> None:
    """release() is called for every candidate even when all raise (no counter leak).

    Scenario BS4: a and b both raise UpstreamUnavailableError.
    Router must re-raise UpstreamUnavailableError AND release must have been called
    for both a and b (the finally on the raise path).
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    gate = FakeLoadGate(in_flight_map={A: 0, B: 1})
    strat = LeastBusyStrategy(load_gate=gate)
    up = _Upstream(
        [
            UpstreamUnavailableError("a down"),
            UpstreamUnavailableError("b down"),
        ]
    )
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    with pytest.raises(UpstreamUnavailableError):
        await router.complete(dict(PAYLOAD))

    # release must have been called for both A and B on the exhausted-raise path.
    assert "release" in gate.ops_for(A), f"A missing release; ops: {gate.ops_for(A)}"
    assert "release" in gate.ops_for(B), f"B missing release; ops: {gate.ops_for(B)}"


# ─────────────────────────────────────────────────────────────────────────────
# BS5 — latency picks the lowest-EWMA deployment as primary
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs5_latency_picks_lowest_ewma() -> None:
    """LatencyStrategy selects lowest-EWMA candidate as primary.

    Scenario BS5: ewma a=400ms, b=120ms → b must be attempted first;
    record_latency(b, _) is called after b answers.
    """
    from gateway.proxy.application.routing_strategy import LatencyStrategy

    gate = FakeLoadGate(ewma_map={A: 400.0, B: 120.0})
    strat = LatencyStrategy(load_gate=gate)
    up = _Upstream([(200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    _status, _body_, served = await router.complete(dict(PAYLOAD))

    assert served == B, f"expected B (lowest EWMA) but got {served}"
    assert up.calls[0]["model"] == B

    # record_latency(B, ...) must be called after B answers.
    assert "record_latency" in gate.ops_for(B), (
        f"record_latency not recorded for B; ops: {gate.ops_for(B)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BS6 — latency probes an unseen deployment first (ewma 0.0 = preferred)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs6_latency_probes_unseen_first() -> None:
    """Never-seen deployment (ewma 0.0) is selected as primary (cold-start probe).

    Scenario BS6: a has ewma=250ms; b is unseen (ewma 0.0) → b wins.
    """
    from gateway.proxy.application.routing_strategy import LatencyStrategy

    # B has no entry in ewma_map → defaults to 0.0 (unseen).
    gate = FakeLoadGate(ewma_map={A: 250.0})
    strat = LatencyStrategy(load_gate=gate)
    deployments = [_deployment(A, 1), _deployment(B, 1)]

    result = await strat.aorder(ALIAS, [A, B], deployments)

    assert result[0] == B, (
        f"unseen deployment B (ewma 0.0) must be preferred over A (ewma 250ms); got {result[0]}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BS7 — load_gate Redis error fails OPEN (degrades to declared order, never 500)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs7_load_gate_redis_error_fails_open() -> None:
    """A Redis read error in in_flight() must fail OPEN (no exception; declared order).

    Scenario BS7: gate.in_flight() raises ConnectionError → treated as 0 for all
    → primary degrades to declared-first (A) → request is served normally (no 500).
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    gate = FakeLoadGate(raise_on=True)  # all reads raise ConnectionError
    strat = LeastBusyStrategy(load_gate=gate)
    up = _Upstream([(200, _body(A))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    # Must NOT raise — fail-OPEN means degrade to declared order, still serve.
    _status, _body_, served = await router.complete(dict(PAYLOAD))

    assert served == A, f"Redis error must degrade to declared-first (A); got {served}"


# ─────────────────────────────────────────────────────────────────────────────
# BS8 — ordered/simple-shuffle stay byte-identical (no load_gate, zero new IO)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs8_ordered_byte_identical_no_loadgate() -> None:
    """OrderedStrategy with load_gate=None produces zero load-gate calls.

    Scenario BS8: no load_gate wired → acquire/release/record_latency never called;
    behavior byte-identical to v6 (a first, gate/billing/counters unchanged).
    """
    from gateway.proxy.application.routing_strategy import OrderedStrategy

    # Use a spy gate even though it is NOT wired, to confirm it is never touched.
    spy_gate = FakeLoadGate()

    up = _Upstream([(200, _body(A))])
    # load_gate=None (the default) → v6 byte-identical path.
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        health_gate=_Gate(),
        strategy=OrderedStrategy(),
        load_gate=None,
    )
    _status, _body_, served = await router.complete(dict(PAYLOAD))
    assert served == A

    # Also verify stream uses declared primary.
    up2 = _Upstream([])
    router2 = FallbackModelRouter(
        upstream=up2,
        model_groups={ALIAS: [A, B, C]},
        strategy=OrderedStrategy(),
        load_gate=None,
    )
    gen = router2.stream(dict(PAYLOAD))
    async for _ in gen:
        break
    assert up2.stream_calls[0]["model"] == A

    # The spy gate was never touched (load_gate=None means no IO to ANY gate object).
    assert spy_gate.calls == [], (
        f"load_gate must NEVER be called when load_gate=None; got {spy_gate.calls}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BS9 — load-aware strategy on the stream path falls back to ordered primary
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs9_load_aware_stream_ordered_fallback() -> None:
    """LeastBusyStrategy.stream() uses sync order() (declared-first, no aorder/in_flight call).

    Scenario BS9: stream path is sync; aorder() and in_flight() must NOT be called.
    The stream must rewrite model=a (candidates[0] via sync order()).
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    gate = FakeLoadGate(in_flight_map={A: 5, B: 1, C: 0})
    strat = LeastBusyStrategy(load_gate=gate)
    up = _Upstream([])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        deployments={ALIAS: [_deployment(A, 1), _deployment(B, 1), _deployment(C, 1)]},
        strategy=strat,
        load_gate=gate,
    )
    gen = router.stream(dict(PAYLOAD))
    async for _ in gen:
        break

    # Stream must use declared primary (A = candidates[0]), NOT the async-chosen primary (C).
    assert up.stream_calls[0]["model"] == A, (
        f"stream must rewrite to declared-first A; got {up.stream_calls[0]['model']}"
    )
    # No in_flight or aorder calls on the stream path.
    async_ops = [op for (op, _) in gate.calls if op in ("in_flight", "latency_ewma")]
    assert async_ops == [], f"stream must not trigger any async load_gate reads; found: {async_ops}"


# ─────────────────────────────────────────────────────────────────────────────
# BS10 — unknown strategy still rejected; least-busy and latency are now valid
# ─────────────────────────────────────────────────────────────────────────────


def test_bs10_unknown_strategy_rejected_least_busy_latency_ok() -> None:
    """Extended Settings validator rejects unknown strategy; accepts least-busy / latency.

    Scenario BS10: "weighted-round-robin" → UNKNOWN_ROUTING_STRATEGY;
    "least-busy" and "latency" boot OK.
    """
    from pydantic import ValidationError

    from gateway.core.config import Settings

    # Unknown value still rejected.
    with pytest.raises(ValidationError) as exc:
        Settings(**_SETTINGS_KWARGS, routing_strategy="weighted-round-robin")
    assert "UNKNOWN_ROUTING_STRATEGY" in str(exc.value)

    # New valid values must NOT raise.
    s_lb = Settings(**_SETTINGS_KWARGS, routing_strategy="least-busy")
    assert s_lb.routing_strategy == "least-busy"

    s_lat = Settings(**_SETTINGS_KWARGS, routing_strategy="latency")
    assert s_lat.routing_strategy == "latency"


# ─────────────────────────────────────────────────────────────────────────────
# BS11 — a load-aware strategy returning a non-permutation is rejected at runtime
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs11_async_non_permutation_rejected() -> None:
    """INVALID_ROUTING_ORDER guard covers the async aorder path.

    Scenario BS11: fake AsyncRoutingStrategy whose aorder() drops a candidate;
    router must raise referencing INVALID_ROUTING_ORDER.
    """
    from gateway.proxy.application.routing_strategy import AsyncRoutingStrategy  # noqa: F401

    class _DropAsyncStrategy:
        """aorder() drops B — not a permutation of [A, B]."""

        def order(
            self,
            alias: str,
            candidates: list[str],
            deployments: list[Any],
        ) -> list[str]:
            return list(candidates)

        async def aorder(
            self,
            alias: str,
            candidates: list[str],
            deployments: list[Any],
        ) -> list[str]:
            # Returns only the first candidate — drops the rest (not a permutation).
            return candidates[:1]

    up = _Upstream([(200, _body(A))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        strategy=_DropAsyncStrategy(),
        load_gate=None,
    )
    with pytest.raises(Exception) as exc:  # noqa: B017 — message-checked below
        await router.complete(dict(PAYLOAD))
    assert "INVALID_ROUTING_ORDER" in str(exc.value), (
        f"expected INVALID_ROUTING_ORDER in error; got: {exc.value}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# BS12 — create_app wires the load_gate only for load-aware strategies
# ─────────────────────────────────────────────────────────────────────────────


def test_bs12_create_app_wires_load_gate_only_when_needed() -> None:
    """create_app wires a DeploymentLoadGate iff strategy ∈ {least-busy, latency}.

    Scenario BS12:
      - routing_strategy="least-busy"    → router._load_gate is RedisDeploymentLoadGate
                                           AND router._strategy is LeastBusyStrategy.
      - routing_strategy="simple-shuffle" → router._load_gate is None
                                           AND router._strategy is SimpleShuffleStrategy.
    Mirrors the test_rs_create_app_wires_strategy idiom from routing_strategy suite.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy, SimpleShuffleStrategy
    from gateway.proxy.infrastructure.redis_load_gate import RedisDeploymentLoadGate

    # --- least-busy branch ---
    settings_lb = Settings(**_SETTINGS_KWARGS, routing_strategy="least-busy")
    app_lb = create_app(settings_lb)
    router_lb = app_lb.state.model_router
    assert isinstance(router_lb._strategy, LeastBusyStrategy), (  # type: ignore[attr-defined]
        f"expected LeastBusyStrategy; got {type(router_lb._strategy)}"  # type: ignore[attr-defined]
    )
    assert isinstance(router_lb._load_gate, RedisDeploymentLoadGate), (  # type: ignore[attr-defined]
        f"expected RedisDeploymentLoadGate; got {type(router_lb._load_gate)}"  # type: ignore[attr-defined]
    )

    # --- simple-shuffle branch ---
    settings_ss = Settings(**_SETTINGS_KWARGS, routing_strategy="simple-shuffle")
    app_ss = create_app(settings_ss)
    router_ss = app_ss.state.model_router
    assert isinstance(router_ss._strategy, SimpleShuffleStrategy), (  # type: ignore[attr-defined]
        f"expected SimpleShuffleStrategy; got {type(router_ss._strategy)}"  # type: ignore[attr-defined]
    )
    assert router_ss._load_gate is None, (  # type: ignore[attr-defined]
        f"simple-shuffle must have load_gate=None; got {router_ss._load_gate}"  # type: ignore[attr-defined]
    )


# ─────────────────────────────────────────────────────────────────────────────
# test_bs_loadgate_inflight_roundtrip — RedisDeploymentLoadGate unit test
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bs_loadgate_inflight_roundtrip() -> None:
    """RedisDeploymentLoadGate: acquire×2 → in_flight==2; release → 1; clamp negative→0; ewma α.

    Uses a local minimal FakeRedis (extended from cooldown_circuit idiom with DECR support).
    """
    from gateway.proxy.infrastructure.redis_load_gate import RedisDeploymentLoadGate

    class _FakeRedis:
        """Minimal in-memory async Redis fake supporting GET/SET/INCR/DECR/EXPIRE."""

        def __init__(self) -> None:
            self._store: dict[str, str] = {}

        async def incr(self, key: str) -> int:
            val = int(self._store.get(key, "0")) + 1
            self._store[key] = str(val)
            return val

        async def decr(self, key: str) -> int:
            val = int(self._store.get(key, "0")) - 1
            self._store[key] = str(val)
            return val

        async def get(self, key: str) -> str | None:
            return self._store.get(key)

        async def set(
            self,
            key: str,
            value: Any,
            ex: int | None = None,
            nx: bool = False,
            **kwargs: Any,
        ) -> bool | None:
            if nx and key in self._store:
                return None
            self._store[key] = str(value)
            return True

        async def expire(self, key: str, seconds: int, **kwargs: Any) -> int:
            return 1  # no-op TTL for unit tests

        async def aclose(self) -> None:
            pass

    redis = _FakeRedis()
    load_gate = RedisDeploymentLoadGate(redis=redis, alpha=0.5, in_flight_ttl_s=60)
    dep = "dep-1"

    # acquire × 2 → in_flight == 2
    await load_gate.acquire(dep)
    await load_gate.acquire(dep)
    inflight = await load_gate.in_flight(dep)
    assert inflight == 2, f"expected in_flight==2 after two acquires; got {inflight}"

    # release × 1 → in_flight == 1
    await load_gate.release(dep)
    inflight = await load_gate.in_flight(dep)
    assert inflight == 1, f"expected in_flight==1 after one release; got {inflight}"

    # in_flight must clamp negative → 0 (extra release beyond zero).
    await load_gate.release(dep)
    await load_gate.release(dep)  # now at -1 raw
    inflight_clamped = await load_gate.in_flight(dep)
    assert inflight_clamped >= 0, f"in_flight must clamp negative to 0; got {inflight_clamped}"

    # EWMA update: alpha=0.5, first sample 200ms → ewma = 0.5*200 + 0.5*0 = 100.0
    dep2 = "dep-2"
    await load_gate.record_latency(dep2, 200.0)
    ewma_val = await load_gate.latency_ewma(dep2)
    assert abs(ewma_val - 100.0) < 1.0, (
        f"expected EWMA ~= 100.0 (alpha=0.5, sample=200, prev=0); got {ewma_val}"
    )

    # Unseen deployment → ewma returns 0.0.
    unseen_ewma = await load_gate.latency_ewma("dep-unseen")
    assert unseen_ewma == 0.0, f"unseen deployment must return ewma=0.0; got {unseen_ewma}"


# ─────────────────────────────────────────────────────────────────────────────
# test_bs_settings_loadbal_knobs — Settings validators for loadbal fields
# ─────────────────────────────────────────────────────────────────────────────


def test_bs_settings_loadbal_knobs() -> None:
    """Settings exposes loadbal_ewma_alpha (default 0.3) and loadbal_inflight_ttl_s (default 60).

    Validators:
      alpha > 1.0 or alpha <= 0    → INVALID_LOADBAL_ALPHA
      ttl <= 0                     → INVALID_LOADBAL_TTL
    """
    from pydantic import ValidationError

    from gateway.core.config import Settings

    # Defaults.
    s = Settings(**_SETTINGS_KWARGS)
    assert s.loadbal_ewma_alpha == 0.3, f"default alpha must be 0.3; got {s.loadbal_ewma_alpha}"
    assert s.loadbal_inflight_ttl_s == 60, f"default ttl must be 60; got {s.loadbal_inflight_ttl_s}"

    # alpha > 1.0 → INVALID_LOADBAL_ALPHA.
    with pytest.raises(ValidationError) as exc_alpha:
        Settings(**_SETTINGS_KWARGS, loadbal_ewma_alpha=1.5)
    assert "INVALID_LOADBAL_ALPHA" in str(exc_alpha.value), (
        f"expected INVALID_LOADBAL_ALPHA; got {exc_alpha.value}"
    )

    # alpha <= 0 → INVALID_LOADBAL_ALPHA.
    with pytest.raises(ValidationError) as exc_alpha_zero:
        Settings(**_SETTINGS_KWARGS, loadbal_ewma_alpha=0.0)
    assert "INVALID_LOADBAL_ALPHA" in str(exc_alpha_zero.value)

    # ttl <= 0 → INVALID_LOADBAL_TTL.
    with pytest.raises(ValidationError) as exc_ttl:
        Settings(**_SETTINGS_KWARGS, loadbal_inflight_ttl_s=0)
    assert "INVALID_LOADBAL_TTL" in str(exc_ttl.value), (
        f"expected INVALID_LOADBAL_TTL; got {exc_ttl.value}"
    )
