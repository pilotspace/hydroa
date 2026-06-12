"""Production-wiring regression tests for the model-fallbacks seam.

v6 foundation rule: every new app.state seam introduced by a task must have a
paired production-wiring regression test asserting that create_app() correctly
threads the Settings fields through to the live infrastructure adapter.

New seams introduced by model-fallbacks task:
  - app.state.model_router: FallbackModelRouter wired with settings.model_groups
  - FallbackModelRouter.health_gate: None (cooldown-circuit task wires the gate later)
  - MetricsRegistry.model_fallbacks_total counter registered on the per-app registry

These tests do NOT touch the frozen model_fallbacks suite.
No DB or Redis required — create_app() is called synchronously; app.state is
inspected without triggering lifespan.

Pattern: tests/retry_policy_wiring/test_retry_policy_wiring.py
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.application.fallback_router import FallbackModelRouter


def _make_settings(**kwargs: object) -> Settings:
    """Build a minimal Settings for unit tests (no live DB/Redis needed)."""
    defaults: dict[str, object] = {
        "database_url": "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
        "jwt_secret": "test-secret-not-for-production-0123456789",
        "redis_url": "redis://localhost:6380/9",
        "environment": "test",
    }
    defaults.update(kwargs)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_model_router_exists_on_app_state() -> None:
    """create_app() wires app.state.model_router as a FallbackModelRouter instance."""
    settings = _make_settings()
    app = create_app(settings)

    assert hasattr(app.state, "model_router"), "app.state.model_router must be set by create_app()"
    assert isinstance(app.state.model_router, FallbackModelRouter), (
        f"Expected FallbackModelRouter, got {type(app.state.model_router)}"
    )


def test_default_model_groups_empty() -> None:
    """create_app() with default settings wires an empty model_groups dict (feature off)."""
    settings = _make_settings()
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._model_groups == {}, (
        f"Expected empty model_groups (feature off), got {router._model_groups!r}"
    )


def test_model_groups_from_settings_threaded_through() -> None:
    """create_app() threads settings.model_groups into the router's _model_groups."""
    groups = {"fast": ["vendor/model-a", "vendor/model-b"]}
    settings = _make_settings(model_groups=groups)
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._model_groups == groups, (
        f"Expected model_groups={groups!r}, got {router._model_groups!r}"
    )


def test_default_health_gate_is_none() -> None:
    """create_app() wires health_gate=None (cooldown-circuit task wires the gate later)."""
    settings = _make_settings()
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._health_gate is None, (
        f"Expected health_gate=None (cooldown-circuit wires it later), got {router._health_gate!r}"
    )


def test_metrics_registry_wired_into_router() -> None:
    """create_app() wires the per-app MetricsRegistry into the model_router."""
    settings = _make_settings()
    app = create_app(settings)

    router: FallbackModelRouter = app.state.model_router
    assert router._metrics_registry is not None, (
        "Expected metrics_registry to be wired into model_router"
    )
    assert router._metrics_registry is app.state.metrics_registry, (
        "Expected model_router._metrics_registry to be app.state.metrics_registry"
    )


def test_model_fallbacks_total_counter_registered() -> None:
    """The gateway_model_fallbacks_total counter is registered on the per-app registry."""
    settings = _make_settings()
    app = create_app(settings)

    registry: CollectorRegistry = app.state.metrics_registry.registry
    metric_names = {m.name for m in registry.collect()}
    assert (
        "gateway_model_fallbacks" in metric_names or "gateway_model_fallbacks_total" in metric_names
    ), f"Expected 'gateway_model_fallbacks[_total]' in registry metrics, found: {metric_names}"
