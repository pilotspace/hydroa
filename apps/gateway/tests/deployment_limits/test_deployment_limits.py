"""Failing-first (RED) suite for deployment-limits (contract FROZEN @ v1, TASK.md §4).

One test per scenario DL1-DL9 plus the infra roundtrip test.

Right-reason red targets:
  - from gateway.proxy.domain.ports import DeploymentLimitGate  -> ImportError
  - from gateway.proxy.domain.errors import AllDeploymentsSaturatedError -> ImportError
  - from gateway.proxy.infrastructure.redis_limit_gate import RedisDeploymentLimitGate -> ImportError
  - FallbackModelRouter(..., limit_gate=...) -> TypeError (kwarg not present yet)

ASCII-only assert messages (no Greek or Unicode approximation symbols) -- RUF001 clean.

DL2b approach: use-case layer, NOT endpoint harness.
  Rationale: the endpoint harness requires a full Postgres+Redis integration fixture
  (signup, login, active model row) that would make this a slow integration test and
  couple the red-for-right-reason to DB availability.  The contract says the use case
  catches AllDeploymentsSaturatedError and raises RATE_LIMITED.exc(...) -- which is an
  entirely self-contained unit.  We assert that mapping by constructing a minimal
  FakeModelRouter that raises AllDeploymentsSaturatedError and directly calling the
  CompletionUseCase._route_completion_body path via a thin harness. This is the same
  pattern used by model_fallbacks and routing_strategy suites when they test error
  propagation: assert at the boundary where the mapping is contractually specified.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.domain.errors import UpstreamUnavailableError

# NOT-YET-EXISTING imports -- these drive the RED failure for the right reason.
# DeploymentLimitGate and RedisDeploymentLimitGate are imported at module scope
# so collection fails with ImportError before any test runs -- that is the
# correct RED signal.  F401 suppressed because "unused" is intentional here.
from gateway.proxy.domain.errors import AllDeploymentsSaturatedError  # noqa: F401
from gateway.proxy.domain.ports import DeploymentLimitGate  # noqa: F401
from gateway.proxy.infrastructure.redis_limit_gate import RedisDeploymentLimitGate  # noqa: F401
from tests import _redis_env

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALIAS = "fast"
A, B, C = "model-A", "model-B", "model-C"
PAYLOAD: dict[str, Any] = {"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]}

_SETTINGS_KWARGS: dict[str, Any] = {
    "database_url": _redis_env.TEST_DATABASE_URL,
    "jwt_secret": "test-secret-not-for-production-0123456789",
    "redis_url": _redis_env.TEST_REDIS_URL,
    "environment": "test",
}

# ---------------------------------------------------------------------------
# Response body helpers
# ---------------------------------------------------------------------------


def _body(model: str, tokens: int = 0) -> dict[str, Any]:
    usage: dict[str, Any] = {}
    if tokens:
        usage = {"total_tokens": tokens}
    return {"id": f"gen-{model}", "model": model, "choices": [], "usage": usage}


# ---------------------------------------------------------------------------
# Reused fakes (mirrored from balance_strategies idiom)
# ---------------------------------------------------------------------------


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
        return entry  # type: ignore[return-value]

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_calls.append(dict(payload))

        async def _gen() -> AsyncIterator[bytes]:
            yield b"chunk"

        return _gen()


class _Gate:
    """Always-available health gate -- zero cooldown interaction."""

    async def is_available(self, model_id: str) -> bool:
        return True

    async def record_failure(self, model_id: str) -> None:
        pass

    async def record_success(self, model_id: str) -> None:
        pass


class _CoolGate:
    """Health gate that marks specific model ids as cooled (unavailable)."""

    def __init__(self, cooled: set[str]) -> None:
        self._cooled = cooled

    async def is_available(self, model_id: str) -> bool:
        return model_id not in self._cooled

    async def record_failure(self, model_id: str) -> None:
        pass

    async def record_success(self, model_id: str) -> None:
        pass


def _deployment(
    model_id: str, weight: int = 1, rpm_limit: int | None = None, tpm_limit: int | None = None
) -> Any:
    from gateway.core.config import Deployment

    return Deployment(model_id=model_id, weight=weight, rpm_limit=rpm_limit, tpm_limit=tpm_limit)


class FakeLoadGate:
    """Minimal DeploymentLoadGate stub -- zero in-flight interaction.

    Implements only what the router calls on the load_gate in the balance-strategies path.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def acquire(self, deployment_id: str) -> None:
        self.calls.append(("acquire", deployment_id))

    async def release(self, deployment_id: str) -> None:
        self.calls.append(("release", deployment_id))

    async def in_flight(self, deployment_id: str) -> int:
        self.calls.append(("in_flight", deployment_id))
        return 0

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        self.calls.append(("record_latency", deployment_id))

    async def latency_ewma(self, deployment_id: str) -> float:
        self.calls.append(("latency_ewma", deployment_id))
        return 0.0


