"""VectorStoreIngestWorker — durable Redis-backed background ingestion worker.

vector-store-files PLAN.md §3 (FROZEN @ v1). Structurally copied from
``gateway.batches.application.worker.BatchJobWorker`` /
``gateway.video.application.worker.VideoJobWorker`` (the repo's established
durable-queue shape — NOT invented machinery):
  - RedisVectorStoreIngestQueue wraps a redis.asyncio client, exposes
    enqueue/claim on the ``vector_store:ingest:pending`` list key (LPUSH/BRPOP).
  - VectorStoreIngestWorker.run_forever() is the long-running lifespan loop. A
    single-job exception never kills the loop.
  - VectorStoreIngestWorker.process_once() is the TEST SEAM: claim + drive
    exactly one row id, False on claim-timeout (queue empty), True otherwise.
  - recover_orphans() is called at startup (before run_forever) to re-enqueue any
    rows that were non-terminal when the gateway last restarted.

Per-row drive (M2/M5/M6/M7): load -> terminal-skip -> raise_if_zdr (FIRST line of
the chunk-write path) -> read file bytes -> chunk -> ONE batched embed via the
ChunkEmbedder port -> ONE atomic finalize tx (RE-CHECK raise_if_zdr + delete
stale chunks + bulk insert + CAS in_progress->completed) -> AWAITED usage
record, ONLY when the CAS won.

SECURITY (ZDR TOCTOU heal, 2026-07-24): the embed call is a network round-trip
(5s connect + 30s read in prod) — a window during which a tenant can flip
tenants.zdr_enabled=true via their OWN retention-policy admin endpoint. The
entry-line raise_if_zdr alone cannot catch a flip that lands DURING that
window, so raise_if_zdr is re-checked a SECOND time, INSIDE the same
session/transaction as finalize_completed, immediately before the chunk
write + status flip and before that transaction commits — a flip caught there
rolls back the whole transaction (zero chunks, zero status flip survive) and
the row is marked failed/zdr_blocked instead. See drive()'s
``zdr_flipped_during_ingest`` branch.

AT-LEAST-ONCE + IDEMPOTENCY guarantees:
  - A stale/missing row id is logged + skipped (no re-enqueue, no raise).
  - A terminal row (completed/failed) claimed from the queue is skipped
    immediately — a duplicate enqueue (re-attach healing, recover_orphans racing
    an active worker) is always safe.
  - finalize_completed's CAS ensures a race between two workers on the SAME row
    can never double-write chunks or double-bill (the CAS loser rolls back its
    whole transaction; see VectorStoreFileRepository.finalize_completed).

Billing (M6): the ledger write goes through the SAME shared rate-card resolver
every other billable op uses (RecordingUsageRecorder) so pricing_snapshot/markup
resolution stays in exactly one place. This worker then immediately flushes the
just-written Redis-Stream event into the Postgres ledger — unlike the hot
completion path (where a 1s-interval background flush is an acceptable trade for
per-request latency), a background ingestion job already carries several
DB round-trips, so trading one more bounded Redis+DB hop for READ-YOUR-WRITES
billing visibility (GET .../files, list, and this task's own red suite all query
usage_records directly, no flusher loop running under test) is the right call.
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
from gateway.core.errors import ProblemError
from gateway.files.infrastructure.repository import FileRepository
from gateway.objectstore.port import ObjectStore
from gateway.proxy.domain.errors import CircuitOpenError, UpstreamUnavailableError
from gateway.tenants.application.retention_policy import raise_if_zdr
from gateway.usage.application.flusher import UsageLedgerFlusher
from gateway.usage.application.recorder import RecordingUsageRecorder
from gateway.vector_stores.application.chunker import MAX_CHUNKS_PER_FILE, chunk_text
from gateway.vector_stores.infrastructure.file_repository import VectorStoreFileRepository
from gateway.vector_stores.infrastructure.orm import VectorStoreFileRow, VectorStoreRow

_log = logging.getLogger(__name__)

# Redis list key — single global queue shared by all worker instances.
_QUEUE_KEY = "vector_store:ingest:pending"

_TERMINAL_STATUSES = ("completed", "failed")

# BRPOP claim timeout (seconds). Must be > 0 (0 = block forever, unsafe for
# graceful shutdown). 2 s gives a responsive CancelledError on shutdown.
_CLAIM_TIMEOUT_RUN_FOREVER = 2
_CLAIM_TIMEOUT_PROCESS_ONCE = 1

# LPUSH has no inherent timeout (unlike BRPOP's blocking-timeout parameter) — a
# hung connection would otherwise block the POST attach request indefinitely.
# Bounded so a hang degrades to the router's fail-open inline fallback instead
# (batches precedent).
_ENQUEUE_TIMEOUT_SECONDS = 2.0

_DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class RedisVectorStoreIngestQueue:
    """Thin wrapper around a redis.asyncio client for the ingest job queue.

    enqueue: LPUSH row_id string to the left of vector_store:ingest:pending.
    claim:   BRPOP from the right (FIFO); returns None on timeout.
    """

    def __init__(self, redis: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self.redis = redis

    async def enqueue(self, row_id: uuid.UUID) -> None:
        """Push row_id onto the left of the pending queue.

        Bounded by _ENQUEUE_TIMEOUT_SECONDS; raises (TimeoutError included) on
        failure — the caller (router) is responsible for the fail-open fallback.
        """
        await asyncio.wait_for(
            self.redis.lpush(_QUEUE_KEY, str(row_id)),
            timeout=_ENQUEUE_TIMEOUT_SECONDS,
        )

    async def claim(self, timeout: float) -> uuid.UUID | None:  # noqa: ASYNC109
        """Block-pop from the right of the pending queue.

        Returns None when the timeout expires without a job being available.
        timeout must be > 0; 0 blocks indefinitely (unsafe for shutdown).
        """
        t = max(1, int(timeout))
        result = await self.redis.brpop(_QUEUE_KEY, timeout=t)
        if result is None:
            return None
        _, raw = result
        id_str = raw if isinstance(raw, str) else raw.decode()
        return uuid.UUID(id_str)


class VectorStoreIngestWorker:
    """In-process worker that drains the Redis vector-store ingest queue.

    Constructed with:
      sessionmaker   — async_sessionmaker for fresh per-operation sessions.
      queue          — RedisVectorStoreIngestQueue instance.
      settings       — frozen Settings.
      get_embedder   — zero-arg callable returning the current ChunkEmbedder (or
                       None). A lambda reading app.state lets tests swap the
                       embedder after construction (the get_batch_processor idiom).
      object_store   — optional ObjectStore for s3-backed file bytes (None
                       honest-degrades an s3-backed file to a read failure —
                       inline BYTEA, the test path, is unaffected).
    """

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        queue: RedisVectorStoreIngestQueue,
        settings: Settings,
        get_embedder: Callable[[], Any],
        object_store: ObjectStore | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._queue = queue
        self._settings = settings
        self._get_embedder = get_embedder
        self._object_store = object_store
        # Billing (M6): the SAME shared rate-card resolver every billable op uses.
        self._usage_recorder = RecordingUsageRecorder(redis=queue.redis, session_factory=sessionmaker)
        self._flusher = UsageLedgerFlusher(redis=queue.redis, session_factory=sessionmaker)

    async def run_forever(self) -> None:
        """Long-running loop: claim -> drive; repeat. Exits only on CancelledError."""
        while True:
            try:
                row_id = await self._queue.claim(timeout=_CLAIM_TIMEOUT_RUN_FOREVER)
                if row_id is None:
                    continue
                await self.drive(row_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(
                    "vector_store_ingest_worker: loop error (swallowed, continuing)"
                )
                continue

    async def process_once(self) -> bool:
        """TEST SEAM: claim + drive exactly one row id.

        Returns False when the queue is empty (claim timed out), True otherwise.
        """
        row_id = await self._queue.claim(timeout=_CLAIM_TIMEOUT_PROCESS_ONCE)
        if row_id is None:
            return False
        await self.drive(row_id)
        return True

    async def drive(self, row_id: uuid.UUID) -> None:
        """Drive a single vector_store_files row: load -> ingest -> finalize."""
        async with self._sessionmaker() as session:
            row = await session.get(VectorStoreFileRow, row_id)

        if row is None:
            _log.warning(
                "vector_store_ingest_worker: row %s not found in DB, skipping (stale enqueue?)",
                row_id,
            )
            return

        if row.status in _TERMINAL_STATUSES:
            _log.info(
                "vector_store_ingest_worker: row %s already terminal (%s), skipping (idempotent)",
                row_id,
                row.status,
            )
            return

        tenant_id = row.tenant_id
        vector_store_id = row.vector_store_id
        file_id = row.file_id

        # M5: raise_if_zdr is the FIRST line of the chunk-write path — a tenant who
        # flips ZDR on AFTER attach must fail closed, never silently store payload.
        async with self._sessionmaker() as session:
            try:
                await raise_if_zdr(session, tenant_id)
            except ProblemError:
                async with self._sessionmaker() as fail_session:
                    repo = VectorStoreFileRepository(fail_session)
                    await repo.set_failed(
                        row_id,
                        code="zdr_blocked",
                        message="Zero-Data-Retention tenant: ingestion blocked",
                    )
                    await fail_session.commit()
                return

        # Read the source file's bytes (inline BYTEA or s3-backed).
        async with self._sessionmaker() as session:
            file_row = await FileRepository(session, object_store=self._object_store).get_active(
                tenant_id=tenant_id, file_id=file_id
            )

        if file_row is None:
            await self._fail(row_id, code="file_empty", message="Source file is no longer available")
            return

        content = await self._read_bytes(file_row)
        text = content.decode("utf-8", errors="replace") if content else ""
        chunks = chunk_text(text)

        if not chunks:
            await self._fail(row_id, code="file_empty", message="File content is empty")
            return
        if len(chunks) > MAX_CHUNKS_PER_FILE:
            await self._fail(
                row_id, code="file_too_large", message="File exceeds the ingestion chunk cap"
            )
            return

        async with self._sessionmaker() as session:
            store_row = await session.get(VectorStoreRow, vector_store_id)
        embedding_model = store_row.embedding_model if store_row is not None else _DEFAULT_EMBEDDING_MODEL
        key_id = store_row.key_id if store_row is not None else tenant_id

        embedder = self._get_embedder()
        if embedder is None:
            await self._fail(
                row_id, code="embedding_unavailable", message="No embeddings provider configured"
            )
            return

        try:
            vectors, usage = await embedder.embed(tenant_id, embedding_model, chunks)
        except (UpstreamUnavailableError, CircuitOpenError):
            await self._fail(
                row_id, code="embedding_unavailable", message="Embeddings upstream unavailable"
            )
            return

        total_bytes = len(content)

        # SECURITY HARD-STOP HEAL (ZDR TOCTOU, refute-reproduced): the entry-line
        # raise_if_zdr above only protects against a tenant that was ALREADY ZDR at
        # drive() start — the embed call's network round-trip (5s connect + 30s
        # read in prod) is a window during which the tenant can flip
        # tenants.zdr_enabled=true via their OWN retention-policy admin endpoint,
        # and the finalize commit would otherwise persist payload chunks for a now
        # -ZDR tenant. Re-run raise_if_zdr INSIDE the SAME transaction/session as
        # finalize_completed, immediately BEFORE the chunk delete/insert + CAS
        # status flip, and BEFORE that transaction commits — read-committed
        # isolation means this SELECT sees any ZDR flip already committed by
        # another session. A flip caught here rolls back the WHOLE transaction
        # (no chunks, no status flip survive) and the row is marked
        # failed/zdr_blocked in a fresh transaction instead — fail-closed, and
        # byte-identical (zero extra latency of consequence) for the non-ZDR
        # majority path.
        zdr_flipped_during_ingest = False
        async with self._sessionmaker() as session:
            try:
                await raise_if_zdr(session, tenant_id)
            except ProblemError:
                zdr_flipped_during_ingest = True
                await session.rollback()
                won = False
            else:
                repo = VectorStoreFileRepository(session)
                won = await repo.finalize_completed(
                    row_id=row_id,
                    vector_store_id=vector_store_id,
                    tenant_id=tenant_id,
                    chunks=chunks,
                    vectors=vectors,
                    usage_bytes=total_bytes,
                )
                if won:
                    await session.commit()
                else:
                    await session.rollback()

        if zdr_flipped_during_ingest:
            await self._fail(
                row_id,
                code="zdr_blocked",
                message="Zero-Data-Retention tenant: ingestion blocked (flipped during ingestion)",
            )
            return

        if not won:
            # CAS miss — a racer already finalized this row; never double-bill,
            # never duplicate chunks (M2/M6 claim-safety invariant).
            return

        await self._record_usage(
            tenant_id=tenant_id, key_id=key_id, model=embedding_model, usage=usage
        )

    async def _fail(self, row_id: uuid.UUID, *, code: str, message: str) -> None:
        async with self._sessionmaker() as session:
            repo = VectorStoreFileRepository(session)
            await repo.set_failed(row_id, code=code, message=message)
            await session.commit()

    async def _read_bytes(self, file_row: Any) -> bytes:
        if file_row.storage_backend == "s3":
            if self._object_store is None or not file_row.object_key:
                _log.warning(
                    "vector_store_ingest_worker: s3-backed file with no ObjectStore configured"
                    " (file_id=%s) — treated as empty",
                    file_row.id,
                )
                return b""
            try:
                return await self._object_store.get(file_row.object_key)
            except Exception:
                _log.warning(
                    "vector_store_ingest_worker: ObjectStore.get failed for file_id=%s",
                    file_row.id,
                )
                return b""
        return file_row.content or b""

    async def _record_usage(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, object] | None,
    ) -> None:
        """Bill exactly once, then flush immediately so it is durably visible
        (M6) — never raises; a metering failure must never undo a completed
        ingest."""
        try:
            await self._usage_recorder.record(
                tenant_id=tenant_id,
                key_id=key_id,
                model=model,
                usage=usage,
                status=200,
            )
            await self._flusher.flush_once()
        except Exception:
            _log.exception(
                "vector_store_ingest_worker: usage recording failed (swallowed, ingest stands)"
            )


def should_start_vector_store_ingest_worker(settings: Settings) -> bool:
    """Predicate: start the VectorStoreIngestWorker (default-ON, operator escape hatch)."""
    return settings.vector_store_ingest_worker_enabled


async def recover_orphans(
    sessionmaker: async_sessionmaker[AsyncSession],
    queue: RedisVectorStoreIngestQueue,
) -> int:
    """Re-enqueue all in_progress rows found in the DB at startup.

    Safe to call multiple times (idempotent at the queue level — a duplicate
    enqueue is a no-op courtesy of the worker's terminal-status skip guard +
    finalize_completed's CAS).
    """
    async with sessionmaker() as session:
        repo = VectorStoreFileRepository(session)
        ids = await repo.list_nonterminal_ids()

    enqueued = 0
    for row_id in ids:
        try:
            await queue.enqueue(row_id)
            enqueued += 1
        except Exception:
            _log.exception(
                "vector_store_ingest_worker: recover_orphans failed to enqueue row %s (Redis down?)",
                row_id,
            )
    return enqueued


__all__ = [
    "RedisVectorStoreIngestQueue",
    "VectorStoreIngestWorker",
    "recover_orphans",
    "should_start_vector_store_ingest_worker",
]
