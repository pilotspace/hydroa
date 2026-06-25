"""SQLAlchemy repository for the artifacts domain.

INVARIANT: every method takes ``tenant_id`` and every query filters on it.
A cross-tenant or unknown id always returns None / empty — NEVER 403/200.
The router maps None → 404 with zero data leak.

Methods:
  create(*, tenant_id, key_id, name, content_type, size_bytes, content) -> ArtifactRow
  list_active(*, tenant_id, limit, offset) -> list[ArtifactRow]  [load_only metadata cols]
  get_active(*, tenant_id, artifact_id) -> ArtifactRow | None  [loads ALL cols including content]
  soft_delete(*, tenant_id, artifact_id) -> bool
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from gateway.artifacts.infrastructure.orm import ArtifactRow


class ArtifactRepository:
    """All artifact persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        name: str,
        content_type: str,
        size_bytes: int,
        content: bytes,
    ) -> ArtifactRow:
        """Insert a new artifact row and return it (with server defaults populated)."""
        row = ArtifactRow(
            tenant_id=tenant_id,
            key_id=key_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            content=content,
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
    ) -> list[ArtifactRow]:
        """Return non-deleted artifacts for the tenant, newest first.

        Does NOT load the ``content`` column to avoid pulling large blobs.
        """
        stmt = (
            select(ArtifactRow)
            .options(
                load_only(
                    ArtifactRow.id,
                    ArtifactRow.tenant_id,
                    ArtifactRow.key_id,
                    ArtifactRow.name,
                    ArtifactRow.content_type,
                    ArtifactRow.size_bytes,
                    ArtifactRow.created_at,
                    ArtifactRow.deleted_at,
                )
            )
            .where(
                ArtifactRow.tenant_id == tenant_id,
                ArtifactRow.deleted_at.is_(None),
            )
            .order_by(ArtifactRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> ArtifactRow | None:
        """Load a single artifact row including content bytes.

        Returns None for unknown, cross-tenant, or deleted artifacts — never 403.
        """
        stmt = select(ArtifactRow).where(
            ArtifactRow.id == artifact_id,
            ArtifactRow.tenant_id == tenant_id,
            ArtifactRow.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(
        self,
        *,
        tenant_id: uuid.UUID,
        artifact_id: uuid.UUID,
    ) -> bool:
        """Soft-delete: set deleted_at = now() scoped to tenant_id.

        Returns True if exactly one row was updated, False otherwise
        (unknown id, belongs to another tenant, or already deleted — same result, no leak).
        """
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(ArtifactRow)
            .where(
                ArtifactRow.id == artifact_id,
                ArtifactRow.tenant_id == tenant_id,
                ArtifactRow.deleted_at.is_(None),
            )
            .values(deleted_at=now)
            .returning(ArtifactRow.id)
        )
        await self._session.flush()
        return result.scalar_one_or_none() is not None