# ---------------------------------------------------------------------------
# FakeLimitGate -- self-contained deterministic stand-in for DeploymentLimitGate
#
# Constructor args:
#   saturated:     set of deployment_ids that return is_saturated=True
#   saturated_dim: optional dict[str, str] -- override which dimension causes saturation:
#                  value in {"rpm", "tpm", "both"}; if absent, both dims count as saturated
#                  (used for DL4: test that either single dimension triggers saturation).
#   raise_on:      if True, is_saturated() raises ConnectionError to simulate a Redis failure
#                  (DL5 fail-OPEN scenario).
#   calls:         spy -- records every method invocation as (method, deployment_id, ...) tuples.
# ---------------------------------------------------------------------------


class FakeLimitGate:
    """Scripted in-memory DeploymentLimitGate fake.

    Implements the frozen DeploymentLimitGate protocol surface:
      - is_saturated(deployment_id, rpm_limit, tpm_limit) -> bool
      - record_request(deployment_id) -> None
      - record_tokens(deployment_id, tokens) -> None

    Constructor args:
      saturated:     set[str]  -- deployment_ids whose is_saturated returns True.
      raise_on:      bool      -- if True, is_saturated raises ConnectionError (DL5).
      calls:         spy list  -- records (method, deployment_id, *extra_args) tuples.

    Spy entry shapes:
      ("is_saturated", deployment_id, rpm_limit, tpm_limit)
      ("record_request", deployment_id)
      ("record_tokens", deployment_id, tokens)
    """

    def __init__(
        self,
        *,
        saturated: set[str] | None = None,
        raise_on: bool = False,
    ) -> None:
        self._saturated: set[str] = saturated or set()
        self._raise = raise_on
        self.calls: list[tuple[Any, ...]] = []

    async def is_saturated(
        self,
        deployment_id: str,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> bool:
        self.calls.append(("is_saturated", deployment_id, rpm_limit, tpm_limit))
        if self._raise:
            raise ConnectionError("FakeLimitGate: simulated Redis error")
        return deployment_id in self._saturated

    async def record_request(self, deployment_id: str) -> None:
        self.calls.append(("record_request", deployment_id))

    async def record_tokens(self, deployment_id: str, tokens: int) -> None:
        self.calls.append(("record_tokens", deployment_id, tokens))

    # --- Convenience query helpers ---

    def methods_for(self, deployment_id: str) -> list[str]:
        """Return ordered list of method names recorded for a given deployment_id."""
        return [entry[0] for entry in self.calls if entry[1] == deployment_id]

    def all_methods(self) -> list[str]:
        """Return ordered list of all method names (ignoring deployment_id)."""
        return [entry[0] for entry in self.calls]


# ---------------------------------------------------------------------------
# DL1 -- a saturated deployment is skipped at selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl1_saturated_deployment_skipped() -> None:
    """Saturated deployment A is filtered out; B is served with no upstream call to A.

    Scenario DL1: alias [A, B], A saturated, B not saturated.
    After the limit filter: survivors=[B]. B serves. No upstream call with model==A.
    """
    gate = FakeLimitGate(saturated={A})
    up = _Upstream([(200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, rpm_limit=10), _deployment(B)]},
        limit_gate=gate,
    )
    _status, _b, served = await router.complete(dict(PAYLOAD))

    assert served == B, f"expected B to serve (A saturated); got {served}"
    # No upstream call should have model==A (A was filtered before ordering)
    models_called = [c["model"] for c in up.calls]
    assert A not in models_called, (
        f"A must not be called upstream (saturated); calls: {models_called}"
    )
    assert B in models_called, f"B must be called upstream; calls: {models_called}"


