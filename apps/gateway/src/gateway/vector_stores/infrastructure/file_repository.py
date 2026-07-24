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

import uuid
from collections.abc import Sequence

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.vector_stores.infrastructure.orm import (
    VectorStoreChunkRow,
    VectorStoreFileRow,
    VectorStoreRow,
)

_TERMINAL_STATUSES = ("completed", "failed")


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
            sa_delete(VectorStoreChunkRow).where(
                VectorStoreChunkRow.vector_store_file_id == row_id
            )
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
            .order_by(VectorStoreFileRow.created_at.desc())
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


__all__ = ["VectorStoreFileRepository", "_TERMINAL_STATUSES"]
