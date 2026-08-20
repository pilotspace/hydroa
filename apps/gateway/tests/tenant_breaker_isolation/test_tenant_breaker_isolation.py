"""Red suite for ADD task `tenant-scoped-breaker-cooldown` (R9 P0 #3).

Every proxy upstream adapter held ONE process-wide CircuitBreaker constructed in
its __init__ and hard-refused through `self._breaker.guard()`. Each adapter is
built once in create_app() while carrying PER-TENANT BYOK credentials resolved
per-request from a contextvar, so tenant A's upstream failures tripped a breaker
that then refused every OTHER tenant on that provider — a cross-tenant DoS.

Template: tests/moderations_endpoint/test_cr1_per_tenant_breaker.py (the shipped
fix for the SAME defect class in the moderation seam, after a Tin HARD-STOP).
Failures are driven over an httpx.MockTransport whose handler raises ReadTimeout:
one deterministic breaker failure per call, no retry sleep, no network.

CRITICAL — these checks must actually TRIP the breaker. circuit_breaker.py:47
counts only 408, 429 and >= 500; a 401/403 does NOT trip. Threshold is 5
consecutive failures (circuit_breaker.py:25). A check that never reaches the
threshold proves nothing.

ANTI-VACUITY DISCIPLINE (the 2026-08-20 amendment, CR-1/CR-2/CR-3)
------------------------------------------------------------------
Three checks in the first build could not pass BY CONSTRUCTION, and four more
promised behaviour their bodies did not deliver. The single rule that fixes all
seven, applied everywhere below:

  * An anti-vacuity floor may NEVER be phrased over the live tree as "the defect
    is still present" (`assert found` / `assert population` / `assert owners`).
    Such a floor is satisfiable only WHILE the defect exists, so a correct fix
    makes it unpassable forever. Floors here are phrased over SYNTHETIC sources
    and over the WALKER'S OWN HEALTH — both survive a correct fix.
  * A `signature` read is not a partition proof and a prefix-constant read is not
    a key-collision proof. Every Reject is earned behaviourally, on the actual
    values the production code produces.

This suite is deliberately INFRA-FREE: httpx.MockTransport, an in-memory Redis
fake, AST, and a `create_app()` that opens no connection. No Postgres, no Redis.
An infra dropout can therefore never be mistaken for this suite going green.

Run: cd apps/gateway && uv run pytest tests/tenant_breaker_isolation -q --no-cov
"""

from __future__ import annotations

import ast
import functools
import importlib
import importlib.util
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Final

import httpx
import pytest
from prometheus_client import CollectorRegistry

from gateway.observability.metrics import MetricsRegistry
from gateway.proxy.domain.credential_context import (
    reset_provider_credential,
    set_provider_credential,
)
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream

# Reuse the in-tree in-memory Redis fake (tests/cooldown_circuit/conftest.py) —
# deliberately NOT a third fake. It is seconds-based, needs no server, and keeps
# this suite infra-free as §EVIDENCE promises.
from tests.cooldown_circuit.conftest import FakeRedis

_REGISTRY_MODULE = "gateway.proxy.infrastructure.tenant_breaker_registry"
_PROXY_SRC = Path(__file__).resolve().parents[2] / "src" / "gateway" / "proxy"

#: Walker-health floor for the AST census. `proxy/` is a large package (111
#: modules measured); if discovery ever walks a near-empty file list it has gone
#: blind, and a blind sweep reports a clean tree. Calibrated close to measured
#: reality — a token floor of 20 would let an 80%-blind walk pass. This floor
#: survives a correct fix, unlike one demanding the DEFECT still be present.
_MIN_PROXY_MODULES: Final[int] = 90


def _require_registry_module() -> Any:
    """Fail with the CONTRACT's name rather than an opaque ImportError."""
    if importlib.util.find_spec(_REGISTRY_MODULE) is None:
        pytest.fail(
            f"contracted surface absent: {_REGISTRY_MODULE} does not exist. "
            "S1 TenantScopedBreakerRegistry / S2 breaker_tenant_key are unbuilt."
        )
    return importlib.import_module(_REGISTRY_MODULE)


def _failing_openrouter() -> tuple[OpenRouterCompletionUpstream, list[httpx.Request]]:
    """A real adapter over a transport that always raises ReadTimeout.

    ReadTimeout is the non-retryable branch (upstream_retry.py) — exactly one
    breaker failure per call, raised immediately, so N calls trip the breaker
    after exactly N failures with no sleep.
    """
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        raise httpx.ReadTimeout("upstream unreachable")

    upstream = OpenRouterCompletionUpstream(base_url="https://openrouter.test/api/v1")
    upstream._client = httpx.AsyncClient(  # noqa: SLF001 — test double injection, same convention as test_cr1_per_tenant_breaker.py
        base_url="https://openrouter.test/api/v1",
        transport=httpx.MockTransport(handler),
    )
    return upstream, captured


async def _drive_failures(
    upstream: OpenRouterCompletionUpstream, tenant: uuid.UUID, n: int
) -> None:
    """Drive n consecutive REAL upstream failures as `tenant`."""
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant)  # noqa: S106
    try:
        for _ in range(n):
            with pytest.raises(UpstreamUnavailableError):
                await upstream.complete({"model": "some/model", "messages": []})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# M1 / M2 — the motivating cross-tenant checks. These MUST drive real failures.
# ---------------------------------------------------------------------------


async def test_adapter_breaker_is_per_tenant_not_process_wide() -> None:
    """M2: tenant A trips the adapter breaker; tenant B must still reach the transport.

    RED against the pre-fix tree: OpenRouterCompletionUpstream.__init__ held ONE
    CircuitBreaker (openrouter_upstream.py:133) shared by every tenant, so A's 5
    failures opened it and B's call raised CircuitOpenError without ever dialing.
    """
    upstream, captured = _failing_openrouter()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    await _drive_failures(upstream, tenant_a, 5)
    assert len(captured) == 5, f"expected 5 real dialed failures, got {len(captured)}"

    # Tenant A is now correctly short-circuited — protection still works for A.
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant_a)  # noqa: S106
    try:
        with pytest.raises(CircuitOpenError):
            await upstream.complete({"model": "some/model", "messages": []})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert len(captured) == 5, "tenant A's 6th call must short-circuit before the transport"

    # THE DEFECT: tenant B must still be served — its own breaker is CLOSED.
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant_b)  # noqa: S106
    try:
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete({"model": "some/model", "messages": []})
    except CircuitOpenError:
        pytest.fail(
            "CROSS-TENANT DoS: tenant B was refused by a breaker that only tenant A's "
            "failures opened. OpenRouterCompletionUpstream.__init__ (openrouter_upstream.py:133) "
            "holds ONE process-wide CircuitBreaker shared by every tenant."
        )
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert len(captured) == 6, (
        "CROSS-TENANT DoS: tenant B's call must reach the transport — its breaker must be "
        f"unaffected by tenant A's failures. Transport dialed {len(captured)} times, expected 6."
    )