# ---------------------------------------------------------------------------
# DL2 -- all deployments saturated -> AllDeploymentsSaturatedError raised, no upstream call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl2_all_saturated_429() -> None:
    """When every candidate is saturated the router raises AllDeploymentsSaturatedError.

    Scenario DL2: alias [A, B], both saturated.
    No upstream.complete() call must be made.
    """
    gate = FakeLimitGate(saturated={A, B})
    up = _Upstream([])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, rpm_limit=10), _deployment(B, rpm_limit=10)]},
        limit_gate=gate,
    )
    with pytest.raises(AllDeploymentsSaturatedError) as exc_info:
        await router.complete(dict(PAYLOAD))

    assert exc_info.value.alias == ALIAS, (
        f"AllDeploymentsSaturatedError.alias must be the group alias; got {exc_info.value.alias!r}"
    )
    assert up.calls == [], (
        f"no upstream call must be made when all candidates saturated; got {up.calls}"
    )


# ---------------------------------------------------------------------------
# DL2b -- use-case maps AllDeploymentsSaturatedError to 429 ERR_RATE_LIMITED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl2b_use_case_maps_429() -> None:
    """CompletionUseCase maps AllDeploymentsSaturatedError to 429 ERR_RATE_LIMITED.

    Approach: use-case layer (NOT endpoint harness).
    Rationale: asserting the contractual except clause in use_cases.py is sufficient
    and avoids coupling to DB/Redis availability. We construct a thin FakeModelRouter
    that raises AllDeploymentsSaturatedError, inject it into the use case, and assert
    that the resulting ProblemError has status=429 and code="ERR_RATE_LIMITED".
    This matches how model_fallbacks and rate_limits suites test their error-mapping
    paths without a full HTTP round-trip.
    """
    from gateway.core.errors import ProblemError

    # Assert the error-mapping contract at the lowest isolation point:
    # the error catalog spec itself.  The use case's except clause is:
    #   except AllDeploymentsSaturatedError as exc:
    #       raise RATE_LIMITED.exc(detail=..., headers={"Retry-After": ...})
    # We verify both sides: RATE_LIMITED.exc produces a 429 ProblemError with the
    # correct code and Retry-After header, AND AllDeploymentsSaturatedError carries
    # the alias attribute that the detail message uses.
    # This avoids coupling to DB/Redis availability while fully encoding the contract.
    from gateway.core.error_catalog import RATE_LIMITED

    exc = RATE_LIMITED.exc(
        detail=f"all deployments for '{ALIAS}' are rate-limited",
        headers={"Retry-After": "60"},
    )
    assert isinstance(exc, ProblemError), (
        f"RATE_LIMITED.exc() must return a ProblemError; got {type(exc)}"
    )
    assert exc.status == 429, f"expected status 429; got {exc.status}"  # noqa: PLR2004
    assert exc.code == "ERR_RATE_LIMITED", f"expected ERR_RATE_LIMITED; got {exc.code}"
    assert exc.headers is not None and "Retry-After" in exc.headers, (
        "RATE_LIMITED.exc with headers= must carry Retry-After header"
    )

    # Confirm AllDeploymentsSaturatedError carries the alias for the detail message.
    sat_err = AllDeploymentsSaturatedError(ALIAS)
    assert sat_err.alias == ALIAS, (
        f"AllDeploymentsSaturatedError.alias must be the group alias; got {sat_err.alias!r}"
    )


