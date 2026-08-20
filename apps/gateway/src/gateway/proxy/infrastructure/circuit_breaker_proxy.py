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
from gateway.proxy.infrastructure.tenant_breaker_registry import (
    TenantScopedBreakerMixin,
    TenantScopedBreakerRegistry,
)


class BoundCircuitBreakerUpstream(TenantScopedBreakerMixin):
    """Per-request view: a stable PER-TENANT breaker registry + per-request delegate.

    Implements CompletionUpstream Protocol so the use case needs no changes.

    This wrapper CONSTRUCTS no breaker — it is handed a registry — so a
    construction-site census is structurally blind to it. That blindness is
    exactly why it kept its process-wide breaker through three previous passes at
    this defect class while every construction site around it was being fixed. It
    now takes a ``TenantScopedBreakerRegistry`` (never a bare ``CircuitBreaker``)
    so there is no signature by which a caller can hand it one shared breaker.
    """

    def __init__(self, breakers: TenantScopedBreakerRegistry, delegate: CompletionUpstream) -> None:
        self._init_tenant_breakers(breakers)
        self._delegate = delegate

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """Guard → delegate.complete → count outcome.

        Raises CircuitOpenError if breaker is open (no upstream call).
        Raises UpstreamUnavailableError on 5xx (increments failure count).
        """
        breaker = self._breaker_for()
        if not breaker.call_allowed():
            raise CircuitOpenError("Circuit breaker is open")

        try:
            status, body = await self._delegate.complete(payload)
        except (UpstreamUnavailableError, CircuitOpenError):
            breaker.on_upstream_error()
            raise

        if status >= 500:
            breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Upstream returned {status}")

        breaker.record_success()
        return status, body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        """Guard → delegate.stream — byte-identical pass-through.

        Raises CircuitOpenError immediately if breaker is open.
        On streaming, success is recorded at stream-start (usage reconciled later).
        """
        # A10: resolved eagerly here, never inside the returned iterator.
        breaker = self._breaker_for()
        if not breaker.call_allowed():
            raise CircuitOpenError("Circuit breaker is open")

        breaker.record_success()
        return self._delegate.stream(payload)
