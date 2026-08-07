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

import asyncio
import time
import uuid
from enum import Enum
from typing import Any

from gateway.proxy.domain.errors import CircuitOpenError

_FAILURE_THRESHOLD = 5
_COOLDOWN_SECONDS = 30.0


def status_counts_as_upstream_failure(status: int) -> bool:
    """Does this HTTP status mean the UPSTREAM is in trouble?

    True for 408, 429, and every status >= 500. False for everything else — including
    every other 4xx, which is a terminal-but-successful round trip: the upstream answered,
    correctly, that the CALLER got something wrong.

    Feeding a 401/403/404/409 to `on_upstream_error()` is a live availability bug, not a
    style question. A tenant whose BYOK key is revoked gets five 401s, their own breaker
    opens, and their CORRECTED traffic then 502s for the whole cooldown — the gateway
    punishes the fix.

    This is the bool twin of `RetryPolicy.classify_status(status) is not None` in
    `upstream_retry.py`. It cannot import that module (upstream_retry imports THIS one),
    so the two are kept honest by a test that walks every status in 100..599 and asserts
    they agree — see tests/breaker_4xx_classification. Collapsing classify_status onto
    this predicate is the right end state; it was out of this task's frozen scope.
    """
    return status in (408, 429) or status >= 500


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
        *,
        session_factory: Any = None,
    ) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._state = _State.CLOSED
        self._failure_count = 0
        self._trip_time: float = 0.0
        # Event emission hook (health-alerting): injected session_factory for alert_events
        self._session_factory = session_factory
        # Episode UUID — set when CLOSED→OPEN; cleared when back to CLOSED
        self._open_episode_id: uuid.UUID | None = None

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
        self._open_episode_id = None

    def record_failure(self) -> None:
        """Increment failure counter; trip the breaker on threshold."""
        self._failure_count += 1
        if self._failure_count >= self._threshold and self._state != _State.OPEN:
            self._state = _State.OPEN
            self._trip_time = time.monotonic()
            self._emit_open_event()

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
            # Probe failed — re-open immediately; new episode
            self._state = _State.OPEN
            self._trip_time = time.monotonic()
            self._emit_open_event()
        else:
            self.record_failure()

    def guard(self) -> None:
        """Raise CircuitOpenError if no call is allowed right now."""
        if not self.call_allowed():
            raise CircuitOpenError("Circuit breaker is open — upstream is unavailable")

    def _emit_open_event(self) -> None:
        """Fire-and-forget: emit circuit_breaker_open alert event (one per episode).

        Only emits when session_factory is wired (production/test with DB).
        Swallows all exceptions — must never raise into the breaker path.
        """
        if self._session_factory is None:
            return
        try:
            # Assign a fresh episode UUID for this open transition
            self._open_episode_id = uuid.uuid4()
            episode_id = self._open_episode_id
            session_factory = self._session_factory
            from gateway.alerting.application.event_emitter import emit_system_event

            task = asyncio.ensure_future(
                emit_system_event(
                    session_factory,
                    event_type="circuit_breaker_open",
                    dedupe_key=f"breaker_open:{episode_id}",
                    payload={"state": "open"},
                )
            )
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception:  # noqa: S110 — must never raise into the breaker path
            # Event emission is best-effort; the breaker state change already
            # succeeded and the gauge/logs still record the transition.
            pass