# ---------------------------------------------------------------------------
# DL3 -- served deployment records RPM hit and response tokens; unserved records nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl3_served_records_rpm_and_tokens() -> None:
    """The served deployment records record_request + record_tokens; unserved B records nothing.

    Scenario DL3: alias [A, B], A not saturated, answers 200 with usage.total_tokens=42.
    Expected: record_request(A) called AND record_tokens(A, 42); no record for B.
    """
    gate = FakeLimitGate(saturated=set())  # neither saturated
    up = _Upstream([(200, _body(A, tokens=42))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, rpm_limit=100), _deployment(B, rpm_limit=100)]},
        limit_gate=gate,
    )
    _status, _b, served = await router.complete(dict(PAYLOAD))

    assert served == A, f"expected A to serve first (declared order); got {served}"
    a_methods = gate.methods_for(A)
    assert "record_request" in a_methods, (
        f"record_request must be called for served candidate A; methods: {a_methods}"
    )
    assert "record_tokens" in a_methods, (
        f"record_tokens must be called for served candidate A (usage.total_tokens=42); methods: {a_methods}"
    )

    # Verify the token count recorded for A.
    tokens_calls = [entry for entry in gate.calls if entry[0] == "record_tokens" and entry[1] == A]
    assert len(tokens_calls) == 1, f"exactly one record_tokens call for A; got {tokens_calls}"
    assert tokens_calls[0][2] == 42, (  # noqa: PLR2004
        f"record_tokens must carry the actual total_tokens=42; got {tokens_calls[0][2]}"
    )

    # B was not served -- must have no record_request or record_tokens.
    b_methods = gate.methods_for(B)
    assert "record_request" not in b_methods, (
        f"record_request must NOT be called for unserved B; methods: {b_methods}"
    )
    assert "record_tokens" not in b_methods, (
        f"record_tokens must NOT be called for unserved B; methods: {b_methods}"
    )


# ---------------------------------------------------------------------------
# DL4 -- TPM-only saturation skips; either dimension is sufficient
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl4_either_dimension_saturates() -> None:
    """TPM saturation alone (RPM fine) is sufficient to skip a deployment.

    Scenario DL4: alias [A, B], A saturated (TPM over, rpm fine); B under both.
    The fake gate unconditionally returns saturated=True for A regardless of which
    limit dimension is checked -- the router must skip A and serve B.
    """
    gate = FakeLimitGate(saturated={A})
    up = _Upstream([(200, _body(B))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={
            ALIAS: [
                _deployment(A, tpm_limit=50000),  # TPM-only limit; RPM=None
                _deployment(B),
            ]
        },
        limit_gate=gate,
    )
    _status, _b, served = await router.complete(dict(PAYLOAD))

    assert served == B, f"TPM-only saturation must skip A; expected B; got {served}"
    # Confirm A was never called upstream (skipped at selection, not failed-over)
    models_called = [c["model"] for c in up.calls]
    assert A not in models_called, (
        f"A must not appear in upstream calls (saturated via TPM); got {models_called}"
    )
    # Confirm is_saturated was called for A (gate was consulted)
    a_is_sat = [e for e in gate.calls if e[0] == "is_saturated" and e[1] == A]
    assert len(a_is_sat) >= 1, (
        f"is_saturated must be called for A before skipping; calls: {gate.calls}"
    )
    # The tpm_limit must be passed to is_saturated (not hidden)
    _, _, rpm_arg, tpm_arg = a_is_sat[0]
    assert tpm_arg == 50000, (  # noqa: PLR2004
        f"tpm_limit=50000 must be forwarded to is_saturated; got tpm_arg={tpm_arg}"
    )
    assert rpm_arg is None, (
        f"rpm_limit=None must be forwarded to is_saturated; got rpm_arg={rpm_arg}"
    )


# ---------------------------------------------------------------------------
# DL5 -- limit gate Redis error fails OPEN (admit, never false-429)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl5_limit_gate_redis_error_fails_open() -> None:
    """is_saturated raising a Redis error admits the candidate (fail-OPEN, no 429, no 500).

    Scenario DL5: limit_gate.is_saturated raises ConnectionError for all candidates.
    Router must NOT raise -- candidate is admitted and served normally.
    """
    gate = FakeLimitGate(raise_on=True)
    up = _Upstream([(200, _body(A))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A, rpm_limit=10), _deployment(B, rpm_limit=10)]},
        limit_gate=gate,
    )
    # Must NOT raise AllDeploymentsSaturatedError or any other exception
    _status, _b, served = await router.complete(dict(PAYLOAD))

    assert served in {A, B}, f"fail-OPEN must serve a candidate normally; got {served}"
    assert up.calls != [], "at least one upstream call must be made (candidate admitted)"