async def test_tenant_a_trip_does_not_deny_tenant_b_on_chat_path() -> None:
    """M1/M3: the deps.py wrapper registry must be keyed per (tenant, provider).

    RED against the pre-fix tree: deps.py:141
    `self._breakers.setdefault(provider, CircuitBreaker())` keyed by provider
    alone, so two tenants on the same provider shared one breaker. Asserted at the
    registry-key level so this check stays honest whichever layer is fixed first
    (A21 — a partitioned outer layer would otherwise mask an unpartitioned inner).
    """
    import types

    from gateway.proxy.api.deps import get_completion_upstream

    class _Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            self.calls += 1
            # Same provider for both models (the resolver maps everything to
            # "openrouter"), so only a TENANT dimension can separate them.
            if payload["model"] == "m-fail":
                return 500, {"error": "boom"}
            return 200, {"ok": True}

        def stream(self, payload: dict[str, Any]) -> Any:
            raise NotImplementedError

    class _Resolver:
        async def provider_for(self, model_id: str) -> str:
            return "openrouter"

    breakers: dict[Any, CircuitBreaker] = {}
    delegate = _Delegate()
    state = types.SimpleNamespace(
        completion_upstream=delegate,
        circuit_breaker=CircuitBreaker(),
        provider_resolver=_Resolver(),
        provider_circuit_breakers=breakers,
    )
    request = types.SimpleNamespace(app=types.SimpleNamespace(state=state))
    upstream = get_completion_upstream(request)

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    scope = set_provider_credential(BearerCredential(secret="sk-a"), tenant_a)  # noqa: S106
    try:
        for _ in range(5):
            with pytest.raises(UpstreamUnavailableError):
                await upstream.complete({"model": "m-fail"})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]

    assert delegate.calls == 5

    scope = set_provider_credential(BearerCredential(secret="sk-b"), tenant_b)  # noqa: S106
    try:
        status, _body = await upstream.complete({"model": "m-ok"})
    except CircuitOpenError:
        pytest.fail(
            "CROSS-TENANT DoS on the chat path: tenant B was refused by a breaker that "
            "only tenant A's failures opened. deps.py:141 keys the registry by provider "
            f"alone; registry keys today are {list(breakers)!r} — they carry no tenant."
        )
    assert delegate.calls == 6, "tenant B's request must reach the delegate"
    assert status == 200


# ---------------------------------------------------------------------------
# S12 — the static structural guard, its walker health, and its mutation proof.
# ---------------------------------------------------------------------------

#: SYNTHETIC violation shapes, one per way a module can construct a breaker
#: directly. These are the guard's anti-vacuity population (CR-1).
#:
#: Why synthetic and not the live tree: after a correct fix the live tree
#: contains ZERO direct constructions under proxy/, so a floor of the form
#: `assert found` over the live tree is satisfiable only WHILE the defect exists
#: and can never pass again. A synthetic population cannot be emptied by a
#: correct fix, so it keeps proving the DETECTOR works forever.
_SYNTHETIC_VIOLATIONS: Final[tuple[tuple[str, str], ...]] = (
    (
        "adapter constructs its own breaker in __init__",
        "class PlainAdapter:\n"
        "    def __init__(self) -> None:\n"
        "        self._breaker = CircuitBreaker()\n",
    ),
    (
        "module-level shared singleton",
        "_SHARED = CircuitBreaker()\n",
    ),
    (
        "module-level default argument",
        "def build(breaker=CircuitBreaker()):\n    return breaker\n",
    ),
    (
        "attribute-qualified construction",
        "import circuit_breaker\n\n"
        "class QualifiedAdapter:\n"
        "    def __init__(self) -> None:\n"
        "        self._breaker = circuit_breaker.CircuitBreaker()\n",
    ),
    (
        "construction with keyword arguments",
        "class TunedAdapter:\n"
        "    def __init__(self) -> None:\n"
        "        self._breaker = CircuitBreaker(failure_threshold=5, cooldown_seconds=30.0)\n",
    ),
    (
        "construction nested inside a call",
        "def wire():\n    return BoundCircuitBreakerUpstream(CircuitBreaker(), delegate)\n",
    ),
)

#: SYNTHETIC shapes that are CORRECT and must NOT be flagged. Without these the
#: predicate could be `return True` and every violation floor above would pass.
_SYNTHETIC_CLEAN: Final[tuple[tuple[str, str], ...]] = (
    (
        "the fixed shape — resolve per tenant from the registry",
        "class FixedAdapter(TenantScopedBreakerMixin):\n"
        "    def __init__(self) -> None:\n"
        "        self._init_tenant_breakers()\n\n"
        "    def call(self):\n"
        "        self._breaker_for().guard()\n",
    ),
    (
        "importing the class is not constructing one",
        "from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker\n",
    ),
    (
        "type annotations are not constructions",
        "def f(b: CircuitBreaker) -> CircuitBreaker:\n    return b\n",
    ),
)


def _proxy_modules(root: Path = _PROXY_SRC) -> list[Path]:
    """Every python module under `root`, sorted. The walker's reach."""
    return sorted(root.rglob("*.py"))


def _discover_direct_breaker_constructions(root: Path = _PROXY_SRC) -> list[tuple[str, int]]:
    """DISCOVER (never hand-enumerate) every direct `CircuitBreaker(...)` call
    under `root`, excluding the registry module that is allowed to make them.

    `root` is a parameter so the walker itself can be exercised against a
    SYNTHETIC tree — which proves discovery works without requiring the live tree
    to still be broken.

    An unparseable module is a FINDING, never a skip.
    """
    found: list[tuple[str, int]] = []
    for path in _proxy_modules(root):
        rel = path.relative_to(root).as_posix()
        if rel == "infrastructure/tenant_breaker_registry.py":
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:  # pragma: no cover - a finding, not a skip
            pytest.fail(f"unparseable module under proxy/: {rel}: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = (
                    fn.id
                    if isinstance(fn, ast.Name)
                    else fn.attr
                    if isinstance(fn, ast.Attribute)
                    else None
                )
                if name == "CircuitBreaker":
                    found.append((rel, node.lineno))
    return found


