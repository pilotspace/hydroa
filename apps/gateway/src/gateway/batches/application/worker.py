"""BatchJobWorker — durable Redis-backed in-process worker for batch chat-completion jobs.

Structurally copied from gateway.video.application.worker (MILESTONE.md v57 shared decision
"batch_jobs table + status machine + durable-queue/worker shape (copied from
video_generation_jobs)"):
  - RedisBatchJobQueue wraps a redis.asyncio client, exposes enqueue/claim on the
    ``batch:jobs:pending`` list key (LPUSH / BRPOP).
  - BatchJobWorker.run_forever() is the long-running lifespan task. It claims one job at a
    time and drives it via _drive(). A single-job exception never kills the loop.
  - BatchJobWorker.process_once() is the TEST SEAM: claim + drive exactly one id, return
    False on claim-timeout (queue empty), True otherwise.
  - recover_orphans() is called at startup (before run_forever) to re-enqueue any rows that
    were non-terminal when the gateway last restarted.
  - should_start_batch_worker() mirrors should_start_video_worker().

AT-LEAST-ONCE + IDEMPOTENCY guarantees:
  - increment_retry / set_in_progress / set_failed all carry the existing status-guard, so a
    re-driven or duplicated worker never corrupts a terminal result.
  - A stale/missing job id is logged + skipped (no re-enqueue, no raise).
  - A terminal job claimed from the queue is skipped immediately (idempotent).

Default-OFF: with batch_durable_queue_enabled=False the router uses an inline
asyncio.create_task path unchanged. This module is imported but the worker is never started.

Fail-open: if enqueue raises (Redis down / network partition), the router falls back to the
inline asyncio.create_task path so no job is ever dropped.

This task's only reachable terminal is "failed" — no real BatchProcessor is wired yet
(openai-batch-adapter / anthropic-batch-adapter, downstream tasks, plug one in later).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.batches.infrastructure.orm import BatchJobRow
from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.core.config import Settings

_log = logging.getLogger(__name__)

# Redis list key — single global queue shared by all worker instances.
_QUEUE_KEY = "batch:jobs:pending"

# Job-level terminal statuses (OpenAI's vocabulary) — a job in one of these states must
# not be re-driven; recover_orphans/increment_retry treat everything else as non-terminal.
_JOB_TERMINAL_STATUSES = ("failed", "completed", "expired", "cancelled")

# BRPOP claim timeout (seconds). Must be > 0 (0 = block forever, unsafe for graceful
# shutdown). 2 s gives a responsive CancelledError on shutdown.
_CLAIM_TIMEOUT_RUN_FOREVER = 2
_CLAIM_TIMEOUT_PROCESS_ONCE = 1

# LPUSH has no inherent timeout (unlike BRPOP's blocking-timeout parameter) — a hung
# connection (partition mid-op) would otherwise block the POST /v1/batches request
# handler indefinitely. Bounded so a hang degrades to the router's existing fail-open
# fallback (asyncio.TimeoutError is an Exception subclass) instead of hanging the request.
_ENQUEUE_TIMEOUT_SECONDS = 2.0


class RedisBatchJobQueue:
    """Thin wrapper around a redis.asyncio client for the batch job queue.

    enqueue: LPUSH job_id string to the left of batch:jobs:pending.
    claim:   BRPOP from the right (FIFO); returns None on timeout.
    """

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def enqueue(self, job_id: uuid.UUID) -> None:
        """Push job_id onto the left of the pending queue.

        Bounded by _ENQUEUE_TIMEOUT_SECONDS — see its module-level docstring. Raises
        (TimeoutError included) on failure; the caller (router) is responsible for the
        fail-open fallback.
        """
        await asyncio.wait_for(
            self._redis.lpush(_QUEUE_KEY, str(job_id)),
            timeout=_ENQUEUE_TIMEOUT_SECONDS,
        )

    async def claim(self, timeout: float) -> uuid.UUID | None:  # noqa: ASYNC109
        """Block-pop from the right of the pending queue.

        Returns None when the timeout expires without a job being available.
        timeout must be > 0; 0 blocks indefinitely (unsafe for shutdown).
        Note: ASYNC109 suppressed — this timeout is a Redis BRPOP parameter, not an
        asyncio.timeout wrapper; renaming it would obscure its semantics.
        """
        t = max(1, int(timeout))
        result = await self._redis.brpop(_QUEUE_KEY, timeout=t)
        if result is None:
            return None
        _, raw = result
        id_str = raw if isinstance(raw, str) else raw.decode()
        return uuid.UUID(id_str)


class BatchJobWorker:
    """In-process worker that drains the Redis batch job queue.

    Constructed with:
      sessionmaker        — async_sessionmaker for fresh per-operation sessions.
      queue                — RedisBatchJobQueue instance.
      settings             — frozen Settings (batch_job_timeout_seconds, batch_job_max_retries).
      get_batch_processor  — zero-arg callable returning the current BatchProcessor (or
                            None). A lambda reading app.state lets tests swap the processor
                            after construction.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        queue: RedisBatchJobQueue,
        settings: Settings,
        get_batch_processor: Callable[[], Any],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._queue = queue
        self._settings = settings
        self._get_batch_processor = get_batch_processor

    async def run_forever(self) -> None:
        """Long-running loop: claim -> drive; repeat. Exits only on CancelledError.

        A single-job exception (any Exception subclass) is caught, logged, and the loop
        continues — one bad job never kills the worker. CancelledError is re-raised
        immediately for clean shutdown.
        """
        while True:
            try:
                job_id = await self._queue.claim(timeout=_CLAIM_TIMEOUT_RUN_FOREVER)
                if job_id is None:
                    continue
                await self._drive(job_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(
                    "batch_worker: loop error (swallowed, continuing)",
                )
                continue

    async def process_once(self) -> bool:
        """TEST SEAM: claim + drive exactly one job id.

        Returns False when the queue is empty (claim timed out), True otherwise (job id
        was claimed and _drive was called — even if the job was stale).
        """
        job_id = await self._queue.claim(timeout=_CLAIM_TIMEOUT_PROCESS_ONCE)
        if job_id is None:
            return False
        await self._drive(job_id)
        return True

    async def _drive(self, job_id: uuid.UUID) -> None:
        """Drive a single job: load -> retry-guard -> process.

        Failure modes handled:
          - Job not in DB: log warning, skip (no crash, no re-enqueue).
          - Job already terminal: log info, skip (idempotent).
          - retry_count > max_retries: set_failed(max_retries_exceeded), return.
          - Otherwise: call _process_batch_job (the shared processing logic).
        """
        async with self._sessionmaker() as session:
            row = await session.get(BatchJobRow, job_id)

        if row is None:
            _log.warning(
                "batch_worker: job %s not found in DB, skipping (stale enqueue?)",
                job_id,
            )
            return

        if row.status in _JOB_TERMINAL_STATUSES:
            _log.info(
                "batch_worker: job %s already terminal (%s), skipping (idempotent)",
                job_id,
                row.status,
            )
            return

        async with self._sessionmaker() as session:
            repo = BatchJobRepository(session)
            new_count = await repo.increment_retry(job_id)
            await session.commit()

        # max_retries == 0 means UNLIMITED (the codebase convention for every other
        # 0-valued cap). Guarding on `> 0` avoids the footgun where a fresh job's first
        # drive (new_count == 1) would trip a zero cap and fail without being processed.
        max_retries = self._settings.batch_job_max_retries
        if max_retries > 0 and new_count > max_retries:
            _log.warning(
                "batch_worker: job %s exceeded max_retries (%d > %d), marking failed",
                job_id,
                new_count,
                max_retries,
            )
            async with self._sessionmaker() as session:
                repo = BatchJobRepository(session)
                await repo.set_failed(job_id=job_id, error="max_retries_exceeded")
                await session.commit()
            return

        # Import here to avoid a circular import at module level (router imports from
        # this worker module at startup; worker imports from router below).
        from gateway.batches.api.router import _process_batch_job  # pyright: ignore[reportPrivateUsage]  # noqa: I001

        batch_processor = self._get_batch_processor()
        _local_tasks: set[asyncio.Task[None]] = set()

        await _process_batch_job(
            job_id=job_id,
            sessionmaker=self._sessionmaker,
            batch_processor=batch_processor,
            timeout_seconds=self._settings.batch_job_timeout_seconds,
            tasks_set=_local_tasks,
        )


def should_start_batch_worker(settings: Settings) -> bool:
    """Predicate: start the BatchJobWorker only when the knob is on.

    Mirrors should_start_video_worker / should_start_drift_checker.
    """
    return settings.batch_durable_queue_enabled


async def recover_orphans(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: RedisBatchJobQueue,
) -> int:
    """Re-enqueue all non-terminal jobs found in the DB at startup.

    Called once in the lifespan BEFORE starting run_forever so any jobs that were
    in-flight when the gateway last restarted are picked up by the worker. Returns the
    number of job ids enqueued.

    Safe to call multiple times (idempotent at the queue level — LPUSH adds duplicates,
    but the worker's terminal-status skip guard ensures already-finished jobs are simply
    discarded when claimed).
    """
    async with sessionmaker() as session:
        repo = BatchJobRepository(session)
        ids = await repo.list_nonterminal_ids()

    enqueued = 0
    for job_id in ids:
        try:
            await queue.enqueue(job_id)
            enqueued += 1
        except Exception:
            _log.exception(
                "batch_worker: recover_orphans failed to enqueue job %s (Redis down?)",
                job_id,
            )
    return enqueued
