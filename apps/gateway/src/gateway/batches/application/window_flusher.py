"""BatchWindowFlusher — periodic drain of due BatchWindowBuffer windows into real
batch jobs (batch-window-grouping TASK.md §3 CONTRACT, FROZEN @ v1).

Structurally mirrors RetentionSweeper (usage/application/retention_sweep.py):
  - flush_once() -> dict[str, int]: one sweep cycle over every tenant with an open
    window; claims each due one and drives it to a real job (or a per-item "failed"
    marker on any exception) — never raises.
  - run_forever(interval_seconds, *, _sleep) -> background lifespan task; swallows
    all errors per cycle; propagates CancelledError for clean shutdown. _sleep is
    injectable for deterministic tests (unused by this task's own test suite, which
    drives flush_once() directly instead — see the TEST SEAM note below).
  - should_start_batch_window_flusher(settings) -> bool.

TEST SEAM: httpx's ASGITransport (used by every test `client` fixture in this
codebase) never fires FastAPI's lifespan, so the real background run_forever() task
never runs under test — exactly like BatchJobWorker.process_once() /
RetentionSweeper.sweep_once(), tests construct their OWN BatchWindowFlusher pointed at
the same real Redis + sessionmaker the app uses and call flush_once() directly to
drive exactly one cycle.

Job creation reuses BatchJobRepository.create (unchanged signature — already accepts
a list of line items) and dispatch_batch_job (unchanged — the ONE dispatch path shared
with the explicit POST /v1/batches endpoint), so there is never a second, drifting
copy of either.

FLUSH-FAILURE HANDLING (G10): a claimed group's DB-write failure (or any parse
failure) publishes a "failed" marker for every item in the group — this, plus
BatchWindowBuffer.claim_due's atomically-written "claimed" marker (see that module's
docstring for the double-submit race this closes), is what lets the SSE generator's
bounded wait distinguish "never claimed" from "claimed but the write later failed"
and land on the identical G10 per-item sync fallback either way.

DISPATCH FAILURE (row already committed): unlike a from-scratch create failure, a
dispatch failure after the job row is committed still publishes "submitted" — the row
genuinely exists and IS pollable via GET /v1/batches/{id} even if the background
hand-off itself needs recover_batch_orphans() on the next restart to actually drive
it. This mirrors BatchDiversionAdapter's own pre-existing contract exactly (a
caller-visible reference is always returned once the row is committed).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any

from gateway.batches.api.router import dispatch_batch_job
from gateway.batches.infrastructure.repository import BatchJobRepository

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from gateway.core.config import Settings
    from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

_log = logging.getLogger(__name__)

# Default background-loop tick — a tuning knob, not a contract point (batch-window-
# grouping TASK.md assumption #4). Small so flush-due latency stays well under
# caller-visible significance relative to batch_window_seconds.
DEFAULT_TICK_INTERVAL_SECONDS = 1.0


def should_start_batch_window_flusher(settings: Settings) -> bool:
    """Predicate: start the background flusher loop only when the window is enabled.

    Mirrors should_start_retention_sweep/should_start_batch_worker's shape. A zero (or
    negative) batch_window_seconds is the operator's escape hatch to fully disable the
    sweeper loop; BatchDiversionAdapter itself is unaffected by this predicate (append
    still runs whenever a tenant is opted in — this only gates the background drain).
    """
    return settings.batch_window_seconds > 0


class BatchWindowFlusher:
    """Periodically claims every tenant's due window and drives it to a real job.

    Constructed with:
      buffer               — BatchWindowBuffer instance (shared Redis state).
      sessionmaker          — async_sessionmaker for fresh per-operation sessions.
      app_state             — duck-types FastAPI's app.state; passed straight through
                              to dispatch_batch_job (needs .sessionmaker and,
                              when durable-queue is enabled, .batch_job_queue).
      settings              — frozen-per-request-but-live Settings.
      get_batch_processor   — zero-arg callable returning the current BatchProcessor
                              (or None) — mirrors BatchJobWorker's identical seam,
                              lets tests swap the processor after construction.
    """

    __slots__ = ("_app_state", "_buffer", "_get_batch_processor", "_sessionmaker", "_settings")

    def __init__(
        self,
        *,
        buffer: BatchWindowBuffer,
        sessionmaker: async_sessionmaker[AsyncSession],
        app_state: Any,
        settings: Settings,
        get_batch_processor: Callable[[], Any],
    ) -> None:
        self._buffer = buffer
        self._sessionmaker = sessionmaker
        self._app_state = app_state
        self._settings = settings
        self._get_batch_processor = get_batch_processor

    async def flush_once(self) -> dict[str, int]:
        """One sweep cycle: claim every due tenant window and drive it to a job.

        Returns {"claimed": n_tenant_windows_claimed, "items": n_items_flushed} —
        observability + the TEST SEAM tests call directly (see module docstring).
        NEVER raises: a failure listing active tenants or claiming one tenant's
        window is logged and treated as "nothing this cycle" / "skip this tenant",
        exactly like RetentionSweeper.sweep_once()'s per-table isolation.
        """
        claimed_tenants = 0
        flushed_items = 0
        try:
            tenant_ids = await self._buffer.active_tenant_ids()
        except Exception as exc:
            _log.warning(
                "batch_window_flusher: failed to list active tenants (swallowed)",
                exc_info=exc,
            )
            return {"claimed": 0, "items": 0}

        for tenant_id in tenant_ids:
            try:
                items = await self._buffer.claim_due(tenant_id)
            except Exception as exc:
                _log.warning(
                    "batch_window_flusher: claim_due failed for tenant %s (swallowed)",
                    tenant_id,
                    exc_info=exc,
                )
                continue
            if not items:
                continue
            claimed_tenants += 1
            flushed_items += len(items)
            await self._flush_group(tenant_id, items)

        return {"claimed": claimed_tenants, "items": flushed_items}

    async def _flush_group(self, tenant_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
        """Create + dispatch ONE multi-item job for a just-claimed window, or publish
        a "failed" marker for every item in the group on any exception (G10)."""
        custom_ids = [item["custom_id"] for item in items]
        try:
            # First-accumulated-item's key_id wins for the merged job row (documented,
            # non-blocking assumption #5 in TASK.md §1 — BatchJobItemRow has no
            # per-item key_id column; tenant_id, the real isolation/audit boundary, is
            # still correct on every item row regardless).
            key_id = uuid.UUID(items[0]["key_id"])
            async with self._sessionmaker() as session:
                repo = BatchJobRepository(session)
                job = await repo.create(
                    tenant_id=tenant_id,
                    key_id=key_id,
                    line_items=[{"custom_id": i["custom_id"], "body": i["body"]} for i in items],
                )
                await session.commit()
        except Exception as exc:
            _log.warning(
                "batch_window_flusher: flush failed for tenant %s (falling back "
                "per-item, %d items)",
                tenant_id,
                len(items),
                exc_info=exc,
            )
            for custom_id in custom_ids:
                await self._safe_publish(custom_id, {"kind": "failed"})
            return

        batch_processor = self._get_batch_processor()
        try:
            await dispatch_batch_job(
                job_id=job.id,
                app_state=self._app_state,
                settings=self._settings,
                batch_processor=batch_processor,
            )
        except Exception as exc:
            _log.warning(
                "batch_window_flusher: dispatch failed after job %s was committed; "
                "orphan recovery will pick it up on next restart",
                job.id,
                exc_info=exc,
            )

        poll_url = f"/v1/batches/{job.id}"
        for custom_id in custom_ids:
            await self._safe_publish(
                custom_id,
                {
                    "kind": "submitted",
                    "batch_job_id": str(job.id),
                    "custom_id": custom_id,
                    "poll_url": poll_url,
                },
            )

    async def _safe_publish(self, custom_id: str, result: dict[str, Any]) -> None:
        try:
            await self._buffer.publish_result(custom_id, result)
        except Exception as exc:
            _log.warning(
                "batch_window_flusher: publish_result failed for %s (swallowed)",
                custom_id,
                exc_info=exc,
            )

    async def run_forever(
        self,
        *,
        interval_seconds: float,
        _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Background loop: flush_once() every interval_seconds.

        Swallows all non-CancelledError exceptions (defence in depth — flush_once()
        already swallows internally); propagates CancelledError for clean task
        cancellation from the lifespan shutdown. _sleep is injectable for tests.
        """
        while True:
            try:
                await self.flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.warning(
                    "batch_window_flusher: background loop error (swallowed): %s",
                    exc,
                    exc_info=exc,
                )
            await _sleep(interval_seconds)


__all__ = [
    "DEFAULT_TICK_INTERVAL_SECONDS",
    "BatchWindowFlusher",
    "should_start_batch_window_flusher",
]