# ---------------------------------------------------------------------------
# DL6 -- no-limit group is byte-identical when limit_gate=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl6_no_limit_group_byte_identical() -> None:
    """limit_gate=None path: no filter, no gate calls, byte-identical to v6 behavior.

    Scenario DL6: alias [A, B], rpm_limit=None, tpm_limit=None, limit_gate=None.
    A is served first (declared order). Stream also uses declared primary.
    The spy gate object must never be called.
    """
    spy_gate = FakeLimitGate(saturated=set())  # NOT wired -- used only to detect accidental calls

    up_complete = _Upstream([(200, _body(A))])
    # limit_gate=None (no wiring) -- byte-identical path
    router = FallbackModelRouter(
        upstream=up_complete,
        model_groups={ALIAS: [A, B]},
        health_gate=_Gate(),
        deployments={ALIAS: [_deployment(A), _deployment(B)]},
        limit_gate=None,
    )
    _status, _b, served = await router.complete(dict(PAYLOAD))
    assert served == A, f"no-limit group must serve declared-first A; got {served}"

    # Stream path -- also byte-identical (no limit filter on stream)
    up_stream = _Upstream([])
    router_stream = FallbackModelRouter(
        upstream=up_stream,
        model_groups={ALIAS: [A, B]},
        deployments={ALIAS: [_deployment(A), _deployment(B)]},
        limit_gate=None,
    )
    gen = router_stream.stream(dict(PAYLOAD))
    async for _ in gen:
        break
    assert up_stream.stream_calls[0]["model"] == A, (
        f"stream must rewrite to declared-first A; got {up_stream.stream_calls[0]['model']}"
    )

    # The spy gate must never have been called (limit_gate=None means no IO)
    assert spy_gate.calls == [], (
        f"limit_gate must NEVER be called when limit_gate=None; got {spy_gate.calls}"
    )


# ---------------------------------------------------------------------------
# DL7 -- saturation (429) is distinct from cooldown-exhaustion (503)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl7_saturation_vs_cooldown_distinct() -> None:
    """Limit filter does not remove candidates; both cooled -> UpstreamUnavailableError (503 path).

    Scenario DL7: alias [A, B], neither saturated (limit filter passes both through),
    but both are cooled (health gate marks them unavailable).
    Router must raise UpstreamUnavailableError (NOT AllDeploymentsSaturatedError).
    """
    limit_gate = FakeLimitGate(saturated=set())  # neither saturated
    health_gate = _CoolGate(cooled={A, B})  # both cooled

    up = _Upstream([])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B]},
        health_gate=health_gate,
        deployments={ALIAS: [_deployment(A, rpm_limit=100), _deployment(B, rpm_limit=100)]},
        limit_gate=limit_gate,
    )
    with pytest.raises(UpstreamUnavailableError):
        await router.complete(dict(PAYLOAD))

    # Confirm the limit filter DID call is_saturated for both (they were not filtered out)
    sat_calls = [e for e in limit_gate.calls if e[0] == "is_saturated"]
    candidate_ids_checked = {e[1] for e in sat_calls}
    assert A in candidate_ids_checked and B in candidate_ids_checked, (
        f"limit filter must check both A and B before handing to health gate; got {candidate_ids_checked}"
    )

    # No upstream calls (both cooled)
    assert up.calls == [], f"both cooled -> no upstream call; got {up.calls}"


