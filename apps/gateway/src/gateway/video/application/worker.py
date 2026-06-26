"""VideoJobWorker — durable Redis-backed in-process worker for video generation jobs.

Design:
  - RedisVideoJobQueue wraps a redis.asyncio client, exposes enqueue/claim on
    the ``video:jobs:pending`` list key (LPUSH / BRPOP).
  - VideoJobWorker.run_forever() is the long-running lifespan task (mirrors
    ReconciliationDriftChecker.run_forever). It claims one job at a time and
    drives it via _drive(). A single-job exception never kills the loop.
  - VideoJobWorker.process_once() is the TEST SEAM: claim + drive exactly one
    id, return False on claim-timeout (queue empty), True otherwise.
  - recover_orphans() is called at startup (before run_forever) to re-enqueue
    any rows that were non-terminal when the gateway last restarted.
  - should_start_video_worker() mirrors should_start_drift_checker().

AT-LEAST-ONCE + IDEMPOTENCY guarantees:
  - increment_retry / set_running / set_succeeded / set_failed all carry the
    existing status-guard (allowed_from), so a re-driven or duplicated worker
    never corrupts a terminal result.
  - A stale/missing job id is logged + skipped (no re-enqueue, no raise).
  - A terminal job claimed from the queue is skipped immediately (idempotent).

Default-OFF: with video_durable_queue_enabled=False the router uses the v48
inline asyncio.create_task path unchanged. This module is imported but the
worker is never started.

Fail-open: if enqueue raises (Redis down / network partition), the router
falls back to the inline asyncio.create_task path so no job is ever dropped.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.config import Settings
from gateway.video.infrastructure.orm import VideoGenerationJobRow
from gateway.video.infrastructure.repository import VideoJobRepository

_log = logging.getLogger(__name__)

# Redis list key — single global queue shared by all worker instances.
_QUEUE_KEY = "video:jobs:pending"

# BRPOP claim timeout (seconds). Must be > 0 (0 = block forever, unsafe for
# graceful shutdown). 2 s gives a responsive CancelledError on shutdown.
_CLAIM_TIMEOUT_RUN_FOREVER = 2
_CLAIM_TIMEOUT_PROCESS_ONCE = 1


class RedisVideoJobQueue:
    """Thin wrapper around a redis.asyncio client for the video job queue.

    enqueue: LPUSH job_id string to the left of video:jobs:pending.
    claim:   BRPOP from the right (FIFO); returns None on timeout.
    """

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def enqueue(self, job_id: uuid.UUID) -> None:
        """Push job_id onto the left of the pending queue."""
        await self._redis.lpush(_QUEUE_KEY, str(job_id))

    async def claim(self, timeout: float) -> uuid.UUID | None:  # noqa: ASYNC109
        """Block-pop from the right of the pending queue.

        Returns None when the timeout expires without a job being available.
        timeout must be > 0; 0 blocks indefinitely (unsafe for shutdown).
        Note: ASYNC109 suppressed — this timeout is a Redis BRPOP parameter,
        not an asyncio.timeout wrapper; renaming it would obscure its semantics.
        """
        # redis.asyncio brpop accepts timeout as int|float; round up to at least 1.
        t = max(1, int(timeout))
        result = await self._redis.brpop(_QUEUE_KEY, timeout=t)
        if result is None:
            return None
        _, raw = result
        id_str = raw if isinstance(raw, str) else raw.decode()
        return uuid.UUID(id_str)


class VideoJobWorker:
    """In-process worker that drains the Redis video job queue.

    Constructed with:
      sessionmaker       — async_sessionmaker for fresh per-operation sessions.
      queue              — RedisVideoJobQueue instance.
      settings           — frozen Settings (video_job_timeout_seconds, video_job_max_retries).
      get_video_generator — zero-arg callable returning the current VideoGenerator
                           (or None). A lambda reading app.state lets tests swap the
                           generator after construction.
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        queue: RedisVideoJobQueue,
        settings: Settings,
        get_video_generator: Callable[[], Any],
    ) -> None:
        self._sessionmaker = sessionmaker
        self._queue = queue
        self._settings = settings
        self._get_video_generator = get_video_generator

    async def run_forever(self) -> None:
        """Long-running loop: claim → drive; repeat. Exits only on CancelledError.

        A single-job exception (any Exception subclass) is caught, logged, and
        the loop continues — one bad job never kills the worker. CancelledError
        is re-raised immediately for clean shutdown.
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
                    "video_worker: loop error (swallowed, continuing)",
                )
                continue

    async def process_once(self) -> bool:
        """TEST SEAM: claim + drive exactly one job id.

        Returns False when the queue is empty (claim timed out), True otherwise
        (job id was claimed and _drive was called — even if the job was stale).
        """
        job_id = await self._queue.claim(timeout=_CLAIM_TIMEOUT_PROCESS_ONCE)
        if job_id is None:
            return False
        await self._drive(job_id)
        return True

    async def _drive(self, job_id: uuid.UUID) -> None:
        """Drive a single job: load → retry-guard → process.

        Failure modes handled:
          - Job not in DB: log warning, skip (no crash, no re-enqueue).
          - Job already terminal: log info, skip (idempotent).
          - retry_count > max_retries: set_failed(max_retries_exceeded), return.
          - Otherwise: call _process_video_job (the v48 logic verbatim).
        """
        # ── Load the job row ───────────────────────────────────────────────
        async with self._sessionmaker() as session:
            row = await session.get(VideoGenerationJobRow, job_id)

        if row is None:
            _log.warning(
                "video_worker: job %s not found in DB, skipping (stale enqueue?)",
                job_id,
            )
            return

        if row.status in ("succeeded", "failed"):
            _log.info(
                "video_worker: job %s already terminal (%s), skipping (idempotent)",
                job_id,
                row.status,
            )
            return

        # ── Increment retry_count (guarded to non-terminal rows) ──────────
        async with self._sessionmaker() as session:
            repo = VideoJobRepository(session)
            new_count = await repo.increment_retry(job_id)
            await session.commit()

        # ── Poison guard ───────────────────────────────────────────────────
        # max_retries == 0 means UNLIMITED (the codebase convention for every
        # other 0-valued cap: timeouts, byte sizes). Guarding on `> 0` avoids the
        # footgun where a fresh job's first drive (new_count == 1) would trip a
        # zero cap and fail WITHOUT ever being processed.
        max_retries = self._settings.video_job_max_retries
        if max_retries > 0 and new_count > max_retries:
            _log.warning(
                "video_worker: job %s exceeded max_retries (%d > %d), marking failed",
                job_id,
                new_count,
                self._settings.video_job_max_retries,
            )
            async with self._sessionmaker() as session:
                repo = VideoJobRepository(session)
                await repo.set_failed(job_id=job_id, error="max_retries_exceeded")
                await session.commit()
            return

        # ── Process via v48 logic (reuse verbatim) ─────────────────────────
        # Import here to avoid a circular import at module level (router imports
        # from this worker module at startup; worker imports from router below).
        from gateway.video.api.router import _process_video_job  # pyright: ignore[reportPrivateUsage]  # noqa: I001

        video_generator = self._get_video_generator()
        # tasks_set is required by _process_video_job's signature (it discards
        # the current_task from the set on completion). The worker drives the
        # job inline (awaited), so there is no asyncio.Task to track — pass a
        # throwaway local set.
        _local_tasks: set[asyncio.Task[None]] = set()

        await _process_video_job(
            job_id=job_id,
            tenant_id=row.tenant_id,
            key_id=row.key_id,
            model=row.model,
            prompt=row.prompt,
            params=row.params,
            sessionmaker=self._sessionmaker,
            video_generator=video_generator,
            timeout_seconds=self._settings.video_job_timeout_seconds,
            tasks_set=_local_tasks,
        )


def should_start_video_worker(settings: Settings) -> bool:
    """Predicate: start the VideoJobWorker only when the knob is on.

    Mirrors should_start_drift_checker / should_start_recovery_sweep.
    """
    return settings.video_durable_queue_enabled


async def recover_orphans(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: RedisVideoJobQueue,
) -> int:
    """Re-enqueue all non-terminal jobs found in the DB at startup.

    Called once in the lifespan BEFORE starting run_forever so any jobs that
    were in-flight when the gateway last restarted are picked up by the worker.
    Returns the number of job ids enqueued.

    This is safe to call multiple times (idempotent at the queue level — LPUSH
    adds duplicates, but the worker's terminal-status skip guard ensures
    already-finished jobs are simply discarded when claimed).
    """
    async with sessionmaker() as session:
        repo = VideoJobRepository(session)
        ids = await repo.list_nonterminal_ids()

    enqueued = 0
    for job_id in ids:
        try:
            await queue.enqueue(job_id)
            enqueued += 1
        except Exception:
            _log.exception(
                "video_worker: recover_orphans failed to enqueue job %s (Redis down?)",
                job_id,
            )
    return enqueued
