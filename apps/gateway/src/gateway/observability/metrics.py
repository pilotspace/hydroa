"""Prometheus metrics registry for the gateway.

Uses a per-app CollectorRegistry (never the global prometheus_client default)
to prevent test-suite cross-contamination when multiple create_app() calls
exist in a single pytest run.

Contracted metric families (TASK.md §3 CONTRACT):
  gateway_circuit_breaker_state        gauge   (0=closed, 1=half_open, 2=open)
  gateway_http_requests_total          counter  labels: method, status_code (exact)
  gateway_usage_flusher_pending_events gauge   (XLEN usage:events; -1 on Redis error)
  gateway_request_duration_seconds     histogram labels: method, status_class (2xx/4xx/5xx)
"""

from __future__ import annotations

from typing import Any

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Redis stream key — imported from the infrastructure constant so we never drift
from gateway.usage.infrastructure.redis_stream import STREAM_KEY

# Contract requires Prometheus text format 0.0.4 (TASK.md §3 CONTRACT M4).
# prometheus_client >= 0.14 changed CONTENT_TYPE_LATEST to version=1.0.0 (OpenMetrics).
# We must always serve the classic 0.0.4 text format as contracted.
_CONTENT_TYPE_004 = "text/plain; version=0.0.4; charset=utf-8"

_DURATION_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]

# Breaker state int mapping — must match _State enum in circuit_breaker.py
_BREAKER_STATE_MAP: dict[str, float] = {
    "closed": 0.0,
    "half_open": 1.0,
    "open": 2.0,
}


class MetricsRegistry:
    """Holds all Prometheus metric objects bound to a single CollectorRegistry.

    Pass registry=CollectorRegistry() from create_app() so each app instance
    has isolated metrics — no ValueError: Duplicated timeseries across tests.
    """

    def __init__(self, *, registry: CollectorRegistry) -> None:
        self._registry = registry

        self.http_requests_total = Counter(
            "gateway_http_requests_total",
            "Total HTTP requests by method and exact status code",
            ["method", "status_code"],
            registry=registry,
        )

        self.request_duration_seconds = Histogram(
            "gateway_request_duration_seconds",
            "Request duration in seconds",
            ["method", "status_class"],
            buckets=_DURATION_BUCKETS,
            registry=registry,
        )

        self.circuit_breaker_state = Gauge(
            "gateway_circuit_breaker_state",
            "Circuit breaker state: 0=closed 1=half_open 2=open",
            registry=registry,
        )

        self.usage_flusher_pending_events = Gauge(
            "gateway_usage_flusher_pending_events",
            "Pending events in the usage Redis stream; -1 if Redis unavailable",
            registry=registry,
        )

        self.cache_events_total = Counter(
            "gateway_cache_events_total",
            "Response cache events by result label (hit, miss, bypass)",
            ["result"],
            registry=registry,
        )

        self.guardrail_events_total = Counter(
            "gateway_guardrail_events_total",
            "Guardrail events by guardrail name, mode, and action",
            ["guardrail", "mode", "action"],
            registry=registry,
        )

        self.otel_spans_total = Counter(
            "gateway_otel_spans_total",
            "OTel span export results by outcome",
            ["result"],  # "exported" | "dropped" | "error"
            registry=registry,
        )

        # labels — provider: openrouter|anthropic|gemini
        #          reason: connect_error|pool_timeout|upstream_408|upstream_429|upstream_5xx
        #          outcome: retried|exhausted|breaker_open|deadline_exceeded
        self.upstream_retries_total = Counter(
            "gateway_upstream_retries_total",
            "Upstream retry attempts by provider, reason and outcome",
            ["provider", "reason", "outcome"],
            registry=registry,
        )

        # labels — alias: alias string; from_model: candidate that fell through or was skipped;
        #          to_model: candidate that served or "_exhausted" sentinel;
        #          outcome: fell_through | served | exhausted
        self.model_fallbacks_total = Counter(
            "gateway_model_fallbacks_total",
            "Model-group fallback events by alias, from_model, to_model, and outcome",
            ["alias", "from_model", "to_model", "outcome"],
            registry=registry,
        )

        # labels — model: upstream model id (public catalog id);
        #          transition in {tripped, probe, closed, reopened}
        # Registered unconditionally so metric family appears in /internal/metrics output.
        self.cooldown_transitions_total = Counter(
            "gateway_cooldown_transitions_total",
            "Cooldown circuit breaker state transitions by model and transition type",
            ["model", "transition"],
            registry=registry,
        )

        # credits-ledger TASK.md §3 (M11): incremented every time the credit gate
        # fail-opens on a ledger-store outage — pairs with the structured warning log
        # so the degrade is measurable/alertable, never just log-buried.
        self.credits_gate_degraded_total = Counter(
            "gateway_credits_gate_degraded_total",
            "Credit gate fail-open events (ledger store unreachable) by operation",
            ["operation"],  # "check_and_hold" | "settle" | "release"
            registry=registry,
        )

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry


def state_value(breaker: Any) -> float:
    """Map a CircuitBreaker instance to its gauge int (0/1/2).

    Reads _state (a _State enum) from the breaker.  Returns 0.0 on any
    attribute error so the gauge degrades gracefully rather than crashing.
    """
    try:
        state_name: str = breaker._state.value  # e.g. "closed", "open", "half_open"
        return _BREAKER_STATE_MAP.get(state_name, 0.0)
    except AttributeError:
        return 0.0


async def expose_metrics(app: Any) -> tuple[bytes, str]:
    """Build the Prometheus text body for GET /internal/metrics.

    1. Reads live circuit-breaker state from app.state.circuit_breaker.
    2. Reads Redis stream XLEN lazily; sets -1.0 on any Redis error.
    3. Returns (body_bytes, content_type_string).

    Never raises — Redis/DB errors produce sentinel values, not HTTP errors.
    """
    metrics_registry: MetricsRegistry = app.state.metrics_registry

    # --- live circuit-breaker state ---
    try:
        breaker = app.state.circuit_breaker
        metrics_registry.circuit_breaker_state.set(state_value(breaker))
    except Exception:  # sentinel: set 0 on any breaker read failure
        metrics_registry.circuit_breaker_state.set(0.0)

    # --- flusher pending events (lazy XLEN) ---
    try:
        redis_client = app.state.redis_client
        count = await redis_client.xlen(STREAM_KEY)
        metrics_registry.usage_flusher_pending_events.set(float(count))
    except Exception:  # sentinel -1 per contract R3 — Redis unavailable
        metrics_registry.usage_flusher_pending_events.set(-1.0)

    body = generate_latest(metrics_registry.registry)
    return body, _CONTENT_TYPE_004