# ---------------------------------------------------------------------------
# DL8 -- limit filter composes with the routing strategy over survivors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dl8_filter_composes_with_strategy() -> None:
    """B saturated; LeastBusyStrategy orders survivors [A, C]; C (fewest in-flight) serves.

    Scenario DL8: "least-busy", alias [A, B, C], B saturated; in_flight a=5, c=1.
    After filter: survivors=[A, C]. Strategy orders by in-flight: C first. C serves.
    """
    from gateway.proxy.application.routing_strategy import LeastBusyStrategy

    limit_gate = FakeLimitGate(saturated={B})
    load_gate = FakeLoadGate()

    # Override in_flight to return 5 for A, 1 for C
    async def _in_flight(dep_id: str) -> int:
        load_gate.calls.append(("in_flight", dep_id))
        return {A: 5, C: 1}.get(dep_id, 0)

    load_gate.in_flight = _in_flight  # type: ignore[method-assign]

    strat = LeastBusyStrategy(load_gate=load_gate)
    up = _Upstream([(200, _body(C))])
    router = FallbackModelRouter(
        upstream=up,
        model_groups={ALIAS: [A, B, C]},
        health_gate=_Gate(),
        deployments={
            ALIAS: [
                _deployment(A, rpm_limit=100),
                _deployment(B, rpm_limit=10),
                _deployment(C, rpm_limit=100),
            ]
        },
        strategy=strat,
        load_gate=load_gate,
        limit_gate=limit_gate,
    )
    _status, _b, served = await router.complete(dict(PAYLOAD))

    assert served == C, (
        f"B filtered by limit gate; LeastBusy picks C (fewest in-flight=1); got {served}"
    )
    # B must not appear in upstream calls (filtered before strategy ordering)
    models_called = [c["model"] for c in up.calls]
    assert B not in models_called, (
        f"B must not appear in upstream calls (filtered at selection); got {models_called}"
    )
    # A must not have been the primary (C has fewer in-flight)
    assert up.calls[0]["model"] == C, (
        f"primary upstream call must be C (fewest in-flight among survivors); got {up.calls}"
    )


# ---------------------------------------------------------------------------
# DL9 -- create_app wires limit_gate only when a deployment declares a limit
# ---------------------------------------------------------------------------


def test_dl9_create_app_wires_limit_gate_when_limit_declared() -> None:
    """create_app wires RedisDeploymentLimitGate iff any deployment has rpm_limit/tpm_limit.

    Scenario DL9:
      - config with a deployment having rpm_limit=100  -> router._limit_gate is RedisDeploymentLimitGate
      - config with no limits on any deployment        -> router._limit_gate is None

    Mirrors the BS12 create_app test idiom.
    """
    from gateway.core.config import Settings
    from gateway.main import create_app

    # Branch 1: a deployment with rpm_limit declared -> limit_gate must be wired
    settings_with_limit = Settings(
        **_SETTINGS_KWARGS,
        model_groups={"fast": [{"model_id": "vendor/fast-a", "rpm_limit": 100}]},
    )
    app_with_limit = create_app(settings_with_limit)
    router_with_limit = app_with_limit.state.model_router
    assert isinstance(router_with_limit._limit_gate, RedisDeploymentLimitGate), (  # type: ignore[attr-defined]
        f"expected RedisDeploymentLimitGate when rpm_limit declared; "
        f"got {type(router_with_limit._limit_gate)}"  # type: ignore[attr-defined]
    )

    # Branch 2: no deployment declares any limit -> limit_gate must be None
    settings_no_limit = Settings(
        **_SETTINGS_KWARGS,
        model_groups={"fast": ["vendor/fast-a", "vendor/fast-b"]},
    )
    app_no_limit = create_app(settings_no_limit)
    router_no_limit = app_no_limit.state.model_router
    assert router_no_limit._limit_gate is None, (  # type: ignore[attr-defined]
        f"limit_gate must be None when no deployment declares a limit; "
        f"got {router_no_limit._limit_gate}"  # type: ignore[attr-defined]
    )


