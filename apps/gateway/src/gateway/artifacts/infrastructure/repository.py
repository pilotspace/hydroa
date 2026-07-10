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
from gateway.tenants.application.retention_policy import raise_if_zdr


class ArtifactRepository:
    """All artifact persistence, scoped to a single SQLAlchemy session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        name: str,
        content_type: str,
        size_bytes: int,
        storage_backend: str,
        object_key: str | None,
        content: bytes | None,
    ) -> ArtifactRow:
        """Insert a new artifact row and return it (with server defaults populated).

        ``id`` is supplied by the caller so the s3 object key is known BEFORE the
        write. ``content`` holds the inline BYTEA for storage_backend='inline'; for
        's3' it is None and the bytes live in the object store at ``object_key``.

        Fail-closed ZDR gate (tenant-retention-zdr TASK.md §3 M5): raises 403
        ERR_ZDR_PAYLOAD_BLOCKED, checked fresh, BEFORE the row is constructed.
        """
        await raise_if_zdr(self._session, tenant_id)
        row = ArtifactRow(
            id=id,
            tenant_id=tenant_id,
            key_id=key_id,
            name=name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_backend=storage_backend,
            object_key=object_key,
            content=content,
        )
        self._session.add(row)
        await self._session.flush()  # populate server defaults (created_at)
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