def _predicate_flags(source: str) -> bool:
    """The guard's predicate, callable on a SOURCE STRING for mutation testing.

    True == this source directly constructs a CircuitBreaker (i.e. is flagged).
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = (
                fn.id
                if isinstance(fn, ast.Name)
                else fn.attr
                if isinstance(fn, ast.Attribute)
                else None
            )
            if name == "CircuitBreaker":
                return True
    return False


def test_no_direct_circuit_breaker_construction_under_proxy() -> None:
    """M9/R:GLOBAL_BREAKER: no module under proxy/ may construct a CircuitBreaker
    outside the tenant-scoped registry module.

    The CARRYING assertion is `assert not found` over the live tree.

    Anti-vacuity is proven three ways, none of which needs the defect to survive:
      1. the PREDICATE flags every synthetic violation shape and no correct shape;
      2. the WALKER (rglob + parse + predicate, end to end) finds a planted
         violation in a synthetic tree;
      3. the walker's REACH over the real tree is non-trivial, so it cannot report
         a clean sweep by walking nothing.
    """
    # (1) predicate health — synthetic, so a correct fix cannot empty it.
    for label, source in _SYNTHETIC_VIOLATIONS:
        assert _predicate_flags(source) is True, (
            f"detector blind to a violation shape ({label}) — it would report a clean "
            "sweep over a live tenant-blind breaker written that way"
        )
    for label, source in _SYNTHETIC_CLEAN:
        assert _predicate_flags(source) is False, (
            f"detector flags a CORRECT shape ({label}) — a predicate that flags "
            "everything proves nothing when it flags the violations too"
        )

    # (2) end-to-end walker health on a synthetic tree: rglob + parse + predicate.
    with tempfile.TemporaryDirectory() as tmp:
        synthetic_root = Path(tmp)
        (synthetic_root / "infrastructure").mkdir()
        (synthetic_root / "infrastructure" / "planted_upstream.py").write_text(
            _SYNTHETIC_VIOLATIONS[0][1], encoding="utf-8"
        )
        # The registry module is the ONE sanctioned construction site and must be
        # excluded — if it were not, the guard could never go green.
        (synthetic_root / "infrastructure" / "tenant_breaker_registry.py").write_text(
            _SYNTHETIC_VIOLATIONS[1][1], encoding="utf-8"
        )
        planted = _discover_direct_breaker_constructions(synthetic_root)
        assert planted == [("infrastructure/planted_upstream.py", 3)], (
            "the walker did not find a planted violation in a synthetic tree (or it "
            f"failed to exclude the registry module): {planted!r}"
        )

    # (3) walker reach on the REAL tree — a blind sweep is a clean sweep.
    modules = _proxy_modules()
    assert len(modules) >= _MIN_PROXY_MODULES, (
        f"discovery walked only {len(modules)} modules under {_PROXY_SRC} — proxy/ is a "
        "large package, so this means the walk went blind and its clean result is worthless"
    )

    found = _discover_direct_breaker_constructions()
    assert not found, (
        "tenant-blind breaker(s): these modules construct a CircuitBreaker directly "
        "instead of resolving one from TenantScopedBreakerRegistry, creating a failure "
        "domain shared by every tenant (cross-tenant DoS; HARD-STOPPED three times).\n"
        + "\n".join(f"  {rel}:{line}" for rel, line in found)
    )


def test_structural_guard_predicate_is_not_vacuous() -> None:
    """The guard's predicate must be ORIGINAL=True / MUTATED=False for EVERY
    member of its population.

    A guard that is red against the pre-fix tree can still be vacuous if its
    predicate matches a token that survives the fix. Here the mutation IS the
    fix: replace the direct construction with a registry lookup and re-run the
    predicate on the mutated source.

    The population is SYNTHETIC (CR-1). It used to be the live tree's discovered
    construction sites — but a correct fix empties that set, so `assert population`
    could never pass again once the task succeeded. Synthetic members are immune
    to that, and they cover shapes the live tree never happened to contain.
    """
    population = _SYNTHETIC_VIOLATIONS
    assert population, "no population to mutation-test — the synthetic corpus is empty"

    for label, source in population:
        assert _predicate_flags(source) is True, f"ORIGINAL not flagged: {label}"

        # The mutation IS the fix, and must stay syntactically valid (a mutation that
        # fails to parse would "pass" this test for the wrong reason).
        mutated = re.sub(r"\bCircuitBreaker\(", "_tenant_registry.get_or_create(", source)
        assert mutated != source, f"mutation was a no-op for {label} — nothing was tested"
        ast.parse(mutated)  # the mutated source must still be real Python
        assert _predicate_flags(mutated) is False, (
            f"MUTATED still flagged: {label} — the predicate matches something the fix "
            "does not remove, so it would report green on a live tenant-blind breaker."
        )


# ---------------------------------------------------------------------------
# Remaining contracted surfaces.
# ---------------------------------------------------------------------------


def test_registry_is_bounded_and_never_evicts_a_hot_tenant() -> None:
    """M5/R:UNBOUNDED/R:HOT_EVICT: bounded LRU; a hot tenant's OPEN breaker survives."""
    mod = _require_registry_module()
    registry = mod.TenantScopedBreakerRegistry(max_size=8)
    hot = uuid.uuid4()

    hot_breaker = registry.get_or_create(hot)
    for _ in range(5):
        hot_breaker.record_failure()
    assert hot_breaker.is_open() is True

    for i in range(50):
        registry.get_or_create(uuid.uuid4())
        if i % 3 == 0:
            registry.get_or_create(hot)

    assert len(registry) <= 8, f"registry exceeded its cap: {len(registry)}"
    assert registry.get_or_create(hot) is hot_breaker, (
        "LRU eviction must never reclaim a tenant that keeps driving traffic"
    )
    assert hot_breaker.is_open() is True, "eviction must not reset a hot tenant's OPEN breaker"
    assert mod.MAX_TENANT_BREAKERS > 0, "the cap must be a documented module constant"


def _cooldown_prefixes() -> list[str]:
    from gateway.proxy.infrastructure import redis_cooldown_gate as gate_mod

    return [
        getattr(gate_mod, name) for name in ("_PFX_FAILS", "_PFX_OPEN", "_PFX_HALF", "_PFX_PROBE")
    ]


def test_unattributed_call_never_shares_a_bucket_with_a_real_tenant() -> None:
    """R:SHARED_BUCKET / A16 / A17: the no-tenant sentinel is its own partition.

    BOTH halves, both behavioural — the BREAKER half (a sentinel breaker is a
    different object from any real tenant's) and the GATE half (the sentinel's
    Redis keys are a different key space from any real tenant's, and are NOT the
    legacy unprefixed key, which would be the pre-fix global shape surviving as
    the None case).
    """
    from gateway.proxy.infrastructure.redis_cooldown_gate import _key

    mod = _require_registry_module()
    registry = mod.TenantScopedBreakerRegistry()

    sentinel = mod.breaker_tenant_key()  # no credential scope set -> unattributed
    real = uuid.uuid4()

    # --- BREAKER half -----------------------------------------------------
    unattributed_breaker = registry.get_or_create(sentinel)
    for _ in range(5):
        unattributed_breaker.record_failure()
    assert unattributed_breaker.is_open() is True

    assert registry.get_or_create(real).is_open() is False, (
        "an unattributed call must never open a real tenant's breaker"
    )
    assert sentinel != real

    # ...and the coupling must not work in the other direction either: a real
    # tenant's open breaker must not refuse the unattributed caller.
    other = uuid.uuid4()
    other_breaker = registry.get_or_create(other)
    for _ in range(5):
        other_breaker.record_failure()
    assert other_breaker.is_open() is True
    assert registry.get_or_create(sentinel) is unattributed_breaker

    # --- GATE half (CR-3 / C4) -------------------------------------------
    model = "openai/gpt-4o"
    for prefix in _cooldown_prefixes():
        sentinel_key = _key(prefix, None, model)
        tenant_key = _key(prefix, real, model)

        assert mod.UNATTRIBUTED_SEGMENT in sentinel_key, (
            f"a no-tenant cooldown key carries no reserved sentinel segment: {sentinel_key!r} "
            f"(expected {mod.UNATTRIBUTED_SEGMENT!r})"
        )
        assert sentinel_key != tenant_key, (
            "the unattributed partition shares a cooldown key with a real tenant "
            f"({sentinel_key!r}) — R:SHARED_BUCKET"
        )
        assert str(real) not in sentinel_key

    legacy_open_key = f"gateway:cooldown:open:{model}"
    assert _key(_cooldown_prefixes()[1], None, model) != legacy_open_key, (
        "the None-tenant key IS the legacy unprefixed key — that is the pre-fix GLOBAL "
        "key shape surviving as the None case, which is exactly A17's failure mode"
    )


def test_one_registry_implementation_in_the_tree() -> None:
    """M4/A7: ml_moderation_evaluator must import the shared registry, not fork it."""
    _require_registry_module()
    source = (_PROXY_SRC / "infrastructure" / "ml_moderation_evaluator.py").read_text(
        encoding="utf-8"
    )
    assert "tenant_breaker_registry" in source, (
        "ml_moderation_evaluator must resolve its registry from the shared module"
    )
    assert "class _TenantBreakerRegistry" not in source, (
        "the private registry copy must be REPLACED, not kept alongside the shared one — "
        "a duplicate drifts and masks the structural guard"
    )


def _build_gate(redis: Any, *, threshold: int = 3) -> Any:
    """A real RedisCooldownGate over an in-memory fake. No server, no Postgres."""
    from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate

    return RedisCooldownGate(
        redis=redis,
        metrics_registry=MetricsRegistry(registry=CollectorRegistry()),
        threshold=threshold,
        ttl_s=30,
        window_s=60,
    )


