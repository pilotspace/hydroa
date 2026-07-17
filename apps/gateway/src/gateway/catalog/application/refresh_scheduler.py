"""CatalogRefreshScheduler — periodic re-sync of the DB model catalog.

catalog-celery-refresh TASK.md §3 (change-request v2, Tin 2026-07-16): the mechanism
was switched from a Celery worker + beat to an in-process asyncio background sweeper,
because the repo runs redis 8.x and celery/kombu's redis transport hard-caps redis<6.5
(adding celery would silently downgrade redis 8→6.4, breaking the redis-8 code paths).
An asyncio lifespan sweeper needs no broker and no downgrade, and it is the repo's OWN
dominant precedent for periodic work — RetentionSweeper, OpenRouterRecoverySweeper,
CreditHoldRecoverySweeper, ReconciliationDriftChecker, UpstreamHealthChecker,
BatchWindowFlusher are all `should_start_*(interval) + run_forever(interval, *, _sleep)`
lifespan tasks gated by a `GATEWAY_*_INTERVAL_SECONDS` knob.

Contract (this file):
  - refresh_once() -> int: fetch the current model list from the wired CatalogSource
    and re-sync it into the DB via SyncCatalogUseCase. Returns the count of models
    processed, or 0 on ANY failure (fail-open — a scheduled refresh must never crash
    the loop; the seed migration + last good catalog keep serving).
  - run_forever(interval_seconds, *, _sleep): background loop; runs refresh_once() every
    interval; swallows all non-CancelledError exceptions; propagates CancelledError for
    clean shutdown cancellation. _sleep is injectable for deterministic testing.
  - should_start_catalog_refresh(interval_seconds) -> bool: interval>0 gate.

Design-for-failure: the OpenRouter fetch is network IO. SyncCatalogUseCase already bounds
it (source-level httpx timeout) and guarantees NO partial write — a chat-fetch failure
raises CatalogSourceUnavailableError BEFORE any write, and the repository's sync_catalog
runs in a single atomic `async with session.begin()`. This scheduler adds only a fail-open
wrapper + a self-healing retry cadence (the next tick re-runs the idempotent, provider-
scoped upsert), so a transient upstream outage recovers automatically at the next interval
with no dead-letter/broker machinery.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.catalog.application.use_cases import SyncCatalogUseCase
from gateway.catalog.domain.errors import CatalogSourceUnavailableError
from gateway.catalog.domain.ports import CatalogSource
from gateway.catalog.infrastructure.repository import SqlAlchemyCatalogRepository

_log = logging.getLogger(__name__)


def should_start_catalog_refresh(interval_seconds: int) -> bool:
    """The interval>0 start gate (mirrors should_start_recovery_sweep).

    A zero (or negative) interval disables the scheduler entirely (opt-OUT, no task
    started). The catalog source is always wired on app.state before lifespan startup
    (main.py builds CompositeCatalogSource unconditionally), so — unlike the recovery
    sweep — there is no second "is the collaborator present?" gate here.
    """
    return interval_seconds > 0


class CatalogRefreshScheduler:
    """Periodically re-sync the DB model catalog from the wired CatalogSource.

    Reuses SyncCatalogUseCase verbatim (the SAME class the manual POST /admin/catalog/sync
    route runs) against the app's shared session factory + catalog source — no logic is
    duplicated here; the scheduler only owns the loop + the fail-open wrapper.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        catalog_source: CatalogSource,
    ) -> None:
        self._session_factory = session_factory
        self._source = catalog_source

    async def refresh_once(self) -> int:
        """One refresh cycle: fetch → sync into the DB. Returns models processed, or 0.

        NEVER raises — a CatalogSourceUnavailableError (upstream unreachable) or any other
        failure is logged and returned as 0 so the background loop keeps ticking. Because
        SyncCatalogUseCase guarantees no partial write, a failed cycle leaves the catalog
        BYTE-IDENTICAL to its prior state (self-heals on the next tick).
        """
        try:
            async with self._session_factory() as session:
                repository = SqlAlchemyCatalogRepository(session)
                use_case = SyncCatalogUseCase(self._source, repository)
                count = await use_case.execute()
            _log.info("catalog_refresh: synced %d models from catalog source", count)
            return count
        except CatalogSourceUnavailableError as exc:
            _log.warning(
                "catalog_refresh: source unavailable (swallowed, no row change, retry next"
                " tick): %s",
                exc,
                exc_info=exc,
            )
            return 0
        except Exception as exc:
            _log.warning(
                "catalog_refresh: refresh_once failed (swallowed): %s",
                exc,
                exc_info=exc,
            )
            return 0

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: refresh_once() every interval_seconds.

        Swallows all non-CancelledError exceptions; propagates CancelledError for clean
        task cancellation from the lifespan shutdown. _sleep is injectable for tests.
        Mirrors RetentionSweeper.run_forever (work-then-sleep — an immediate refresh at
        boot, then on cadence).
        """
        while True:
            try:
                await self.refresh_once()
            except asyncio.CancelledError:
                raise  # propagate — shutdown is cancelling us
            except Exception as exc:
                # Defence in depth — refresh_once already swallows, but guard the loop.
                _log.warning(
                    "catalog_refresh: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await _sleep(interval_seconds)
