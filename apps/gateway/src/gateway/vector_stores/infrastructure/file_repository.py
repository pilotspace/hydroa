"""SQLAlchemy repository for vector_store_files + vector_store_chunks (wave-2).

vector-store-files PLAN.md §3 (FROZEN @ v1). ZERO DDL — reuses the FROZEN
vector-store-core tables verbatim; the vsf row IS the ingestion job row.

INVARIANT: every method that reads/writes a row is tenant-scoped (or store-scoped,
which is itself tenant-validated by the caller) — a cross-tenant id is never
distinguishable from an absent one (router maps to 404, no oracle).

Methods:
  get_or_create(*, tenant_id, vector_store_id, file_id) -> VectorStoreFileRow
      Idempotent attach: INSERT .. ON CONFLICT (vector_store_id, file_id) DO
      NOTHING RETURNING + re-select. No TOCTOU (single statement + fallback SELECT
      inside the same transaction).
  retry_if_failed(row_id) -> bool
      CAS status failed->in_progress (clears last_error) — the M7 retry path.
  set_failed(row_id, *, code, message) -> bool
      CAS status in_progress->failed with a structured last_error. Status-guarded
      so a duplicate drive of an already-terminal row is a safe no-op.
  finalize_completed(...) -> bool
      ONE atomic finalize: delete stale chunks for this vsf id, bulk-insert the new
      chunk rows, CAS status in_progress->completed, and bump the store's
      usage_bytes. Returns False (caller MUST rollback) on a CAS miss — a racer
      already finalized; chunks are never double-written.
  list_nonterminal_ids() -> list[UUID]
      Every non-terminal (in_progress) row id — recover_orphans' startup re-enqueue.
  get_by_file(*, tenant_id, vector_store_id, file_id) -> VectorStoreFileRow | None
  list_for_store(*, tenant_id, vector_store_id, limit, offset) -> list[VectorStoreFileRow]
  file_counts(vector_store_id) -> dict[str, int]
      Live COUNT..GROUP BY status (M8) — {in_progress, completed, failed,
      cancelled, total}.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.vector_stores.infrastructure.orm import (
    VectorStoreChunkRow,
    VectorStoreFileRow,
    VectorStoreRow,
)

_log = logging.getLogger(__name__)

_TERMINAL_STATUSES = ("completed", "failed")

# --- todo #62: the HNSW post-filtering bound -------------------------------------
# The HNSW index covers ONLY `embedding`, so a (tenant_id, vector_store_id)-filtered
# top-k can be served two ways, and the difference is CORRECTNESS, not just speed:
#   * exact  — filter via ix_vector_store_chunks_store, then sort the matches. Full recall.
#   * HNSW   — traverse the vector index for the globally-nearest candidates, THEN apply
#              the filter. Any of this store's chunks ranking below the candidate window
#              is dropped: fewer than top_k hits, a 200, and a per_query bill.
# Measured (pgvector 0.8.5 / PG16): the planner picks EXACT for a selective store (up to
# ~29% of the table in a 13.6k-row probe) and HNSW once the store dominates (~59%) — and
# in that regime the store's own rows fill the window anyway. So the harmful combination
# is not one the cost model produces on its own; it needs the protective btree index to
# be gone, or statistics to have drifted after a bulk ingest.
#
# Iterative scan is the insurance for exactly that: it keeps traversing until top_k rows
# have PASSED the filter (bounded by hnsw.max_scan_tuples, default 20000). strict_order
# keeps the exact distance ordering. Measured on the forced-HNSW plan: 1 of 5 chunks
# without it, 5 of 5 with it.
#
# NOT ef_search: raising it to 1000 changed nothing (still 2 of 5 in a 4000-row probe).
# A bigger window is still a window; it cannot reach rows that rank below it globally.
# That was the remedy the todo proposed, and it would have cost latency for no recall.
_ITERATIVE_SCAN_SQL = "SET LOCAL hnsw.iterative_scan = strict_order"


class _IterativeScan:
    """Process-wide capability cache for the pgvector >= 0.8 GUC above.

    An older pgvector rejects the SET with "unrecognized configuration parameter",
    which POISONS the transaction — so the first attempt per process runs inside a
    SAVEPOINT and a failure degrades retrieval to the documented post-filter bound
    instead of 500-ing every file_search on that deployment.
    """

    supported: bool | None = None


def is_topk_shortfall(*, returned: int, total: int, top_k: int) -> bool:
    """True iff a retrieval came back short WHILE the store had more to give (todo #62).

    Both halves are required, and that is the whole point: a store holding fewer than
    top_k chunks returns everything it has, every time, which is not a truncation. An
    alert that fired on those would be muted within a week and the real event would be
    invisible with it.
    """
    return returned < top_k and total > returned


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """One chunk returned by top-k retrieval (file-search-tool PLAN.md §3).

    Deliberately a thin projection — the grounding seam only needs the chunk text;
    it never mutates a live ORM row nor holds the (heavy) embedding vector in memory.
    """

    content: str
    chunk_index: int


class VectorStoreFileRepository:
    """All vector_store_files + vector_store_chunks persistence for wave-2."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(
        self,
        *,
        tenant_id: uuid.UUID,
        vector_store_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> VectorStoreFileRow:
        """Idempotent attach — UNIQUE(vector_store_id, file_id) is the dedup key.

        A fresh row lands ``status="in_progress"`` (the column's server default).
        A conflict (re-attach) returns the EXISTING row unchanged — the caller
        (router) applies the M7 retry-from-failed / re-enqueue-from-in_progress
        semantics on top of whatever status comes back. One statement (INSERT ..
        ON CONFLICT DO NOTHING) plus a follow-up SELECT inside the SAME
        transaction — no TOCTOU window a concurrent attacher could race.
        """
        stmt = (
            pg_insert(VectorStoreFileRow)
            .values(
                id=uuid.uuid4(),
                vector_store_id=vector_store_id,
                tenant_id=tenant_id,
                file_id=file_id,
            )
            .on_conflict_do_nothing(constraint="uq_vector_store_files_store_file")
        )
        await self._session.execute(stmt)
        await self._session.flush()

        result = await self._session.execute(
            select(VectorStoreFileRow).where(
                VectorStoreFileRow.vector_store_id == vector_store_id,
                VectorStoreFileRow.file_id == file_id,
            )
        )
        return result.scalar_one()

    async def retry_if_failed(self, row_id: uuid.UUID) -> bool:
        """CAS status failed->in_progress, clearing last_error (M7 retry)."""
        result = await self._session.execute(
            update(VectorStoreFileRow)
            .where(VectorStoreFileRow.id == row_id, VectorStoreFileRow.status == "failed")
            .values(status="in_progress", last_error=None)
            .returning(VectorStoreFileRow.id)
        )
        await self._session.flush()
        return result.first() is not None

    async def set_failed(self, row_id: uuid.UUID, *, code: str, message: str) -> bool:
        """CAS status in_progress->failed with a structured last_error.

        Status-guarded: a duplicate drive of an already-terminal row is a
        no-op (returns False), never corrupting a prior completed/failed result.
        """
        result = await self._session.execute(
            update(VectorStoreFileRow)
            .where(VectorStoreFileRow.id == row_id, VectorStoreFileRow.status == "in_progress")
            .values(status="failed", last_error={"code": code, "message": message})
            .returning(VectorStoreFileRow.id)
        )
        await self._session.flush()
        return result.first() is not None

    async def finalize_completed(
        self,
        *,
        row_id: uuid.UUID,
        vector_store_id: uuid.UUID,
        tenant_id: uuid.UUID,
        chunks: Sequence[str],
        vectors: Sequence[list[float]],
        usage_bytes: int,
    ) -> bool:
        """ONE atomic finalize: replace chunks + CAS-flip to completed + bump usage.

        Returns False on a CAS miss (a racer already finalized this row) — the
        CALLER must roll back the whole transaction in that case (chunk delete +
        insert must never survive without the CAS also winning: never a
        half-indexed "completed", never duplicate chunks).
        """
        # Idempotent replace: a re-run of a stranded/re-enqueued row must never
        # accumulate duplicate chunks alongside a prior partial attempt.
        await self._session.execute(
            sa_delete(VectorStoreChunkRow).where(VectorStoreChunkRow.vector_store_file_id == row_id)
        )
        rows = [
            VectorStoreChunkRow(
                vector_store_file_id=row_id,
                vector_store_id=vector_store_id,
                tenant_id=tenant_id,
                chunk_index=index,
                content=chunk,
                embedding=vector,
            )
            for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
        ]
        self._session.add_all(rows)

        result = await self._session.execute(
            update(VectorStoreFileRow)
            .where(VectorStoreFileRow.id == row_id, VectorStoreFileRow.status == "in_progress")
            .values(status="completed", usage_bytes=usage_bytes, last_error=None)
            .returning(VectorStoreFileRow.id)
        )
        if result.first() is None:
            return False

        await self._session.execute(
            update(VectorStoreRow)
            .where(VectorStoreRow.id == vector_store_id)
            .values(usage_bytes=VectorStoreRow.usage_bytes + usage_bytes)
        )
        await self._session.flush()
        return True

    async def list_nonterminal_ids(self) -> list[uuid.UUID]:
        """Every in_progress row id — recover_orphans' startup re-enqueue set."""
        result = await self._session.execute(
            select(VectorStoreFileRow.id).where(VectorStoreFileRow.status == "in_progress")
        )
        return [row[0] for row in result.all()]

    async def get_by_file(
        self,
        *,
        tenant_id: uuid.UUID,
        vector_store_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> VectorStoreFileRow | None:
        """Load one attach row, scoped to (tenant, store, file). None for absent."""
        result = await self._session.execute(
            select(VectorStoreFileRow).where(
                VectorStoreFileRow.tenant_id == tenant_id,
                VectorStoreFileRow.vector_store_id == vector_store_id,
                VectorStoreFileRow.file_id == file_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_for_store(
        self,
        *,
        tenant_id: uuid.UUID,
        vector_store_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[VectorStoreFileRow]:
        """Attach rows for one store, newest first, tenant-scoped."""
        result = await self._session.execute(
            select(VectorStoreFileRow)
            .where(
                VectorStoreFileRow.tenant_id == tenant_id,
                VectorStoreFileRow.vector_store_id == vector_store_id,
            )
            .order_by(VectorStoreFileRow.created_at.desc(), VectorStoreFileRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def file_counts(self, vector_store_id: uuid.UUID) -> dict[str, int]:
        """Live {in_progress, completed, failed, cancelled, total} counts (M8)."""
        result = await self._session.execute(
            select(VectorStoreFileRow.status, func.count())
            .where(VectorStoreFileRow.vector_store_id == vector_store_id)
            .group_by(VectorStoreFileRow.status)
        )
        counts = {"in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": 0}
        for status, n in result.all():
            if status in counts:
                counts[status] = int(n)
            counts["total"] += int(n)
        return counts

    async def file_counts_for(
        self, vector_store_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, int]]:
        """Live file counts for MANY stores in ONE round-trip (todo #63).

        The list endpoint used to call ``file_counts`` once per row — up to 100 sequential
        GROUP BYs for a single request, getting linearly slower as a tenant added stores.

        Returns an entry for EVERY id asked for, including stores with no files at all: a
        file-less store produces no group row, and the caller must still render its
        all-zero counts rather than a missing key. That asymmetry — the rows you get back
        are a subset of the rows you asked about — is the bug a naive GROUP BY rewrite
        ships, so the zero-fill happens here rather than being left to each caller.
        """
        if not vector_store_ids:
            return {}

        def _zero() -> dict[str, int]:
            return {"in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": 0}

        counts: dict[uuid.UUID, dict[str, int]] = {sid: _zero() for sid in vector_store_ids}
        result = await self._session.execute(
            select(
                VectorStoreFileRow.vector_store_id,
                VectorStoreFileRow.status,
                func.count(),
            )
            .where(VectorStoreFileRow.vector_store_id.in_(list(vector_store_ids)))
            .group_by(VectorStoreFileRow.vector_store_id, VectorStoreFileRow.status)
        )
        for store_id, status, n in result.all():
            bucket = counts.get(store_id)
            if bucket is None:  # pragma: no cover — defensive; IN () cannot widen the set
                continue
            if status in bucket:
                bucket[status] = int(n)
            bucket["total"] += int(n)
        return counts

    async def search_chunks(
        self,
        *,
        tenant_id: uuid.UUID,
        store_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Top-k cosine-nearest chunks for one store, tenant + store scoped (M1).

        ZERO DDL — rides the FROZEN wave-1/2 ``vector_store_chunks`` schema + its HNSW
        cosine index (``vector_cosine_ops``). Nearest-first (``embedding <=> :q`` ASC),
        bounded by ``top_k``. The WHERE clause filters BOTH ``tenant_id`` AND
        ``vector_store_id`` (both denormalized onto the chunk row) so a store owned by
        another tenant — or an unknown store — returns ``[]`` with ZERO leak; the caller
        maps ``[]``/absent-store to a uniform 404 (never an enumeration oracle).

        Asks for iterative index scan first, and logs a shortfall whose signature cannot
        be a legitimately small store — see the todo #62 block at the top of this module
        for the measurements behind both.
        """
        await self._request_iterative_scan()
        result = await self._session.execute(
            select(VectorStoreChunkRow.content, VectorStoreChunkRow.chunk_index)
            .where(
                VectorStoreChunkRow.tenant_id == tenant_id,
                VectorStoreChunkRow.vector_store_id == store_id,
            )
            .order_by(VectorStoreChunkRow.embedding.cosine_distance(query_embedding))
            .limit(top_k)
        )
        chunks = [RetrievedChunk(content=row[0], chunk_index=row[1]) for row in result.all()]
        if len(chunks) < top_k:
            await self._warn_if_truncated(
                tenant_id=tenant_id, store_id=store_id, returned=len(chunks), top_k=top_k
            )
        return chunks

    async def _request_iterative_scan(self) -> None:
        """Enable pgvector's iterative index scan for this transaction, or degrade."""
        if _IterativeScan.supported is False:
            return
        if _IterativeScan.supported is True:
            await self._session.execute(text(_ITERATIVE_SCAN_SQL))
            return
        try:
            # SAVEPOINT: an unrecognized GUC aborts the transaction, and every retrieval
            # after it in this session would fail too. Probed once per process.
            async with self._session.begin_nested():
                await self._session.execute(text(_ITERATIVE_SCAN_SQL))
        except Exception:
            _IterativeScan.supported = False
            _log.error(
                "file_search_iterative_scan_unavailable: pgvector < 0.8 on this database;"
                " top-k retrieval falls back to the HNSW post-filter bound (a store's"
                " lower-ranked chunks can be dropped when the planner takes the vector"
                " index). Upgrade pgvector to restore full recall."
            )
            return
        _IterativeScan.supported = True

    async def _warn_if_truncated(
        self, *, tenant_id: uuid.UUID, store_id: uuid.UUID, returned: int, top_k: int
    ) -> None:
        """Alert on the published bound (todo #62): short result, store had more.

        The extra COUNT runs ONLY on an under-full result and is served by
        ix_vector_store_chunks_store, so it costs a sub-millisecond index scan on a path
        that has already made an embedding round-trip. Silence here is what made this
        defect class unobservable: a truncated search and an empty store look identical
        from the outside.
        """
        total = int(
            (
                await self._session.execute(
                    select(func.count())
                    .select_from(VectorStoreChunkRow)
                    .where(
                        VectorStoreChunkRow.tenant_id == tenant_id,
                        VectorStoreChunkRow.vector_store_id == store_id,
                    )
                )
            ).scalar_one()
        )
        if not is_topk_shortfall(returned=returned, total=total, top_k=top_k):
            return
        _log.warning(
            "file_search_topk_shortfall: retrieval returned %d of top_k=%d for store %s"
            " which holds %d chunks — HNSW post-filtering or the iterative-scan tuple"
            " bound (hnsw.max_scan_tuples) dropped reachable chunks",
            returned,
            top_k,
            store_id,
            total,
            extra={
                "tenant_id": str(tenant_id),
                "vector_store_id": str(store_id),
                "returned": returned,
                "top_k": top_k,
                "chunks_in_store": total,
            },
        )


__all__ = ["_TERMINAL_STATUSES", "VectorStoreFileRepository", "is_topk_shortfall"]
