"""Redis-backed per-deployment load metrics gate.

Implements the DeploymentLoadGate protocol from gateway.proxy.domain.ports.
Contract FROZEN @ balance-strategies TASK.md §3.

Key shapes (FROZEN):
  gateway:loadbal:inflight:{deployment_id}  — INT  (INCR on acquire + EXPIRE refresh;
                                                     DECR on release; clamp negative→0)
  gateway:loadbal:ewma:{deployment_id}      — FLOAT (record_latency: ewma = a*sample
                                                     + (1-a)*prev; unseen -> 0.0)

Fail-OPEN: any Redis error → log WARNING (deployment_id only — no key strings, no
credentials); return neutral value (in_flight=0 / ewma=0.0); acquire/release errors
swallowed.

Constructor does NOT connect to Redis — safe to call without lifespan, same as
RedisCooldownGate.
"""

from __future__ import annotations

from typing import Any

import structlog

# Redis key prefixes (FROZEN — contract §3)
_PFX_INFLIGHT = "gateway:loadbal:inflight:"
_PFX_EWMA = "gateway:loadbal:ewma:"


class RedisDeploymentLoadGate:
    """Redis-backed per-deployment load gate implementing DeploymentLoadGate.

    Constructed once at create_app() when routing_strategy ∈ {least-busy, latency}.
    Constructor does NOT connect to Redis (safe without lifespan).

    The redis parameter is typed Any because redis.asyncio does not ship bundled
    stubs compatible with mypy strict; using Any avoids attr-defined noise while
    keeping all runtime behaviour correct.
    """

    def __init__(
        self,
        *,
        redis: Any,
        alpha: float,
        in_flight_ttl_s: int,
    ) -> None:
        self._redis: Any = redis
        self._alpha = alpha
        self._ttl_s = in_flight_ttl_s

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def acquire(self, deployment_id: str) -> None:
        """INCR in-flight counter and refresh EXPIRE(ttl_s) for the key.

        Errors are swallowed (logged with deployment_id only — never key strings).
        """
        try:
            key = _PFX_INFLIGHT + deployment_id
            await self._redis.incr(key)
            await self._redis.expire(key, self._ttl_s)
        except Exception as exc:
            structlog.get_logger().warning(
                "load_gate_acquire_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )

    async def release(self, deployment_id: str) -> None:
        """DECR in-flight counter.

        Errors are swallowed (logged with deployment_id only — never key strings).
        The in_flight() read clamps negative→0 so a raw negative value is benign.
        """
        try:
            key = _PFX_INFLIGHT + deployment_id
            await self._redis.decr(key)
        except Exception as exc:
            structlog.get_logger().warning(
                "load_gate_release_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )

    async def in_flight(self, deployment_id: str) -> int:
        """Return current in-flight count (≥ 0); fail-OPEN → 0 on error."""
        try:
            key = _PFX_INFLIGHT + deployment_id
            val = await self._redis.get(key)
            return max(0, int(val)) if val is not None else 0
        except Exception as exc:
            structlog.get_logger().warning(
                "load_gate_inflight_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )
            return 0

    async def record_latency(self, deployment_id: str, latency_ms: float) -> None:
        """Update EWMA: ewma = alpha * sample + (1 - alpha) * prev.

        First sample (unseen): prev = 0.0, so ewma = alpha * sample.
        Errors are swallowed (logged with deployment_id only).
        """
        try:
            key = _PFX_EWMA + deployment_id
            raw = await self._redis.get(key)
            prev = float(raw) if raw is not None else 0.0
            ewma = self._alpha * latency_ms + (1.0 - self._alpha) * prev
            await self._redis.set(key, str(ewma))
        except Exception as exc:
            structlog.get_logger().warning(
                "load_gate_record_latency_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )

    async def latency_ewma(self, deployment_id: str) -> float:
        """Return recent EWMA latency in ms; unseen deployment → 0.0; fail-OPEN → 0.0."""
        try:
            key = _PFX_EWMA + deployment_id
            val = await self._redis.get(key)
            return float(val) if val is not None else 0.0
        except Exception as exc:
            structlog.get_logger().warning(
                "load_gate_ewma_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )
            return 0.0
