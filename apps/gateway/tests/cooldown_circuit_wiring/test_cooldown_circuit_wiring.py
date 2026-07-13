"""Production-wiring regression tests for the cooldown-circuit seam.

v6 foundation rule: every new app.state seam introduced by a task must have a
paired production-wiring regression test asserting that create_app() correctly
threads the Settings fields through to the live infrastructure adapter.

New seams introduced by cooldown-circuit task:
  - app.state.cooldown_gate: None at default settings (threshold=0)
  - app.state.cooldown_gate: RedisCooldownGate at threshold>0,
    wired with redis_client, metrics_registry, threshold, ttl_s, window_s
  - FallbackModelRouter.health_gate: threads through from app.state.cooldown_gate
  - MetricsRegistry.cooldown_transitions_total counter registered on per-app registry

These tests do NOT touch the frozen cooldown_circuit suite.
No DB or Redis required — create_app() is called synchronously; app.state is
inspected without triggering lifespan.

Pattern: tests/retry_policy_wiring/test_retry_policy_wiring.py
         tests/model_fallbacks_wiring/test_model_fallbacks_wiring.py
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.application.fallback_router import FallbackModelRouter
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from tests import _redis_env


def _make_settings(**kwargs: object) -> Settings:
    """Build a minimal Settings for unit tests (no live DB/Redis needed)."""
    defaults: dict[str, object] = {
        "database_url": _redis_env.TEST_DATABASE_URL,
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": _redis_env.TEST_REDIS_URL,
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_default_cooldown_gate_is_none() -> None:
    """create_app() with default settings (threshold=0) sets app.state.cooldown_gate = None."""
    settings = _make_settings()
    app = create_app(settings)

    assert hasattr(app.state, "cooldown_gate"), (
        "app.state.cooldown_gate must be set by create_app() (even when None)"
    )
    assert app.state.cooldown_gate is None, (
        f"Expected cooldown_gate=None at default threshold=0, got {app.state.cooldown_gate!r}"
    )


def test_default_router_health_gate_is_none() -> None:
    """create_app() with default settings threads health_gate=None into model_router."""
    settings = _make_settings()
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._health_gate is None, (
        f"Expected router health_gate=None at threshold=0, got {router._health_gate!r}"
    )


def test_threshold_nonzero_creates_redis_cooldown_gate() -> None:
    """create_app() with cooldown_failure_threshold>0 wires a RedisCooldownGate."""
    settings = _make_settings(cooldown_failure_threshold=3)
    app = create_app(settings)

    assert isinstance(app.state.cooldown_gate, RedisCooldownGate), (
        f"Expected RedisCooldownGate at threshold=3, got {type(app.state.cooldown_gate)}"
    )


def test_threshold_nonzero_gate_wired_into_router() -> None:
    """create_app() with threshold>0 passes gate into FallbackModelRouter.health_gate."""
    settings = _make_settings(cooldown_failure_threshold=3)
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._health_gate is app.state.cooldown_gate, (
        "router._health_gate must be the same object as app.state.cooldown_gate"
    )
    assert isinstance(router._health_gate, RedisCooldownGate), (
        f"Expected RedisCooldownGate in router, got {type(router._health_gate)}"
    )


def test_gate_settings_threaded_through() -> None:
    """create_app() threads threshold, ttl_s, window_s into the RedisCooldownGate."""
    settings = _make_settings(
        cooldown_failure_threshold=5,
        cooldown_ttl_s=120,
        cooldown_window_s=90,
    )
    app = create_app(settings)

    gate: RedisCooldownGate = app.state.cooldown_gate
    assert gate._threshold == 5, f"Expected threshold=5, got {gate._threshold}"
    assert gate._ttl_s == 120, f"Expected ttl_s=120, got {gate._ttl_s}"
    assert gate._window_s == 90, f"Expected window_s=90, got {gate._window_s}"


def test_gate_uses_app_redis_client() -> None:
    """The gate reuses app.state.redis_client (no new connection pool)."""
    settings = _make_settings(cooldown_failure_threshold=3)
    app = create_app(settings)

    gate: RedisCooldownGate = app.state.cooldown_gate
    assert gate._redis is app.state.redis_client, (
        "gate._redis must be the same object as app.state.redis_client"
    )


def test_gate_metrics_registry_is_app_registry() -> None:
    """The gate's MetricsRegistry is the same per-app instance as app.state.metrics_registry."""
    settings = _make_settings(cooldown_failure_threshold=3)
    app = create_app(settings)

    gate: RedisCooldownGate = app.state.cooldown_gate
    assert gate._metrics is app.state.metrics_registry, (
        "gate._metrics must be app.state.metrics_registry"
    )


def test_cooldown_transitions_total_counter_registered() -> None:
    """The gateway_cooldown_transitions_total counter is registered on the per-app registry."""
    settings = _make_settings()
    app = create_app(settings)

    registry: CollectorRegistry = app.state.metrics_registry.registry
    metric_names = {m.name for m in registry.collect()}
    assert (
        "gateway_cooldown_transitions" in metric_names
        or "gateway_cooldown_transitions_total" in metric_names
    ), f"Expected 'gateway_cooldown_transitions[_total]' in registry metrics, found: {metric_names}"
