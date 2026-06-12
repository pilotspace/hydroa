"""Redis-backed per-model cooldown circuit breaker.

Implements the ModelHealthGate protocol from gateway.proxy.domain.ports.
Contract FROZEN @ cooldown-circuit TASK.md §3 (Amendments B1-B3 binding).

State machine (authoritative order in is_available):
  1. threshold == 0 → True (zero Redis commands).
  2. open key present → False (OPEN state).
  3. half marker present → HALF_OPEN:
       SET probe NX EX ttl_s;
       if NX succeeds → True + emit transition="probe";
       else → False (probe claimed by another caller).
  4. otherwise (no open, no half) → CLOSED → True, read-only, zero Redis writes.

Key shapes (FROZEN):
  gateway:cooldown:fails:{model_id}   — INCR counter; EXPIRE NX cooldown_window_s
  gateway:cooldown:open:{model_id}    — SET "1" EX cooldown_ttl_s on trip
  gateway:cooldown:half:{model_id}    — SET "1" EX (2*cooldown_ttl_s) on trip/re-trip
  gateway:cooldown:probe:{model_id}   — SET "1" NX EX cooldown_ttl_s in HALF_OPEN

Fail-OPEN: any Redis error → log WARNING (no key string, no payload, no credential
material); behave as available / no-op.

B3: model_id is a public catalog id and MAY appear in log fields.
"""

from __future__ import annotations

from typing import Any

import structlog

from gateway.observability.metrics import MetricsRegistry

# Redis key prefixes (FROZEN — contract §3)
_PFX_FAILS = "gateway:cooldown:fails:"
_PFX_OPEN = "gateway:cooldown:open:"
_PFX_HALF = "gateway:cooldown:half:"
_PFX_PROBE = "gateway:cooldown:probe:"


class RedisCooldownGate:
    """Redis-backed per-model cooldown gate implementing ModelHealthGate.

    Constructed once at create_app() when cooldown_failure_threshold > 0.
    When threshold == 0, is_available always returns True and all record_*
    calls are no-ops with zero Redis commands (CC3 guarantee).

    Constructor does NOT connect to Redis — safe to call without lifespan.

    The redis parameter is typed Any because redis.asyncio does not ship
    bundled stubs compatible with mypy strict; using Any avoids attr-defined
    noise while keeping all runtime behaviour correct.
    """

    def __init__(
        self,
        *,
        redis: Any,
        metrics_registry: MetricsRegistry,
        threshold: int,
        ttl_s: int,
        window_s: int,
    ) -> None:
        self._redis: Any = redis
        self._metrics = metrics_registry
        self._threshold = threshold
        self._ttl_s = ttl_s
        self._window_s = window_s

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def is_available(self, model_id: str) -> bool:
        """Return True iff the model should be attempted.

        Authoritative order (per §3 B1 amendment):
          1. threshold == 0 → True (no Redis).
          2. open key present → False (OPEN).
          3. half marker present → HALF_OPEN probe machinery.
          4. otherwise → CLOSED → True (read-only, zero writes).
        """
        # Step 1: fast-path; zero Redis commands.
        if self._threshold == 0:
            return True

        try:
            redis: Any = self._redis

            # Step 2: check OPEN key.
            open_key = _PFX_OPEN + model_id
            open_val = await redis.get(open_key)
            if open_val is not None:
                return False

            # Step 3: check half marker (HALF_OPEN detection — B1).
            half_key = _PFX_HALF + model_id
            half_val = await redis.get(half_key)
            if half_val is not None:
                # HALF_OPEN: attempt to claim probe token with SET NX.
                probe_key = _PFX_PROBE + model_id
                claimed = await redis.set(probe_key, "1", ex=self._ttl_s, nx=True)
                if claimed is True:
                    # This caller owns the probe.
                    self._metrics.cooldown_transitions_total.labels(
                        model=model_id, transition="probe"
                    ).inc()
                    return True
                # Probe already claimed by another caller.
                return False

            # Step 4: CLOSED state — no open key, no half marker.
            # Read-only True; zero Redis writes (B1 regression CC10).
            return True

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            return True  # fail-OPEN

    async def record_failure(self, model_id: str) -> None:
        """Record a retry-exhausted failure for model_id.

        CLOSED → INCR fails; EXPIRE NX window_s; if count >= threshold → trip.
        HALF_OPEN (probe token present) → immediate re-trip (no threshold accumulation).
        """
        if self._threshold == 0:
            return

        try:
            redis: Any = self._redis
            probe_key = _PFX_PROBE + model_id
            half_key = _PFX_HALF + model_id

            # Detect HALF_OPEN by checking whether the probe key or half marker exists.
            # The probe caller set the probe key in is_available; record_failure is
            # called by FallbackModelRouter right after the upstream attempt fails.
            probe_val = await redis.get(probe_key)
            half_val = await redis.get(half_key)

            if probe_val is not None or half_val is not None:
                # HALF_OPEN re-trip: immediate SET open + REFRESH half + DEL probe.
                await self._trip(redis, model_id, transition="reopened")
                return

            # CLOSED → accumulate failure counter.
            fails_key = _PFX_FAILS + model_id
            count = await redis.incr(fails_key)
            # EXPIRE NX: set expiry only if not already set (preserves running window).
            await redis.expire(fails_key, self._window_s, nx=True)

            if count >= self._threshold:
                # Trip: SET open + SET half + DEL fails.
                open_key = _PFX_OPEN + model_id
                await redis.set(open_key, "1", ex=self._ttl_s)
                await redis.set(half_key, "1", ex=2 * self._ttl_s)
                await redis.delete(fails_key)
                self._metrics.cooldown_transitions_total.labels(
                    model=model_id, transition="tripped"
                ).inc()

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            # fail-OPEN: no-op

    async def record_success(self, model_id: str) -> None:
        """Record a successful completion for model_id.

        DEL fails, probe, half (idempotent).
        Emit transition="closed" ONLY when the half marker was actually present
        (i.e., a probe/half-open state was being cleared). Plain successes on a
        CLOSED model (no half marker) emit no transition.
        """
        if self._threshold == 0:
            return

        try:
            redis: Any = self._redis
            fails_key = _PFX_FAILS + model_id
            probe_key = _PFX_PROBE + model_id
            half_key = _PFX_HALF + model_id

            # Check half marker before deleting (determines whether to emit "closed").
            half_val = await redis.get(half_key)

            await redis.delete(fails_key, probe_key, half_key)

            if half_val is not None:
                self._metrics.cooldown_transitions_total.labels(
                    model=model_id, transition="closed"
                ).inc()

        except Exception as exc:
            structlog.get_logger().warning(
                "cooldown_gate_redis_error",
                model_id=model_id,
                error=type(exc).__name__,
            )
            # fail-OPEN: no-op

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _trip(self, redis: Any, model_id: str, transition: str) -> None:
        """SET open key + REFRESH half marker + DEL probe; emit transition metric.

        Used for the HALF_OPEN re-trip path only (transition="reopened");
        the initial trip is inlined in record_failure (it also DELs the
        fails counter, which does not exist in HALF_OPEN).
        """
        open_key = _PFX_OPEN + model_id
        half_key = _PFX_HALF + model_id
        probe_key = _PFX_PROBE + model_id

        await redis.set(open_key, "1", ex=self._ttl_s)
        await redis.set(half_key, "1", ex=2 * self._ttl_s)
        await redis.delete(probe_key)

        self._metrics.cooldown_transitions_total.labels(model=model_id, transition=transition).inc()
