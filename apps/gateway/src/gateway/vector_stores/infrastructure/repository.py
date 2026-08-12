"""SQLAlchemy repository for the vector-stores domain (vector-store-core PLAN.md §3).

INVARIANT: every method takes ``tenant_id`` and every query filters on it. A
cross-tenant or unknown id always returns None/False — NEVER 403/200. The router
maps that to 404 (ERR_VECTOR_STORE_NOT_FOUND) with zero data leak.

Mirrors ``gateway.files.infrastructure.repository.FileRepository`` in shape.
The container is metadata-only (M8 ZDR compose) so ``create`` does NOT call
``raise_if_zdr`` — that gate is the wave-2 chunk-write choke point, not here.

Methods:
  create(*, id, tenant_id, key_id, name, metadata) -> VectorStoreRow
  list_active(*, tenant_id, limit, offset) -> list[VectorStoreRow]   newest first
  get_active(*, tenant_id, vector_store_id) -> VectorStoreRow | None
  delete(*, tenant_id, vector_store_id) -> bool
      HARD delete — one atomic DELETE ... WHERE id AND tenant_id RETURNING id;
      FK CASCADE wipes vector_store_files + vector_store_chunks (M4).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.vector_stores.infrastructure.orm import VectorStoreRow


class VectorStoreRepository:
    """All vector-store CRUD persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        name: str | None,
        metadata: dict[str, str] | None,
    ) -> VectorStoreRow:
        """Insert a new vector_stores row and return it (server defaults populated).

        The container is metadata-only — no ZDR gate here (M8; the payload gate is
        the wave-2 chunk-write choke point, pinned by this contract, not this method).
        """
        row = VectorStoreRow(
            id=id,
            tenant_id=tenant_id,
            key_id=key_id,
            name=name,
            metadata_=metadata if metadata else {},
        )
        self._session.add(row)
        await self._session.flush()  # populate server defaults (created_at, status, ...)
        await self._session.refresh(row)
        return row

    async def list_active(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[VectorStoreRow]:
        """Return the tenant's vector stores, newest first."""
        stmt = (
            select(VectorStoreRow)
            .where(VectorStoreRow.tenant_id == tenant_id)
            .order_by(VectorStoreRow.created_at.desc(), VectorStoreRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active(
        self,
        *,
        tenant_id: uuid.UUID,
        vector_store_id: uuid.UUID,
    ) -> VectorStoreRow | None:
        """Load a single vector-store row. None for unknown/cross-tenant — never 403 (M5)."""
        stmt = select(VectorStoreRow).where(
            VectorStoreRow.id == vector_store_id,
            VectorStoreRow.tenant_id == tenant_id,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete(
        self,
        *,
        tenant_id: uuid.UUID,
        vector_store_id: uuid.UUID,
    ) -> bool:
        """HARD delete: one atomic DELETE ... RETURNING keyed (id, tenant_id) (M4).

        FK ON DELETE CASCADE wipes vector_store_files + vector_store_chunks — no
        orphan embeddings behind a soft-hidden row (payload-at-rest hygiene).
        Returns True if exactly one row was deleted, False otherwise (unknown id,
        another tenant's id, or already deleted — same result, no leak).
        """
        stmt = (
            sa_delete(VectorStoreRow)
            .where(
                VectorStoreRow.id == vector_store_id,
                VectorStoreRow.tenant_id == tenant_id,
            )
            .returning(VectorStoreRow.id)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.first() is not None
