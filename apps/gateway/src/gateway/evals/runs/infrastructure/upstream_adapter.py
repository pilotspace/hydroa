"""Per-tenant breaker + concurrency for eval dials (eval-run-executor §3, M4 — the HARD-STOP).

[[per-tenant-breaker-recurring-defect]]: new provider surfaces keep shipping a PROCESS-GLOBAL
breaker → one tenant's failing burst degrades everyone (cross-tenant DoS). An eval run is a
BURST of billed upstream calls, so it is exactly the shape that trips a shared breaker. The
live completion path's own breaker (``BoundCircuitBreakerUpstream`` over
``app.state.circuit_breaker``) is global; reusing it here would let tenant A's run open the
breaker that guards tenant B's LIVE traffic (R:GLOBAL_BREAKER).

So the executor injects its OWN upstream into ``CompletionUseCase.complete(upstream=...)``:
``TenantBreakerUpstream`` wraps the raw ``app.state.completion_upstream`` delegate with a
breaker + concurrency semaphore drawn from ``TenantExecutionRegistry``, keyed by ``tenant_id``.
An eval dial then trips ONLY that tenant's eval-breaker; the live global breaker is never
touched by a run, and one tenant's burst can never exhaust another's concurrency slots.

Every dial also carries a per-call TIMEOUT (M4): a hung provider fails the case CLOSED
(``errored``) via ``UpstreamUnavailableError`` — it never hangs the run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# Sensible eval-burst defaults; overridable from Settings at construction (A3).
_DEFAULT_CONCURRENCY = 4
_DEFAULT_TIMEOUT_SECONDS = 30.0


class TenantExecutionRegistry:
    """Lazily-allocated per-tenant CircuitBreaker + concurrency Semaphore.

    One instance is owned by the executor (NOT app.state.circuit_breaker). ``dict.setdefault``
    keying by ``tenant_id`` is the same lazy-per-key idiom deps.py uses for the per-provider
    breaker registry — a trip / slot on one tenant never touches another's.
    """

    def __init__(
        self,
        *,
        concurrency: int = _DEFAULT_CONCURRENCY,
        failure_threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._concurrency = max(1, concurrency)
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._breakers: dict[Any, CircuitBreaker] = {}
        self._semaphores: dict[Any, asyncio.Semaphore] = {}

    def breaker_for(self, tenant_id: Any) -> CircuitBreaker:
        breaker = self._breakers.get(tenant_id)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=self._failure_threshold,
                cooldown_seconds=self._cooldown_seconds,
            )
            self._breakers[tenant_id] = breaker
        return breaker

    def semaphore_for(self, tenant_id: Any) -> asyncio.Semaphore:
        sem = self._semaphores.get(tenant_id)
        if sem is None:
            sem = asyncio.Semaphore(self._concurrency)
            self._semaphores[tenant_id] = sem
        return sem


class TenantBreakerUpstream:
    """CompletionUpstream that guards a delegate with a tenant-keyed breaker + per-call timeout.

    Mirrors ``BoundCircuitBreakerUpstream`` (guard → delegate → count outcome) but the breaker
    is the PER-TENANT one from the registry, and every dial is bounded by ``timeout_seconds``.
    Breaker-open and timeout BOTH fail closed (``UpstreamUnavailableError``) so the executor
    records the case ``errored`` and the run continues (M4, A6/E5).
    """

    def __init__(
        self,
        breaker: CircuitBreaker,
        delegate: CompletionUpstream,
        *,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._breaker = breaker
        self._delegate = delegate
        self._timeout = timeout_seconds

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not self._breaker.call_allowed():
            # This tenant's eval-breaker is open — never dial, never touch another tenant.
            raise CircuitOpenError("Eval breaker is open for this tenant")
        try:
            status, body = await asyncio.wait_for(
                self._delegate.complete(payload), timeout=self._timeout
            )
        except TimeoutError as exc:
            # Per-call timeout (M4): count it as an upstream failure and fail the case closed.
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError("Eval upstream call timed out") from exc
        except (UpstreamUnavailableError, CircuitOpenError):
            self._breaker.on_upstream_error()
            raise
        if status >= 500:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(f"Eval upstream returned {status}")
        self._breaker.record_success()
        return status, body

    def stream(self, payload: dict[str, Any]) -> Any:
        # Eval replays are non-streaming (complete() only). A stream() is never invoked on this
        # path; provide it so the object still satisfies the CompletionUpstream Protocol shape.
        raise NotImplementedError("eval runs do not stream")