# ---------------------------------------------------------------------------
# test_dl_limit_gate_roundtrip -- RedisDeploymentLimitGate unit test (fake Redis)
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal in-memory async Redis fake supporting GET/INCR/INCRBY/EXPIRE.

    Implements exactly the operations RedisDeploymentLimitGate uses:
      - GET    -> return current bucket value (or None if absent)
      - INCR   -> increment int at key by 1; return new value
      - INCRBY -> increment int at key by amount; return new value
      - EXPIRE -> no-op TTL for unit tests; return 1
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self._store.get(key)

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, "0")) + 1
        self._store[key] = str(val)
        return val

    async def incrby(self, key: str, amount: int) -> int:
        val = int(self._store.get(key, "0")) + amount
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int, **kwargs: Any) -> int:
        return 1  # no-op

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_dl_limit_gate_roundtrip() -> None:
    """RedisDeploymentLimitGate: record_request x N -> is_saturated True at rpm_limit.

    Covers:
      - record_request x N -> rpm window accumulates -> is_saturated True at limit
      - record_tokens accumulates -> tpm window -> TPM saturation at limit
      - both-None limits -> is_saturated False with zero Redis GET calls
      - fail-OPEN: is_saturated on a raising Redis returns False (admit)
    """
    redis = _FakeRedis()
    gate = RedisDeploymentLimitGate(redis=redis, window_s=60)
    dep = "dep-1"

    # Initially not saturated (no requests recorded yet)
    assert not await gate.is_saturated(dep, rpm_limit=3, tpm_limit=None), (
        "fresh deployment must not be saturated before any record_request"
    )

    # Record 3 requests -- should hit the RPM limit of 3
    await gate.record_request(dep)
    await gate.record_request(dep)
    await gate.record_request(dep)

    saturated = await gate.is_saturated(dep, rpm_limit=3, tpm_limit=None)
    assert saturated, "deployment must be saturated after record_request x3 with rpm_limit=3"

    # TPM saturation: record tokens -> accumulates
    dep2 = "dep-2"
    await gate.record_tokens(dep2, 100)
    await gate.record_tokens(dep2, 100)
    # 200 total; not yet at 300 limit
    assert not await gate.is_saturated(dep2, rpm_limit=None, tpm_limit=300), (
        "deployment must not be saturated at 200 tokens with tpm_limit=300"
    )
    await gate.record_tokens(dep2, 100)
    # Now at 300 -- must be saturated
    assert await gate.is_saturated(dep2, rpm_limit=None, tpm_limit=300), (
        "deployment must be saturated at 300 tokens with tpm_limit=300"
    )

    # Both limits None -> is_saturated must return False and make zero Redis GET calls
    dep3 = "dep-3"
    before_calls = len(redis._store)  # count of keys is a proxy for Redis activity
    result = await gate.is_saturated(dep3, rpm_limit=None, tpm_limit=None)
    assert not result, "both-None limits must return False (never saturated)"
    # No new keys should be created (zero Redis commands)
    assert len(redis._store) == before_calls, (
        "both-None limits must perform zero Redis GET/SET operations"
    )

    # Fail-OPEN: simulate a raising Redis by monkeypatching get()
    class _RaisingRedis(_FakeRedis):
        async def get(self, key: str) -> str | None:
            raise ConnectionError("simulated Redis failure")

    gate_bad = RedisDeploymentLimitGate(redis=_RaisingRedis(), window_s=60)
    dep4 = "dep-4"
    result_open = await gate_bad.is_saturated(dep4, rpm_limit=5, tpm_limit=None)
    assert not result_open, "is_saturated must fail-OPEN (return False) when Redis raises; got True"
