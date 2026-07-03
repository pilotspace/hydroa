"""Infrastructure implementation: BatchDiversionAdapter.

§3 CONTRACT (batch-auto-grouping TASK.md) — FROZEN @ v1.

Implements the M4 safety-gate (batch-auto-grouping TASK.md §1): a diversion into
async batch processing only happens when the job row is genuinely created AND the
background hand-off is attempted. ANY failure — a missing batch_processor, a DB
error creating the job row — is caught here and signals the caller (via a None
return) to proceed synchronously, exactly as if the tenant's policy were disabled.

Two independent try/except scopes enforce the frozen Safety rule verbatim ("no job
row without a caller-visible reference, no caller-visible reference without a real
job row"):
  1. create + commit the job row — any failure here means no row exists, so
     returning None (fall back to sync) is correct and leaves nothing behind.
  2. dispatch the background task — once the row is committed, a caller-visible
     reference is ALWAYS returned even if dispatch itself unexpectedly raises
     (durable-vs-inline enqueue failure is already handled fail-open inside
     dispatch_batch_job itself; this outer catch is a last-resort defensive net
     for a wiring bug, e.g. a missing app.state attribute). An undispatched
     "validating" row is not silently lost — recover_batch_orphans() picks up any
     non-terminal job on the next restart, same as any other stuck job.

The tenant-policy check itself (authz.batch_grouping_enabled) is NOT read here —
mirroring semantic_cache_enabled's existing convention, the caller (CompletionUseCase
.complete()) already resolved it at authentication time (zero extra DB reads) and
only calls try_divert() when the policy is already known to be enabled.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from gateway.batches.api.router import dispatch_batch_job
from gateway.batches.infrastructure.repository import BatchJobRepository

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from gateway.core.config import Settings

_log = logging.getLogger(__name__)


class BatchDiversionAdapter:
    """Diverts an eligible /v1/chat/completions request into the batch-job-store
    pipeline as a single-line-item job, reusing dispatch_batch_job so there is
    exactly one job-dispatch code path shared with the explicit /v1/batches endpoint.

    Constructed once at startup (main.py) — cheap, holds only stable references.
    """

    __slots__ = ("_app_state", "_sessionmaker", "_settings")

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[Any],
        app_state: Any,
        settings: Settings,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._app_state = app_state
        self._settings = settings

    async def try_divert(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        body: dict[str, Any],
        batch_processor: object | None,
    ) -> dict[str, Any] | None:
        """Return a batch-reference envelope dict when genuinely diverted, else None.

        NEVER raises — every failure mode is caught and logged; a None result
        signals the caller to proceed synchronously, unchanged.
        """
        if batch_processor is None:
            # M4 safety branch (d): no processor configured — this is what makes
            # enabling the tenant policy pre-adapter a no-op, not a landmine.
            return None

        custom_id = str(uuid.uuid4())

        try:
            async with self._sessionmaker() as session:
                repo = BatchJobRepository(session)
                job = await repo.create(
                    tenant_id=tenant_id,
                    key_id=key_id,
                    line_items=[{"custom_id": custom_id, "body": body}],
                )
                await session.commit()
        except Exception:
            _log.warning(
                "batch diversion hand-off failed creating job row; falling back to sync",
                exc_info=True,
            )
            return None

        # The row now exists — from here on a caller-visible reference is ALWAYS
        # returned (frozen Safety rule: no job row without a caller-visible
        # reference). dispatch_batch_job already fails open internally for the
        # durable-vs-inline choice; this catch is a last-resort net for a wiring
        # bug — an undispatched "validating" row is recovered by
        # recover_batch_orphans() on the next restart, never silently lost.
        try:
            await dispatch_batch_job(
                job_id=job.id,
                app_state=self._app_state,
                settings=self._settings,
                batch_processor=batch_processor,
            )
        except Exception:
            _log.warning(
                "batch diversion dispatch failed after job row %s was committed; "
                "orphan recovery will pick it up on next restart",
                job.id,
                exc_info=True,
            )

        return {
            "id": f"batchref_{uuid.uuid4()}",
            "object": "chat.completion.batch_reference",
            "status": "queued",
            "batch_job_id": str(job.id),
            "custom_id": custom_id,
            "poll_url": f"/v1/batches/{job.id}",
        }


__all__ = ["BatchDiversionAdapter"]
