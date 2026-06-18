"""ReconciliationDriftChecker — periodic operator-wide unbilled-upstream leak monitor.

CONTRACT (FROZEN @ drift-alert v1 — TASK.md §3):
  - check_once()  -> reconcile today's UTC window operator-wide; if unbilled_upstream_cost
                     > threshold, emit ONE deduped "reconciliation_drift" system event.
  - run_forever() -> background loop every interval_seconds; swallows all errors.
  - Dedup: dedupe_key = "reconciliation_drift:{YYYYMMDD}" -> ON CONFLICT DO NOTHING -> <=1/day.
  - Operator-wide (tenant_id=None); the emitted row is a SYSTEM event (tenant_id NULL).
  - READ + alert-write only (never mutates the ledger). Delivery is the existing dispatcher's job.
  - Default-OFF: the lifespan starts it only when both config knobs are > 0.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.alerting.application.event_emitter import emit_system_event
from gateway.usage.application.reconciliation import reconcile_window

_log = logging.getLogger(__name__)


def should_start_drift_checker(threshold: Decimal, interval_seconds: int) -> bool:
    """The default-OFF guard: start the periodic checker ONLY when BOTH knobs are > 0.

    A zero (or negative) threshold or interval leaves the monitor disabled — the safe default —
    so the lifespan never wires a checker an operator did not explicitly turn on.
    """
    return threshold > 0 and interval_seconds > 0


class ReconciliationDriftChecker:
    """Periodically reconcile the operator-wide ledger and alert on an unbilled-upstream leak."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        threshold: Decimal,
    ) -> None:
        self._session_factory = session_factory
        self._threshold = threshold

    async def check_once(self) -> None:
        """One cycle: reconcile today's UTC window operator-wide; emit on a leak. NEVER raises."""
        try:
            now = datetime.datetime.now(datetime.UTC)
            window_from = now.replace(hour=0, minute=0, second=0, microsecond=0)
            window_to = window_from + datetime.timedelta(days=1)

            async with self._session_factory() as session:
                summary = await reconcile_window(session, window_from, window_to, tenant_id=None)

            if summary.unbilled_upstream_cost > self._threshold:
                _log.warning(
                    "drift_checker: unbilled-upstream leak %s > threshold %s over %d row(s)"
                    " — emitting reconciliation_drift alert",
                    summary.unbilled_upstream_cost,
                    self._threshold,
                    summary.unbilled_rows,
                )
                await emit_system_event(
                    self._session_factory,
                    event_type="reconciliation_drift",
                    dedupe_key=f"reconciliation_drift:{window_from:%Y%m%d}",
                    payload={
                        "window_from": window_from.isoformat(),
                        "window_to": window_to.isoformat(),
                        "unbilled_upstream_cost": str(summary.unbilled_upstream_cost),
                        "unbilled_rows": summary.unbilled_rows,
                        "drift": str(summary.drift),
                        "threshold": str(self._threshold),
                    },
                )
        except Exception as exc:
            _log.warning("drift_checker: check cycle failed (swallowed): %s", exc, exc_info=exc)

    async def run_forever(self, *, interval_seconds: float) -> None:
        """Background loop: check_once() every interval_seconds. Swallows all errors."""
        while True:
            try:
                await self.check_once()
            except Exception as exc:  # defence in depth — check_once already swallows
                _log.warning(
                    "drift_checker: background loop error (swallowed): %s", exc, exc_info=exc
                )
            await asyncio.sleep(interval_seconds)
