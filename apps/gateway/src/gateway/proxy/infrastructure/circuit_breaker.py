"""In-process circuit breaker for upstream completion calls.

State machine:
  CLOSED  → normal; failure_count tracks consecutive failures
            on 5th consecutive failure → OPEN (trip_time = now)
  OPEN    → no upstream calls; after 30 s cooldown → HALF_OPEN
  HALF_OPEN → single probe request:
              success → CLOSED (failure_count reset)
              failure → OPEN (trip_time reset, 30 s more)

Per-instance (per-replica) as documented in TASK.md §1 assumptions.
Thread-safety: asyncio is single-threaded; no locking needed.
"""

from __future__ import annotations

import time
from enum import Enum

from gateway.proxy.domain.errors import CircuitOpenError

_FAILURE_THRESHOLD = 5
_COOLDOWN_SECONDS = 30.0


class _State(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Decorates an upstream call with circuit-breaker semantics."""

    def __init__(
        self,
        failure_threshold: int = _FAILURE_THRESHOLD,
        cooldown_seconds: float = _COOLDOWN_SECONDS,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._state = _State.CLOSED
        self._failure_count = 0
        self._trip_time: float = 0.0

    def _check_transition(self) -> None:
        """Transition OPEN → HALF_OPEN if cooldown has elapsed."""
        if self._state == _State.OPEN:
            elapsed = time.monotonic() - self._trip_time
            if elapsed >= self._cooldown:
                self._state = _State.HALF_OPEN

    def is_open(self) -> bool:
        """Return True if the breaker is open (or half-open probe slot unavailable)."""
        self._check_transition()
        return self._state == _State.OPEN

    def record_success(self) -> None:
        """Reset breaker to closed on successful upstream response."""
        self._failure_count = 0
        self._state = _State.CLOSED

    def record_failure(self) -> None:
        """Increment failure counter; trip the breaker on threshold."""
        self._failure_count += 1
        if self._failure_count >= self._threshold:
            self._state = _State.OPEN
            self._trip_time = time.monotonic()

    def call_allowed(self) -> bool:
        """Return True iff a call to upstream is permitted right now.

        CLOSED: always True.
        OPEN: False (unless cooldown elapsed → HALF_OPEN → True for one probe).
        HALF_OPEN: True (one probe attempt).
        """
        self._check_transition()
        return self._state != _State.OPEN

    def on_upstream_error(self) -> None:
        """To be called when upstream raises UpstreamUnavailableError.

        Increments failure count and trips if threshold reached.
        In HALF_OPEN: failure re-opens immediately.
        """
        if self._state == _State.HALF_OPEN:
            # Probe failed — re-open immediately
            self._state = _State.OPEN
            self._trip_time = time.monotonic()
        else:
            self.record_failure()

    def guard(self) -> None:
        """Raise CircuitOpenError if no call is allowed right now."""
        if not self.call_allowed():
            raise CircuitOpenError("Circuit breaker is open — upstream is unavailable")