async def test_cooldown_gate_is_tenant_partitioned() -> None:
    """M6/A2/A12/A23/A31/E6/E13: the port carries the tenant AND it partitions.

    BEHAVIOURAL against the in-memory Redis fake. A `signature` read alone proves
    the parameter EXISTS, not that it partitions anything — that was CR-3's
    finding, and it is why the driven half below is the assertion that earns M6.
    """
    import inspect

    from gateway.proxy.domain.ports import ModelHealthGate

    for method in (
        ModelHealthGate.is_available,
        ModelHealthGate.record_failure,
        ModelHealthGate.record_success,
    ):
        sig = inspect.signature(method)
        assert "tenant_id" in sig.parameters, (
            f"{method.__name__} carries no tenant: {sig} — one tenant's failures "
            "still gate every other tenant's traffic through the shared cooldown keys."
        )
        assert sig.parameters["tenant_id"].kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{method.__name__}'s tenant_id must be KEYWORD-ONLY (A23) so a fake gate "
            "that ignores it fails loudly instead of swallowing a positional"
        )

    # --- E13: driving A past the threshold must not gate B -----------------
    fake = FakeRedis()
    gate = _build_gate(fake, threshold=3)
    model = "openai/gpt-4o"
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    for _ in range(3):
        await gate.record_failure(model, tenant_id=tenant_a)

    assert await gate.is_available(model, tenant_id=tenant_a) is False, (
        "tenant A drove 3 failures at threshold 3 — its OWN circuit must be open, or "
        "isolation was achieved by removing protection (R:WEAKEN)"
    )
    assert await gate.is_available(model, tenant_id=tenant_b) is True, (
        "CROSS-TENANT DoS through the cooldown gate: tenant B is refused on a model it "
        "never failed on, because tenant A's failures accumulated in a SHARED key. "
        f"keys in the fake: {sorted(fake._store)!r}"  # noqa: SLF001 — diagnostics
    )

    # The sentinel partition is not collateral damage either.
    assert await gate.is_available(model, tenant_id=None) is True

    # --- E6/A12: threshold == 0 issues ZERO Redis commands -----------------
    quiet = FakeRedis()
    disabled = _build_gate(quiet, threshold=0)
    assert await disabled.is_available(model, tenant_id=tenant_a) is True
    await disabled.record_failure(model, tenant_id=tenant_a)
    await disabled.record_success(model, tenant_id=tenant_a)
    assert quiet.command_log == [], (
        "the default-off deployment must stay byte-identical: partitioning must happen "
        f"AFTER the threshold==0 fast path, but it issued {quiet.command_log!r}"
    )


def test_cooldown_key_cannot_be_collided_by_a_crafted_model_id() -> None:
    """R:KEY_COLLISION/A20/E4: tenant segment FIRST, caller-controlled model_id LAST.

    BEHAVIOURAL, on the ACTUAL KEY STRINGS the gate builds. Reading the prefix
    constants and asserting the word "tenant" appears in them is NOT sufficient
    and does not earn this Reject — that was CR-3's finding.
    """
    from gateway.proxy.infrastructure.redis_cooldown_gate import _fixed_prefix, _key

    prefixes = _cooldown_prefixes()
    assert all("{tenant_key}" in p for p in prefixes), (
        f"cooldown key prefixes carry no tenant slot: {prefixes}"
    )

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    # `model_id` arrives straight from the request body. These are the shapes an
    # attacker would reach for to climb out of their own namespace.
    crafted = [
        "gpt-4o",
        f"{tenant_b}:gpt-4o",
        f"../{tenant_b}:gpt-4o",
        ":x",
        "*",
        f"{tenant_b}",
        f":{tenant_b}:gpt-4o",
    ]

    keys_a: set[str] = set()
    keys_b: set[str] = set()
    for prefix in prefixes:
        fixed = _fixed_prefix(prefix)
        for model in crafted:
            key_a = _key(prefix, tenant_a, model)
            key_b = _key(prefix, tenant_b, model)

            # ORDER — where A20's property actually lives, and what nothing in the
            # first build asserted: the tenant segment must be the FIRST thing after
            # the fixed prefix, so a crafted model id can only ever address DEEPER
            # inside its own namespace, never climb out of it.
            assert key_a.startswith(f"{fixed}{tenant_a}:"), (
                f"key does not start with its own tenant segment: {key_a!r} "
                f"(expected prefix {fixed}{tenant_a}:) — a caller-controlled model_id "
                "placed before the tenant would make a cross-tenant write reachable"
            )
            assert key_b.startswith(f"{fixed}{tenant_b}:")
            assert key_a != key_b, (
                f"two tenants share one cooldown key for model {model!r}: {key_a!r}"
            )

            keys_a.add(key_a)
            keys_b.add(key_b)

    collisions = keys_a & keys_b
    assert not collisions, (
        "R:KEY_COLLISION — a crafted model_id assembled one tenant's key into another "
        f"tenant's namespace: {sorted(collisions)!r}"
    )

    # And the specific attack the Reject names: tenant A asking for a model id that
    # spells out tenant B must not reach any key tenant B can reach.
    for prefix in prefixes:
        attack = _key(prefix, tenant_a, f"{tenant_b}:gpt-4o")
        target = _key(prefix, tenant_b, "gpt-4o")
        assert attack != target, f"crafted model_id reached another tenant's key: {attack!r}"

    # A tenant key that would itself split the namespace is REFUSED, never rewritten:
    # a sanitiser that rewrites ":" is non-injective and collides two distinct tenants.
    mod = _require_registry_module()
    with pytest.raises(ValueError, match="separator"):
        mod.tenant_key_segment("tenant:evil")


async def test_streaming_breaker_records_against_the_guarded_tenant() -> None:
    """A10/E5: a streamed call records its outcome against the tenant it guarded.

    stream() calls guard() EAGERLY but the generator body runs later, after the
    credential contextvar has been reset — a lazily-read tenant would split one
    request across two buckets.
    """
    from gateway.proxy.domain.credential_context import current_credential_tenant

    upstream, captured = _failing_openrouter()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    # Credential stays valid throughout (production resets it only AFTER the stream
    # completes). Only the TENANT moves between guard time and consume time — which
    # is precisely what distinguishes an eager capture from a lazy read.
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant_a)  # noqa: S106
    try:
        for _ in range(5):
            gen = upstream.stream({"model": "some/model", "messages": []})  # guard: tenant A
            swap = current_credential_tenant.set(tenant_b)  # body will see tenant B
            try:
                with pytest.raises(UpstreamUnavailableError):
                    async for _chunk in gen:
                        pass
            finally:
                current_credential_tenant.reset(swap)
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]

    assert len(captured) == 5, f"expected 5 real dialed stream failures, got {len(captured)}"

    # Tenant A guarded all five, so A must now be OPEN.
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant_a)  # noqa: S106
    try:
        with pytest.raises(CircuitOpenError):
            await upstream.complete({"model": "some/model", "messages": []})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]

    # Tenant B merely happened to be current while the generator bodies ran. If the
    # tenant is read lazily at record time, B absorbs A's five failures and is refused.
    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant_b)  # noqa: S106
    try:
        with pytest.raises(UpstreamUnavailableError):
            await upstream.complete({"model": "some/model", "messages": []})
    except CircuitOpenError:
        pytest.fail(
            "the streamed failures were recorded against whichever tenant happened to be "
            "current when the generator body ran, not the tenant that was guarded — one "
            "request split across two failure domains (openrouter_upstream.py:317 guards "
            "eagerly, the _gen() body records lazily)."
        )
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]


def _realtime_bound_upstream_call() -> ast.Call:
    """The ONE `BoundCircuitBreakerUpstream(...)` construction in realtime_ws.py."""
    tree = ast.parse((_PROXY_SRC / "api" / "realtime_ws.py").read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "BoundCircuitBreakerUpstream"
    ]
    assert len(calls) == 1, (
        f"expected exactly one BoundCircuitBreakerUpstream construction in realtime_ws.py, "
        f"found {len(calls)} — the M8 seam moved and this check no longer binds it"
    )
    return calls[0]


