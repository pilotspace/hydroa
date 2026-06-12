"""Production-wiring regression tests for the retry-policy seam.

v6 foundation rule: every new app.state seam introduced by a task must have a
paired production-wiring regression test asserting that create_app() correctly
threads the Settings fields through to the live infrastructure adapter.

New seams introduced by retry-policy task:
  - Settings.upstream_max_retries → OpenRouterCompletionUpstream._max_retries
  - Settings.upstream_retry_backoff_base_s → OpenRouterCompletionUpstream._backoff_base
  - OpenRouterCompletionUpstream.metrics_registry → MetricsRegistry with
    upstream_retries_total counter registered on the per-app registry

These tests do NOT touch the frozen retry_policy suite.
No DB or Redis required — create_app() is called synchronously; app.state is
inspected without triggering lifespan.
"""

from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from gateway.core.config import Settings
from gateway.main import create_app
from gateway.proxy.infrastructure.openrouter_upstream import OpenRouterCompletionUpstream


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


def test_default_wiring_max_retries_zero() -> None:
    """create_app() with default settings wires _max_retries=0 into completion_upstream."""
    settings = _make_settings()
    app = create_app(settings)

    # v9: the raw OpenRouter adapter relocated to app.state.openrouter_completion_upstream
    # (app.state.completion_upstream is now the provider-dispatch wrapper). The Settings →
    # adapter threading invariant is unchanged; assert it against the raw adapter.
    upstream = app.state.openrouter_completion_upstream
    assert isinstance(upstream, OpenRouterCompletionUpstream)
    assert upstream._max_retries == 0, (
        f"Expected _max_retries=0 (default), got {upstream._max_retries}"
    )


def test_custom_max_retries_wired() -> None:
    """create_app() with upstream_max_retries=3 threads through to completion_upstream."""
    settings = _make_settings(upstream_max_retries=3)
    app = create_app(settings)

    upstream = app.state.openrouter_completion_upstream  # v9: raw adapter relocated
    assert upstream._max_retries == 3, f"Expected _max_retries=3, got {upstream._max_retries}"


def test_custom_backoff_base_wired() -> None:
    """create_app() with upstream_retry_backoff_base_s=1.5 threads through to completion_upstream."""
    settings = _make_settings(upstream_retry_backoff_base_s=1.5)
    app = create_app(settings)

    upstream = app.state.openrouter_completion_upstream  # v9: raw adapter relocated
    assert upstream._backoff_base == 1.5, (
        f"Expected _backoff_base=1.5, got {upstream._backoff_base}"
    )


def test_metrics_registry_wired() -> None:
    """create_app() wires the per-app MetricsRegistry into completion_upstream."""
    settings = _make_settings()
    app = create_app(settings)

    upstream = app.state.openrouter_completion_upstream  # v9: raw adapter relocated
    assert upstream._metrics_registry is not None, (
        "Expected metrics_registry to be wired into completion_upstream"
    )
    assert upstream._metrics_registry is app.state.metrics_registry, (
        "Expected completion_upstream._metrics_registry to be app.state.metrics_registry"
    )


def test_upstream_retries_total_counter_registered() -> None:
    """The gateway_upstream_retries_total counter is registered on the per-app registry."""
    settings = _make_settings()
    app = create_app(settings)

    registry: CollectorRegistry = app.state.metrics_registry.registry
    # Verify the counter family exists by checking its name in the registry
    # prometheus_client stores Counter name without the _total suffix in m.name;
    # the full time-series name (with _total) appears in m.samples[*].name.
    metric_names = {m.name for m in registry.collect()}
    # Accept either the base name or the full _total name (version-independent)
    assert (
        "gateway_upstream_retries" in metric_names
        or "gateway_upstream_retries_total" in metric_names
    ), f"Expected 'gateway_upstream_retries[_total]' in registry metrics, found: {metric_names}"
