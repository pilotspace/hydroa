"""CreditHoldRecoverySweeper — the M6 backstop for orphaned holds.

A HOLD with no matching SETTLE/RELEASE within hold_timeout_s (a crashed/never-
finalized request — e.g. the process died before usage_recorder.record()'s
settle callback ran) is auto-released by this periodic sweep, so a crashed
request never permanently starves a tenant's balance.

Mirrors OpenRouterRecoverySweeper's shape: check-once + run_forever loop that
swallows all errors, bounded by a max-age window AND a per-cycle batch limit
(no full-table scan under a backlog). NEVER raises into the loop. Reuses
CreditGuard.release() for the actual reversal (the SAME lock/insert/update
path every other release goes through) rather than re-implementing the write.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.credits.domain.ports import CreditGuard

_log = logging.getLogger(__name__)

# Candidate scan: open 'hold' rows (no settle/release sibling) older than the timeout.
_CANDIDATE_SQL = text(
    """
    SELECT h.tenant_id, h.request_id
      FROM credit_ledger h
     WHERE h.entry_type = 'hold'
       AND h.request_id IS NOT NULL
       AND h.created_at < :cutoff
       AND NOT EXISTS (
             SELECT 1 FROM credit_ledger s
              WHERE s.tenant_id = h.tenant_id
                AND s.request_id = h.request_id
                AND s.entry_type IN ('settle', 'release'))
     LIMIT :batch
    """
)


def should_start_credit_recovery_sweep(interval_seconds: int) -> bool:
    """Default-OFF guard — a zero/negative interval leaves the sweep disabled."""
    return interval_seconds > 0


class CreditHoldRecoverySweeper:
    """Periodically release orphaned credit holds older than hold_timeout_s."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        credit_guard: CreditGuard,
        hold_timeout_s: int = 600,
        batch_size: int = 200,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._session_factory = session_factory
        self._credit_guard = credit_guard
        self._hold_timeout_s = hold_timeout_s
        self._batch_size = batch_size
        self._sleep = sleep
        self._monotonic = monotonic

    async def sweep_once(self) -> int:
        """One cycle: release() every orphaned hold older than hold_timeout_s.

        Returns the number of release() attempts made. NEVER raises.
        """
        attempts = 0
        try:
            cutoff = (
                datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(seconds=self._hold_timeout_s)
            ).replace(tzinfo=None)
            async with self._session_factory() as session:
                result = await session.execute(
                    _CANDIDATE_SQL, {"cutoff": cutoff, "batch": self._batch_size}
                )
                candidates = result.all()

            if len(candidates) >= self._batch_size:
                _log.info(
                    "credit_recovery_sweep: batch limit (%d) reached — backlog deferred"
                    " to next cycle",
                    self._batch_size,
                )

            for tenant_id, request_id in candidates:
                try:
                    await self._credit_guard.release(
                        uuid.UUID(str(tenant_id)), uuid.UUID(str(request_id))
                    )
                    attempts += 1
                except Exception as exc:  # one bad row must not abort the cycle
                    _log.warning(
                        "credit_recovery_sweep: release() failed (swallowed)"
                        " tenant=%s request=%s: %s",
                        tenant_id,
                        request_id,
                        exc,
                        exc_info=exc,
                    )
        except Exception as exc:
            _log.warning(
                "credit_recovery_sweep: sweep cycle failed (swallowed): %s", exc, exc_info=exc
            )
        return attempts

    async def run_forever(self, *, interval_seconds: float) -> None:
        """Background loop: sweep_once() every interval_seconds. Swallows all errors."""
        while True:
            try:
                recovered = await self.sweep_once()
                if recovered:
                    _log.info(
                        "credit_recovery_sweep: released %d orphaned hold(s)", recovered
                    )
            except Exception as exc:  # defence in depth — sweep_once already swallows
                _log.warning(
                    "credit_recovery_sweep: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await self._sleep(interval_seconds)


__all__ = ["CreditHoldRecoverySweeper", "should_start_credit_recovery_sweep"]