async def test_realtime_path_breaker_is_per_tenant() -> None:
    """M8/A9/A14: the realtime websocket chat turn resolves a PER-TENANT breaker.

    BEHAVIOURAL. The previous form of this check was
    `assert "app.state.circuit_breaker" not in source` — a text grep on one file.
    It proved neither half of the row it carries: it would go green on a rename,
    an alias, a `getattr(app.state, "circuit_breaker")` spelling, or a rewrite
    that shared ONE breaker under a different name. That is CR-3's exact defect
    class, and this row binds M8 on a `sensitivity: security` task.

    So: drive the ACTUAL wrapper the realtime turn constructs
    (`BoundCircuitBreakerUpstream`), fed from the ACTUAL registry seam the call
    site uses (`registry_for_state(app.state)`), and assert tenant A tripping it
    does not refuse tenant B's turn. Then bind the call SITE structurally, so the
    behaviour proven here is the behaviour the realtime path actually gets.
    """
    import types

    from gateway.proxy.infrastructure.circuit_breaker_proxy import BoundCircuitBreakerUpstream

    mod = _require_registry_module()

    # --- structural half: the call site resolves a REGISTRY, not one breaker ---
    call = _realtime_bound_upstream_call()
    kwargs = {kw.arg: kw.value for kw in call.keywords}
    assert "breakers" in kwargs, (
        "the realtime turn does not pass a tenant registry to BoundCircuitBreakerUpstream "
        f"(keywords: {sorted(k for k in kwargs if k)}) — with a single `breaker=` every "
        "tenant on the realtime path shares one failure domain"
    )
    registry_expr = kwargs["breakers"]
    assert (
        isinstance(registry_expr, ast.Call)
        and isinstance(registry_expr.func, ast.Name)
        and registry_expr.func.id == "registry_for_state"
    ), (
        "the realtime turn's breakers argument is not resolved from the shared per-app "
        f"tenant registry: {ast.dump(registry_expr)[:200]}"
    )

    # ...and no expression anywhere under proxy/ still reads the legacy breaker.
    assert not _proxy_code_reading_legacy_breaker(), (
        "proxy/ code reads the single process-wide app.state.circuit_breaker again: "
        f"{_proxy_code_reading_legacy_breaker()}"
    )

    # --- behavioural half: A trips the turn's breaker, B is still served -------
    class _Delegate:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            self.calls += 1
            if payload["model"] == "m-fail":
                return 500, {"error": "boom"}
            return 200, {"ok": True}

        def stream(self, payload: dict[str, Any]) -> Any:
            raise NotImplementedError

    delegate = _Delegate()
    state = types.SimpleNamespace()
    upstream = BoundCircuitBreakerUpstream(
        delegate=delegate, breakers=mod.registry_for_state(state)
    )

    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    scope = set_provider_credential(BearerCredential(secret="sk-a"), tenant_a)  # noqa: S106
    try:
        for _ in range(5):
            with pytest.raises(UpstreamUnavailableError):
                await upstream.complete({"model": "m-fail"})
        with pytest.raises(CircuitOpenError):
            await upstream.complete({"model": "m-fail"})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert delegate.calls == 5, "tenant A's 6th realtime turn must short-circuit"

    scope = set_provider_credential(BearerCredential(secret="sk-b"), tenant_b)  # noqa: S106
    try:
        status, _body = await upstream.complete({"model": "m-ok"})
    except CircuitOpenError:
        pytest.fail(
            "CROSS-TENANT DoS on the realtime websocket path: tenant B's chat turn was "
            "refused by a breaker only tenant A's failures opened. realtime_ws.py used to "
            "read app.state.circuit_breaker — the single legacy breaker every tenant shared."
        )
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert (delegate.calls, status) == (6, 200), "tenant B's turn must reach the delegate"


class _NeverCompletingScanRedis(FakeRedis):
    """A fake whose SCAN cursor never returns to 0 — an INCOMPLETE keyspace walk.

    Real SCAN is O(keyspace) and the gate bounds it. A bounded walk that did not
    finish has NOT inspected every partition, and the board must say so.
    """

    async def scan(
        self, cursor: int = 0, match: str | None = None, count: int | None = None
    ) -> tuple[int, list[str]]:
        self._log("SCAN")
        del match, count
        return 1, []


async def test_routing_admin_reports_an_honest_cross_partition_state() -> None:
    """M7/A3/A18/A25/A28/E7: the board must not report 'closed' over uninspected partitions.

    BEHAVIOURAL, driving the real `snapshot_state` over the in-memory Redis fake.
    A `signature` read does not earn M7 — that was CR-3's finding.
    """
    import inspect

    from gateway.proxy.infrastructure.redis_cooldown_gate import _PFX_OPEN, RedisCooldownGate, _key

    sig = inspect.signature(RedisCooldownGate.snapshot_state)
    assert "tenant_id" in sig.parameters, (
        f"snapshot_state carries no tenant: {sig} — after partitioning it would read one "
        "arbitrary key space and report 'closed' over partitions it never inspected."
    )

    model = "openai/gpt-4o"
    tenant_a = uuid.uuid4()

    # --- a circuit open in SOME partition is never reported as plainly closed ---
    fake = FakeRedis()
    gate = _build_gate(fake, threshold=3)

    assert await gate.snapshot_state(model, tenant_id=None) == "closed", (
        "an empty, COMPLETELY scanned keyspace is honestly closed"
    )

    await fake.set(_key(_PFX_OPEN, tenant_a, model), "1", ex=30)
    aggregate = await gate.snapshot_state(model, tenant_id=None)
    assert aggregate == "open", (
        "the superadmin board reported "
        f"{aggregate!r} while tenant {tenant_a} is being refused on {model!r} — a state "
        "that is open in ANY partition must never be averaged into 'closed' (A18/M7)"
    )

    # A neighbouring tenant's key must not be mistaken for this model's.
    other_model_key = _key(_PFX_OPEN, uuid.uuid4(), "anthropic/claude-3")
    await fake.set(other_model_key, "1", ex=30)
    assert await gate.snapshot_state("anthropic/claude-3", tenant_id=None) == "open"

    # --- an INCOMPLETE scan is "unknown", NEVER "closed" -------------------
    truncating = _NeverCompletingScanRedis()
    truncating_gate = _build_gate(truncating, threshold=3)
    verdict = await truncating_gate.snapshot_state(model, tenant_id=None)
    assert verdict == "unknown", (
        f"a bounded scan that did not finish reported {verdict!r}. Reporting 'closed' over "
        "a partition it never inspected is the masked-gate failure mode this row exists "
        "to close — the operator reads a green board through a live outage."
    )
    assert verdict != "closed"


