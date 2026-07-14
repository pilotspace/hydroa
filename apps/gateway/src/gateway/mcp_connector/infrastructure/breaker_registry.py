"""Per-(tenant_id, server-host) circuit breaker registry (M8, R6).

A new dimension key on the EXISTING `proxy/infrastructure/circuit_breaker.py` pattern
(Ground §0) — reuses `CircuitBreaker` verbatim (not forked); this module only adds the
keyed-dict bookkeeping a single, process-wide instance doesn't provide. Per-instance
(per-replica), same documented scope as the class it wraps.
"""

from __future__ import annotations

import uuid

from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


class McpCircuitBreakerRegistry:
    """Lazily creates and caches one `CircuitBreaker` per (tenant_id, server_host)."""

    def __init__(self, *, failure_threshold: int = 5, cooldown_seconds: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._breakers: dict[tuple[uuid.UUID, str], CircuitBreaker] = {}

    def get(self, tenant_id: uuid.UUID, server_host: str) -> CircuitBreaker:
        key = (tenant_id, server_host)
        breaker = self._breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=self._failure_threshold,
                cooldown_seconds=self._cooldown_seconds,
            )
            self._breakers[key] = breaker
        return breaker


__all__ = ["McpCircuitBreakerRegistry"]
