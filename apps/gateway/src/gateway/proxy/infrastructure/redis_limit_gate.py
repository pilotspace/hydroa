"""Redis-backed per-deployment RPM/TPM saturation gate.

Implements the DeploymentLimitGate protocol from gateway.proxy.domain.ports.
Contract FROZEN @ deployment-limits TASK.md §3.

Key shapes (FROZEN):
  gateway:deplimit:rpm:{deployment_id}:{bucket}  INT  (record_request: INCR + EXPIRE window_s)
  gateway:deplimit:tpm:{deployment_id}:{bucket}  INT  (record_tokens:  INCRBY tokens + EXPIRE)

where bucket = floor(now / window_s)  — per-minute fixed window.

Fail-OPEN: any Redis error in is_saturated -> False (admit; never a false-429, never a 500).
record_* errors are fail-soft (swallowed; logged with deployment_id only — never key strings).

Constructor does NOT connect to Redis — safe to call without lifespan, same as
RedisDeploymentLoadGate and RedisCooldownGate.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import structlog

# Redis key prefixes (FROZEN — contract §3)
_PFX_RPM = "gateway:deplimit:rpm:"
_PFX_TPM = "gateway:deplimit:tpm:"


class RedisDeploymentLimitGate:
    """Redis-backed per-deployment limit gate implementing DeploymentLimitGate.

    Constructed once at create_app() when any configured deployment declares an
    rpm_limit or tpm_limit. Constructor does NOT connect to Redis (safe without
    lifespan).

    The redis parameter is typed Any because redis.asyncio does not ship bundled
    stubs compatible with pyright strict; using Any avoids attr-defined noise while
    keeping all runtime behaviour correct.
    """

    # `now` is injectable so a test can PIN the window (todo #111). The bucket is
    # `floor(now / WINDOW)`, so a test that fires N requests and expects the (N+1)th to be
    # rejected is silently assuming no window boundary falls between them. Nothing measured
    # that assumption, and it fails a few percent of the time under load — which reads as a
    # limiter regression, not as a test artifact. Both clock reads below MUST come from this
    # one source: a bucket and a retry_after taken from separate `time.time()` calls can
    # straddle a boundary and disagree with each other.
    def __init__(
        self,
        *,
        redis: Any,
        window_s: int = 60,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._redis: Any = redis
        self._window_s = window_s
        self._now = now

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _bucket(self) -> int:
        """Return the current per-minute bucket index (floor(now / window_s))."""
        return int(self._now()) // self._window_s

    def _rpm_key(self, deployment_id: str, bucket: int) -> str:
        return f"{_PFX_RPM}{deployment_id}:{bucket}"

    def _tpm_key(self, deployment_id: str, bucket: int) -> str:
        return f"{_PFX_TPM}{deployment_id}:{bucket}"

    # ------------------------------------------------------------------
    # Public protocol methods
    # ------------------------------------------------------------------

    async def is_saturated(
        self,
        deployment_id: str,
        rpm_limit: int | None,
        tpm_limit: int | None,
    ) -> bool:
        """Return True iff deployment has exceeded its RPM or TPM limit this window.

        Both-None fast path: returns False with zero Redis IO.
        Fail-OPEN: any Redis error returns False (admit; never a false-429).
        """
        # Fast path: no limits configured -> never saturated, zero Redis IO.
        if rpm_limit is None and tpm_limit is None:
            return False

        try:
            bucket = self._bucket()

            if rpm_limit is not None:
                rpm_val = await self._redis.get(self._rpm_key(deployment_id, bucket))
                rpm_count = int(rpm_val) if rpm_val is not None else 0
                if rpm_count >= rpm_limit:
                    return True

            if tpm_limit is not None:
                tpm_val = await self._redis.get(self._tpm_key(deployment_id, bucket))
                tpm_count = int(tpm_val) if tpm_val is not None else 0
                if tpm_count >= tpm_limit:
                    return True

            return False

        except Exception as exc:
            structlog.get_logger().warning(
                "limit_gate_is_saturated_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )
            # Fail-OPEN: admit the candidate rather than block traffic on Redis outage.
            return False

    async def record_request(self, deployment_id: str) -> None:
        """Increment the per-minute RPM window for deployment_id (INCR + EXPIRE).

        Errors are swallowed (logged with deployment_id only — never key strings).
        """
        try:
            bucket = self._bucket()
            key = self._rpm_key(deployment_id, bucket)
            await self._redis.incr(key)
            await self._redis.expire(key, self._window_s)
        except Exception as exc:
            structlog.get_logger().warning(
                "limit_gate_record_request_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )

    async def record_tokens(self, deployment_id: str, tokens: int) -> None:
        """Add tokens to the per-minute TPM window for deployment_id (INCRBY + EXPIRE).

        Errors are swallowed (logged with deployment_id only — never key strings).
        """
        try:
            bucket = self._bucket()
            key = self._tpm_key(deployment_id, bucket)
            await self._redis.incrby(key, tokens)
            await self._redis.expire(key, self._window_s)
        except Exception as exc:
            structlog.get_logger().warning(
                "limit_gate_record_tokens_error",
                deployment_id=deployment_id,
                error=type(exc).__name__,
            )