def test_no_caller_reaches_a_breaker_with_an_untagged_credential() -> None:
    """M11/E8: a caller holding a tenant must not set an untagged credential.

    RED against the pre-fix tree: usage/application/cost_recovery.py:168 called
    `set_provider_credential(cred)` with no tenant while `tenant_id` was in scope
    at :165, from a background task, against the SAME adapter instance the live
    chat path uses (main.py:1727) — so its failures landed in the untagged bucket
    and could open a breaker that also serves real tenant traffic.

    Asserted at the CALL SITE by AST, and BEHAVIOURALLY by entering the credential
    context the recovery path establishes and asserting which bucket a breaker
    lookup would then resolve to.
    """
    mod = _require_registry_module()
    gateway_src = _PROXY_SRC.parent

    # --- call-site half ----------------------------------------------------
    offenders: list[str] = []
    for rel in (
        "usage/application/cost_recovery.py",
        "proxy/infrastructure/ml_moderation_evaluator.py",
    ):
        source = (gateway_src / rel).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "set_provider_credential"
                and len(node.args) < 2
                and not node.keywords
            ):
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "untagged credential reaching a shared breaker — these call sites hold a tenant "
        "but set the credential without it, so their upstream failures accumulate in the "
        "untagged bucket alongside traffic that is not theirs:\n"
        + "\n".join(f"  {o}" for o in offenders)
    )

    # --- behavioural half --------------------------------------------------
    # Enter exactly the credential context the recovery path establishes and ask
    # the production resolver which bucket the failure would land in. This is the
    # honest form of "which bucket", without booting a background task.
    tenant = uuid.uuid4()
    scope = set_provider_credential(BearerCredential(secret="sk-recovery"), tenant)  # noqa: S106
    try:
        resolved = mod.breaker_tenant_key()
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]

    assert resolved == tenant, (
        f"inside the cost-recovery credential scope a breaker lookup resolved to {resolved!r} "
        f"instead of tenant {tenant} — the background poll's failures would land in a bucket "
        "shared with live tenant traffic"
    )
    assert resolved is not mod.UNATTRIBUTED_TENANT

    # Control: the PRE-FIX shape (an untagged credential) really does fall into the
    # sentinel bucket, so the assertion above is capable of failing.
    untagged = set_provider_credential(BearerCredential(secret="sk-recovery"))  # noqa: S106
    try:
        fallback = mod.breaker_tenant_key()
    finally:
        reset_provider_credential(untagged)  # type: ignore[arg-type]
    assert fallback is mod.UNATTRIBUTED_TENANT, (
        "an UNTAGGED credential no longer resolves to the sentinel, so the assertion above "
        "cannot distinguish the fixed call site from the broken one — it is vacuous"
    )


# ---------------------------------------------------------------------------
# S13 — the BOOT-TIME census. Disjoint predicate: `isinstance` over the LIVE
# object graph, never the static census's AST rule (M12).
# ---------------------------------------------------------------------------

# The five shapes that produce a tenant-blind breaker. FOUR of them are invisible
# to the static AST census, which is precisely why M12 requires a second guard
# with a DISJOINT predicate. These are built as LIVE OBJECTS below.
_AliasedBreaker = CircuitBreaker  # 1. import alias
_CLS = CircuitBreaker  # 2. module-level rebinding
_PARTIAL_BREAKER = functools.partial(CircuitBreaker)  # 3. functools.partial


class _SubclassedBreaker(CircuitBreaker):  # 4. subclass
    pass


def _default_arg_breaker(breaker: CircuitBreaker = CircuitBreaker()) -> CircuitBreaker:  # noqa: B008
    """5. module-level default argument — evaluated once, at import."""
    return breaker


