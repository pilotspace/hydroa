"""SQLAlchemy repository for the memory domain.

INVARIANT: every method takes ``tenant_id`` and every query filters on it.
A cross-tenant or unknown id always returns None / empty — NEVER 403/200.
The router maps None → 404 with zero data leak.

Methods:
  create(*, tenant_id, key_id, content, meta) -> MemoryRow
  list_active(*, tenant_id, limit, offset) -> list[MemoryRow]
  load_for_search(*, tenant_id) -> list[MemoryRow]
  soft_delete(*, tenant_id, memory_id) -> bool
  set_embedding(*, tenant_id, memory_id, embedding) -> None
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.memory.infrastructure.orm import MemoryRow
from gateway.tenants.application.retention_policy import raise_if_zdr


class MemoryRepository:
    """All memory persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        content: str,
        meta: dict[str, object] | None,
    ) -> MemoryRow:
        """Insert a new memory row (embedding NULL initially) and return it.

        Fail-closed ZDR gate (tenant-retention-zdr TASK.md §3 M5): raises 403
        ERR_ZDR_PAYLOAD_BLOCKED, checked fresh, BEFORE the row is constructed.
        """
        await raise_if_zdr(self._session, tenant_id)
        row = MemoryRow(
            tenant_id=tenant_id,
            key_id=key_id,
            content=content,
            meta=meta,
            embedding=None,
        )
        self._session.add(row)
        await self._session.flush()  # populate server defaults (id, created_at)
        await self._session.refresh(row)
        return row

    async def list_active(
        self,
        *,
        tenant_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> list[MemoryRow]:
        """Return non-deleted memories for the tenant, newest first."""
        stmt = (
            select(MemoryRow)
            .where(
                MemoryRow.tenant_id == tenant_id,
                MemoryRow.deleted_at.is_(None),
            )
            .order_by(MemoryRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def load_for_search(
        self,
        *,
        tenant_id: uuid.UUID,
    ) -> list[MemoryRow]:
        """Load all non-deleted memories for this tenant for in-Python search ranking.

        The full result set is loaded because cosine ranking is done in Python.
        Soft-deleted rows are excluded. Embedding may be NULL (best-effort).
        """
        stmt = (
            select(MemoryRow)
            .where(
                MemoryRow.tenant_id == tenant_id,
                MemoryRow.deleted_at.is_(None),
            )
            .order_by(MemoryRow.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def soft_delete(
        self,
        *,
        tenant_id: uuid.UUID,
        memory_id: uuid.UUID,
    ) -> bool:
        """Soft-delete: set deleted_at = now() scoped to tenant_id.

        Returns True if exactly one row was updated, False otherwise
        (unknown id, belongs to another tenant, or already deleted — same result, no leak).
        """
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(MemoryRow)
            .where(
                MemoryRow.id == memory_id,
                MemoryRow.tenant_id == tenant_id,
                MemoryRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .returning(MemoryRow.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None

    async def set_embedding(
        self,
        *,
        tenant_id: uuid.UUID,
        memory_id: uuid.UUID,
        embedding: list[float],
    ) -> None:
        """Persist the embedding for an existing memory row (tenant-scoped update).

        Called after commit of the initial create so the row is already visible.
        No-op if the row was deleted between create and embed (unlikely).
        """
        await self._session.execute(
            update(MemoryRow)
            .where(
                MemoryRow.id == memory_id,
                MemoryRow.tenant_id == tenant_id,
                MemoryRow.deleted_at.is_(None),
            )
            .values(embedding=embedding)
        )
        await self._session.flush()
