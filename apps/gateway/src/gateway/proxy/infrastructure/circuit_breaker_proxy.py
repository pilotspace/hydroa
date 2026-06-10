"""Circuit breaker proxy that wraps any CompletionUpstream.

This proxy is stored on app.state alongside the inner upstream. Each request,
deps.py constructs a BoundCircuitBreakerUpstream that holds both the stable
CircuitBreaker (from app.state.circuit_breaker) and the current delegate
(from app.state.completion_upstream — may be swapped by tests). The bound
instance implements the CompletionUpstream Protocol, so the use case receives
a single upstream object as usual.

Design rationale:
- CircuitBreaker state is per-app-instance, lives on app.state
- Tests swap app.state.completion_upstream freely; breaker still wraps it
- Per-request binding avoids capturing stale delegate at create_app time
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


class BoundCircuitBreakerUpstream:
    """Per-request view: stable CircuitBreaker + per-request delegate.

    Implements CompletionUpstream Protocol so the use case needs no changes.
    """

    def __init__(self, breaker: CircuitBreaker, delegate: CompletionUpstream) -> None:
        self._breaker = breaker
        self._delegate = delegate

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Guard → delegate.complete → count outcome.

        Raises CircuitOpenError if breaker is open (no upstream call).
        Raises UpstreamUnavailableError on 5xx (increments failure count).
        """
        if not self._breaker.call_allowed():
            raise CircuitOpenError("Circuit breaker is open")

        try:
            status, body = await self._delegate.complete(payload)
        except (UpstreamUnavailableError, CircuitOpenError):
            self._breaker.on_upstream_error()
            raise

        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        self._breaker.record_success()
        return status, body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Guard → delegate.stream — byte-identical pass-through.

        Raises CircuitOpenError immediately if breaker is open.
        On streaming, success is recorded at stream-start (usage reconciled later).
        """
        if not self._breaker.call_allowed():
            raise CircuitOpenError("Circuit breaker is open")

        self._breaker.record_success()
        return self._delegate.stream(payload)