class _EvasiveAdapter:
    """An adapter that owns a breaker without ever writing `CircuitBreaker(`."""

    def __init__(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker


#: (label, live breaker, source the STATIC census would see) per evasion shape.
_EVASION_SHAPES: Final[tuple[tuple[str, CircuitBreaker, str], ...]] = (
    (
        "import alias",
        _AliasedBreaker(),
        "from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker as _CB\n"
        "class A:\n    def __init__(self):\n        self._breaker = _CB()\n",
    ),
    (
        "_CLS = CircuitBreaker",
        _CLS(),
        "_CLS = CircuitBreaker\n"
        "class A:\n    def __init__(self):\n        self._breaker = _CLS()\n",
    ),
    (
        "functools.partial",
        _PARTIAL_BREAKER(),
        "import functools\n_MK = functools.partial(CircuitBreaker)\n"
        "class A:\n    def __init__(self):\n        self._breaker = _MK()\n",
    ),
    (
        "subclass",
        _SubclassedBreaker(),
        "class _Sub(CircuitBreaker):\n    pass\n"
        "class A:\n    def __init__(self):\n        self._breaker = _Sub()\n",
    ),
    (
        "module-level default argument",
        _default_arg_breaker(),
        "def _mk(b=CircuitBreaker()):\n    return b\n"
        "class A:\n    def __init__(self):\n        self._breaker = _mk()\n",
    ),
)


def _build_booted_app() -> Any:
    """Build the REAL app. Opens no socket: create_app() wires objects only.

    (The lifespan is what connects to Postgres/Redis, and it is never entered
    here — which is what keeps this suite infra-free.)
    """
    from gateway.core.config import Settings
    from gateway.main import create_app
    from tests import _redis_env

    return create_app(
        Settings(
            database_url=_redis_env.TEST_DATABASE_URL,
            jwt_secret="test-secret-not-for-production-0123456789",  # noqa: S106
            redis_url=_redis_env.TEST_REDIS_URL,
        )
    )


def _proxy_code_reading_legacy_breaker() -> list[str]:
    """Every `*.state.circuit_breaker` EXPRESSION under proxy/ (comments excluded).

    An AST read, not a text grep: several modules discuss the legacy breaker in
    prose, and a text match on a comment would make this a false positive.
    """
    readers: list[str] = []
    for path in _proxy_modules():
        rel = path.relative_to(_PROXY_SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "circuit_breaker"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "state"
            ):
                readers.append(f"{rel}:{node.lineno}")
    return readers


def test_no_live_breaker_escapes_the_tenant_registry_at_boot() -> None:
    """M12/E9: every live CircuitBreaker the booted app can reach is registry-owned.

    The population predicate is `isinstance` over the LIVE OBJECT GRAPH. It may
    NOT reuse the static census's AST rule (`name == "CircuitBreaker"` on an
    `ast.Call`) — that is the very predicate M12 exists to complement:
    `_CLS = CircuitBreaker; _CLS()` yields `name == "_CLS"`, so a shared predicate
    would leave the class out of the population and report green on four of the
    five evasion shapes this census was written to catch.

    The anti-vacuity floor is the WALKER'S REACH — that the traversal visits a
    known-nonempty set and terminates — never `assert owners` over the live tree.
    After a correct fix every breaker IS registry-owned, so a floor demanding an
    unowned one could never pass again.
    """
    mod = _require_registry_module()

    # --- floor 1: the walker finds what is planted, with correct ownership ---
    import types

    planted_registry = mod.TenantScopedBreakerRegistry()
    owned = [planted_registry.get_or_create(uuid.uuid4()) for _ in range(3)]
    loose = CircuitBreaker()
    probe_state = types.SimpleNamespace(
        registry=planted_registry,
        legacy=loose,
        nested={"adapters": [_EvasiveAdapter(CircuitBreaker())]},
    )
    probe = mod.iter_live_breakers(probe_state, root_path="probe")
    assert not probe.truncated
    assert len(probe.breakers) == 5, (
        "the walker did not reach a known-nonempty planted set (3 registry-owned + 2 "
        f"loose): it found {[b.path for b in probe.breakers]!r}"
    )
    assert {id(b.breaker) for b in probe.breakers if b.owner is planted_registry} == {
        id(b) for b in owned
    }, "the walker mis-attributed registry ownership"
    assert len(probe.unowned) == 2, (
        "the walker reported a LOOSE breaker as registry-owned — it would then never "
        "flag a real one"
    )

    # A breaker held ONLY in a closure cell — no attribute anywhere names it.
    # main.py:1869 wires the eval executor through closures over app.state, so
    # this is a live pattern in this codebase, not a hypothetical.
    def _capture(hidden: CircuitBreaker) -> Any:
        def _run() -> CircuitBreaker:
            return hidden

        return _run

    captured = CircuitBreaker()
    closure_probe = mod.iter_live_breakers(
        types.SimpleNamespace(factory=_capture(captured)), root_path="closure"
    )
    assert [b.breaker for b in closure_probe.unowned] == [captured], (
        "the census cannot see a breaker captured in a CLOSURE CELL — main.py wires the "
        "eval executor exactly that way, so a process-wide breaker could live there and "
        f"the census would report a clean fleet. Found: {[b.path for b in closure_probe.breakers]!r}"
    )

    # --- floor 2 / E9: every evasion shape is caught, and four are invisible
    #     to the static census, which is what makes the two guards complementary.
    for label, live_breaker, static_source in _EVASION_SHAPES:
        holder = types.SimpleNamespace(adapter=_EvasiveAdapter(live_breaker))
        scan = mod.iter_live_breakers(holder, root_path=f"evasion[{label}]")
        assert [b.breaker for b in scan.unowned] == [live_breaker], (
            f"the boot census MISSED a tenant-blind breaker built via {label} — this is "
            "the exact shape M12 exists to catch"
        )
        if label != "module-level default argument":
            assert _predicate_flags(static_source) is False, (
                f"the static AST census can see the {label} shape after all. That is not a "
                "bug in this test — but M12's complementarity claim is then overstated and "
                "belongs back in Direction as a change-request."
            )

    # --- the carrying assertion, over the REAL booted app -------------------
    app = _build_booted_app()
    scan = mod.iter_live_breakers(app.state)

    assert not scan.truncated, (
        f"the census stopped after {scan.nodes_visited} nodes without finishing the graph — "
        "a partial walk reporting a clean result is the masked-gate failure mode"
    )
    # Reach floors, calibrated against MEASURED reality (~1.85k nodes, 13
    # registries at boot) rather than a token number a 95%-blind walk would still
    # clear. `registries_visited` is the better of the two: every adapter owns
    # one, so the count GROWS with the tree and a correct fix can never empty it.
    assert scan.nodes_visited >= 1000, (
        f"the census walked only {scan.nodes_visited} objects of the booted app (measured "
        "~1850 on a healthy tree) — it has gone blind, and a blind census reports a clean fleet"
    )
    assert scan.registries_visited >= 10, (
        f"the census reached only {scan.registries_visited} tenant registries (measured 13 on "
        "a healthy tree) — it is not reaching the adapters whose breakers it exists to audit"
    )

    # The ONE named exemption: `main.py:1222` still assigns the legacy
    # process-wide breaker to app.state. It is exempt ONLY because it is INERT.
    # main.py is outside this task's frozen `scope:`, so DELETING it is a
    # change-request, not a build-time edit.
    legacy = getattr(app.state, "circuit_breaker", None)
    exempt = [b for b in scan.unowned if b.breaker is legacy]
    escaped = [b for b in scan.unowned if b.breaker is not legacy]

    assert not escaped, (
        "these live breakers are reachable from the booted app but are NOT owned by a "
        f"TenantScopedBreakerRegistry ({len(escaped)} of {len(scan.breakers)} live "
        "breakers) — each is one failure domain shared by every tenant "
        "(cross-tenant DoS; HARD-STOPPED three times):\n"
        + "\n".join(f"  {b.path}: {type(b.breaker).__name__}" for b in escaped)
    )

    if exempt:
        assert len(exempt) == 1, (
            f"more than one breaker claims the legacy exemption: {[b.path for b in exempt]!r}"
        )
        readers = _proxy_code_reading_legacy_breaker()
        assert not readers, (
            "the legacy app.state.circuit_breaker is exempt from this census ONLY while it "
            "is inert, but proxy/ code reads it again — it is a live process-wide breaker "
            f"on a tenant-reachable path (R:GLOBAL_BREAKER):\n"
            + "\n".join(f"  {r}" for r in readers)
        )

        # ...and the two assertions above are NOT enough on their own. The walk
        # dedupes by `id()`, so ONE object reached by N references is reported
        # ONCE — `len(exempt) == 1` says nothing about how many things hold it.
        # And the AST scan covers only proxy/, while the read that would
        # resurrect this breaker lives in the composition root (main.py) that
        # `scope:` excludes. A single line there —
        # `Adapter(breaker=app.state.circuit_breaker)` or a `getattr` spelling —
        # yields one exempt entry, zero proxy/ readers, and a GREEN census over a
        # live process-wide breaker on a tenant-reachable adapter. That is the
        # masked gate this whole task is about, so prove inertness on the LIVE
        # GRAPH instead of in the source: detach the one sanctioned reference and
        # re-walk. Anything still reaching the object is a second live path,
        # whatever package it was written in and however it was spelled.
        # MUST BE LAST — it mutates the throwaway app.
        delattr(app.state, "circuit_breaker")
        residual = mod.iter_live_breakers(app.state, root_path="app.state<legacy-detached>")
        second_paths = [b.path for b in residual.breakers if b.breaker is legacy]
        assert not second_paths, (
            "the legacy app.state.circuit_breaker is exempt ONLY while it is inert, but it "
            "is reachable by a SECOND live path — a process-wide breaker held by something "
            "that serves tenants (R:GLOBAL_BREAKER):\n" + "\n".join(f"  {p}" for p in second_paths)
        )


def _discover_breaker_entry_points() -> set[tuple[str, str, str, int]]:
    """DERIVE every (rel, class, function, lineno) that reaches a breaker.

    An entry point is any function calling `guard()`, `call_allowed()`, or
    `execute_with_retry(...)` — the latter guards per ATTEMPT (upstream_retry.py:137).
    Discovered, never hand-typed.
    """
    out: set[tuple[str, str, str, int]] = set()
    for path in _proxy_modules():
        rel = path.relative_to(_PROXY_SRC).as_posix()
        if rel in (
            "infrastructure/circuit_breaker.py",
            "infrastructure/tenant_breaker_registry.py",
        ):
            continue  # the primitive's own internals are not entry points
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:  # pragma: no cover - a finding, not a skip
            pytest.fail(f"unparseable module under proxy/: {rel}: {exc}")
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for fn in (
                n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            ):
                for call in ast.walk(fn):
                    if not isinstance(call, ast.Call):
                        continue
                    f = call.func
                    if isinstance(f, ast.Attribute) and f.attr in ("guard", "call_allowed"):
                        out.add((rel, cls.name, fn.name, call.lineno))
                    elif isinstance(f, ast.Name) and f.id == "execute_with_retry":
                        out.add((rel, cls.name, fn.name, call.lineno))
    return out


def test_every_breaker_entry_point_is_tenant_scoped() -> None:
    """M2/E10: the sweep is by ENTRY POINT, not by adapter.

    Eleven adapters own a breaker, but there are THIRTY distinct entry points —
    OpenAIDirectProvider alone has six. The secondary ones run off the request
    path entirely, so no per-request check would ever drive them.
    """
    entries = _discover_breaker_entry_points()

    # Anti-vacuity floor: the WALKER's health, not the defect's survival. These two
    # secondary entries stay entry points after the fix — the fix changes HOW they
    # resolve a breaker, not WHETHER they reach one.
    known_secondary = {
        ("infrastructure/openrouter_upstream.py", "get_generation"),
        ("infrastructure/openai_provider.py", "post_json_with_retry"),
    }
    seen = {(rel, fn) for rel, _cls, fn, _ln in entries}
    missing = known_secondary - seen
    assert not missing, f"discovery stopped seeing known secondary entry points: {sorted(missing)}"

    mod = _require_registry_module()
    mixin = mod.TenantScopedBreakerMixin
    unscoped: list[str] = []
    for rel, cls_name, fn_name, lineno in sorted(entries):
        module_name = "gateway.proxy." + rel[: -len(".py")].replace("/", ".")
        cls = getattr(importlib.import_module(module_name), cls_name, None)
        # A class the census cannot resolve is a FINDING, never a skip — the file's
        # own doctrine. `if cls is not None and ...` would silently exempt any
        # entry-point class that gets renamed, moved, or hidden behind
        # TYPE_CHECKING, which is a guard quietly shrinking its own population.
        assert cls is not None, (
            f"derived entry-point class {cls_name} is not importable from {module_name} "
            f"({rel}:{lineno}) — the census cannot judge it, so it must not pass it"
        )
        if mixin not in cls.__mro__:
            unscoped.append(f"{rel}:{lineno} {cls_name}.{fn_name}")

    assert not unscoped, (
        f"{len(unscoped)} of {len(entries)} breaker entry points are not tenant-scoped:\n"
        + "\n".join(f"  {u}" for u in unscoped)
    )


def test_tenant_key_resolver_prefers_credential_then_guardrail_tenant() -> None:
    """M13/E11: credential tenant -> guardrail tenant -> sentinel.

    The moderation seam is ALREADY per-tenant but keys off guardrail_tenant_context
    (ml_moderation_evaluator.py:270, :389) while setting its credential UNTAGGED
    (:402). A resolver reading only the credential contextvar would collapse every
    tenant's moderation traffic into one bucket, silently undoing the CR-1 fix.
    """
    mod = _require_registry_module()
    from gateway.proxy.domain.guardrail_tenant_context import (
        reset_guardrail_tenant_id,
        set_guardrail_tenant_id,
    )

    tenant_cred, tenant_guard = uuid.uuid4(), uuid.uuid4()
    sentinel = mod.breaker_tenant_key()  # nothing set

    # Only the guardrail tenant is set — moderation's exact shape.
    gtid = set_guardrail_tenant_id(tenant_guard)
    try:
        key = mod.breaker_tenant_key()
    finally:
        reset_guardrail_tenant_id(gtid)
    assert key != sentinel, (
        "with only a guardrail tenant in context the resolver returned the sentinel — "
        "every tenant's moderation traffic would share one breaker, undoing CR-1"
    )
    assert key == tenant_guard or tenant_guard in repr(key), (
        f"expected the guardrail tenant to key the breaker, got {key!r}"
    )

    # Credential tenant wins when both are set.
    scope = set_provider_credential(BearerCredential(secret="sk-x"), tenant_cred)  # noqa: S106
    gtid = set_guardrail_tenant_id(tenant_guard)
    try:
        both = mod.breaker_tenant_key()
    finally:
        reset_guardrail_tenant_id(gtid)
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert both == tenant_cred or tenant_cred in repr(both), (
        f"the credential tenant must take precedence when both are set, got {both!r}"
    )


async def test_breaker_still_protects_each_tenants_upstream() -> None:
    """M10/A29/R:WEAKEN: partitioning must not be achieved by disabling protection."""
    upstream, captured = _failing_openrouter()
    tenant = uuid.uuid4()

    await _drive_failures(upstream, tenant, 5)
    assert len(captured) == 5

    scope = set_provider_credential(BearerCredential(secret="sk-test"), tenant)  # noqa: S106
    try:
        with pytest.raises(CircuitOpenError):
            await upstream.complete({"model": "some/model", "messages": []})
    finally:
        reset_provider_credential(scope)  # type: ignore[arg-type]
    assert len(captured) == 5, (
        "the breaker must STILL short-circuit a tenant that is genuinely failing — "
        "isolation comes from partitioning the key, never from fail-opening"
    )

    # ...and protection must be PER TENANT, not one shared breaker wearing a
    # registry's name. A registry that hands the same breaker to every key would
    # satisfy every assertion above while preserving the cross-tenant DoS.
    mod = _require_registry_module()
    registry = mod.TenantScopedBreakerRegistry()
    a, b = uuid.uuid4(), uuid.uuid4()
    assert registry.get_or_create(a) is not registry.get_or_create(b), (
        "the registry returned the SAME CircuitBreaker for two different tenants — "
        "protection is still one shared failure domain"
    )
    assert registry.get_or_create(a) is registry.get_or_create(a), (
        "the registry must return a STABLE breaker per tenant, or failures never accumulate"
    )


# ---------------------------------------------------------------------------
# S14 — the Prometheus gauge must report a breaker production actually drives.
# ---------------------------------------------------------------------------


class _XlenOnlyRedis:
    """Satisfies the metrics endpoint's single Redis call. No server involved."""

    async def xlen(self, key: str) -> int:
        return 0


def _gauge_series(body: bytes) -> list[str]:
    return [
        line
        for line in body.decode().splitlines()
        if line.startswith("gateway_circuit_breaker_state") and not line.startswith("#")
    ]


async def test_breaker_state_gauge_reports_a_breaker_production_drives() -> None:
    """M14/A32-A37/E12: the gauge reads the tenant registries, not an orphan.

    RED against the pre-fix tree, where `expose_metrics` read
    `app.state.circuit_breaker` — a breaker that, once M8 moved the realtime path
    off it, NOTHING in production drives. The gauge was pinned at 0.0 = "closed"
    forever: an operator watching a green board through a live cross-tenant outage.
    """
    from gateway.observability.metrics import expose_metrics

    mod = _require_registry_module()
    app = _build_booted_app()
    app.state.redis_client = _XlenOnlyRedis()

    # A35: a freshly booted app has created no breakers (they are lazy, A11) —
    # honestly closed over an INSPECTED and genuinely empty set.
    body, content_type = await expose_metrics(app)
    assert content_type.startswith("text/plain")
    series = _gauge_series(body)
    assert len(series) == 1, f"expected exactly one gauge series, got {series!r}"
    assert float(series[0].split()[-1]) == 0.0, (
        f"a freshly booted app must export 0.0 (closed), got {series[0]!r}"
    )

    # A32: NO tenant dimension. A `tenant` label is unbounded and
    # attacker-influenceable via self-serve signup — R:UNBOUNDED reappearing as
    # Prometheus cardinality explosion.
    assert "{" not in series[0], (
        f"the gauge carries labels: {series[0]!r} — the tenant dimension is what this "
        "metric AGGREGATES OVER, never what it labels by (A32)"
    )
    assert "tenant" not in series[0]

    # E12/A36: exactly ONE tenant open, every other partition closed.
    registry = mod.registry_for_state(app.state)
    open_tenant, quiet_tenant = uuid.uuid4(), uuid.uuid4()
    open_breaker = registry.get_or_create(open_tenant)
    for _ in range(5):
        open_breaker.record_failure()
    assert open_breaker.is_open() is True, "precondition: one tenant's circuit must be OPEN"
    assert registry.get_or_create(quiet_tenant).is_open() is False

    # A34: recomputed at SCRAPE time — the value moved without rebuilding the app.
    body, _ = await expose_metrics(app)
    series = _gauge_series(body)
    assert len(series) == 1, f"expected exactly one gauge series, got {series!r}"
    value = float(series[0].split()[-1])
    assert value == 2.0, (
        f"the gauge reported {value} while a tenant's circuit is OPEN. Worst-state-wins "
        "(A36): during an incident the operator's question is 'is anyone being refused', "
        "not 'is the average fine'. A gauge pinned to a breaker nothing drives reports "
        "health over a surface that cannot move (M14)."
    )

    # A37: name and 0/1/2 encoding unchanged — a half-open circuit still reads 1.0.
    half_open = registry.get_or_create(uuid.uuid4())
    for _ in range(5):
        half_open.record_failure()
    open_breaker._state = type(open_breaker._state).CLOSED  # noqa: SLF001 — reset the OPEN one
    half_open._state = type(half_open._state).HALF_OPEN  # noqa: SLF001
    body, _ = await expose_metrics(app)
    assert float(_gauge_series(body)[0].split()[-1]) == 1.0, (
        "half_open must still encode as 1.0 — existing dashboards read this encoding (A37)"
    )
