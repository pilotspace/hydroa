"""SQLAlchemy repository for the files domain (files-uploads-api PLAN.md §3).

INVARIANT: every method takes ``tenant_id`` and every query filters on it. A
cross-tenant or unknown id always returns None / empty — NEVER 403/200. The router
maps None -> 404 (ERR_FILE_NOT_FOUND) with zero data leak.

Mirrors ``gateway.artifacts.infrastructure.repository.ArtifactRepository`` verbatim
in shape (create / list_active / get_active / soft_delete + best-effort s3 purge on
delete). ``get_active`` is ALSO the seam the batches router uses to validate an
``input_file_id`` reference (tenant-scoped, then a purpose check in the caller) — one
tenant-scoped SELECT, no cross-module FK.

Methods:
  create(*, id, tenant_id, key_id, filename, purpose, bytes, content_type,
         storage_backend, object_key, content) -> FileRow
  list_active(*, tenant_id, limit, offset, purpose=None) -> list[FileRow]  [metadata cols]
  get_active(*, tenant_id, file_id) -> FileRow | None   [loads ALL cols incl. content]
  soft_delete(*, tenant_id, file_id) -> bool            [best-effort s3 byte purge]
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from gateway.files.infrastructure.orm import FileRow
from gateway.objectstore.errors import ObjectStoreUnavailableError
from gateway.objectstore.port import ObjectStore
from gateway.tenants.application.retention_policy import raise_if_zdr

_log = logging.getLogger(__name__)


class FileRepository:
    """All file persistence, scoped to a single SQLAlchemy session.

    ``object_store`` is optional (default None): when configured, soft_delete()
    best-effort purges an s3-backed file's bytes at delete time; when None, the
    object-store purge honest-degrades to a no-op (logged) and only the DB row /
    inline bytes are cleared — the periodic RetentionSweeper's ZDR/window passes
    remain the backstop for any orphaned s3 object either way (mirrors artifacts).
    """

    def __init__(self, session: AsyncSession, object_store: ObjectStore | None = None) -> None:
        self._session = session
        self._object_store = object_store

    async def create(
        self,
        *,
        id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        filename: str,
        purpose: str,
        bytes: int,
        content_type: str,
        storage_backend: str,
        object_key: str | None,
        content: bytes | None,
    ) -> FileRow:
        """Insert a new file row and return it (with server defaults populated).

        ``id`` is supplied by the caller so the s3 object key is known BEFORE the
        write. ``content`` holds the inline BYTEA for storage_backend='inline'; for
        's3' it is None and the bytes live in the object store at ``object_key``.

        Fail-closed ZDR gate (tenant-retention-zdr §3 M5): raises 403
        ERR_ZDR_PAYLOAD_BLOCKED, checked fresh, BEFORE the row is constructed — the
        router ALSO checks it ahead of any object-store put (defence in depth, so a
        ZDR-blocked upload never leaves bytes in the store with no row).
        """
        await raise_if_zdr(self._session, tenant_id)
        row = FileRow(
            id=id,
            tenant_id=tenant_id,
            key_id=key_id,
            filename=filename,
            purpose=purpose,
            bytes=bytes,
            content_type=content_type,
            status="processed",
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
        purpose: str | None = None,
    ) -> list[FileRow]:
        """Return non-deleted files for the tenant, newest first.

        Does NOT load the ``content`` column (metadata only — M2: raw bytes never
        appear in the list envelope). ``purpose`` optionally filters the list.
        """
        stmt = (
            select(FileRow)
            .options(
                load_only(
                    FileRow.id,
                    FileRow.tenant_id,
                    FileRow.key_id,
                    FileRow.filename,
                    FileRow.purpose,
                    FileRow.bytes,
                    FileRow.content_type,
                    FileRow.status,
                    FileRow.created_at,
                    FileRow.deleted_at,
                )
            )
            .where(
                FileRow.tenant_id == tenant_id,
                FileRow.deleted_at.is_(None),
            )
            .order_by(FileRow.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if purpose is not None:
            stmt = stmt.where(FileRow.purpose == purpose)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active(
        self,
        *,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> FileRow | None:
        """Load a single file row including content bytes.

        Returns None for unknown, cross-tenant, or deleted files — never 403. Also the
        validation seam for the batches ``input_file_id`` reference (caller then checks
        purpose=='batch').
        """
        stmt = select(FileRow).where(
            FileRow.id == file_id,
            FileRow.tenant_id == tenant_id,
            FileRow.deleted_at.is_(None),
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def soft_delete(
        self,
        *,
        tenant_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> bool:
        """Soft-delete: set deleted_at=now() AND clear the inline ``content`` bytes,
        scoped to tenant_id — plus a best-effort ObjectStore.delete() for an s3-backed
        row (a "deleted" file must have its bytes actually gone, not merely be
        DB-invisible — artifacts precedent).

        Returns True if exactly one row was updated, False otherwise (unknown id,
        another tenant's id, or already deleted — same result, no leak).

        Design for failure: the object-store delete happens AFTER the DB soft-delete is
        flushed and is strictly best-effort — an ObjectStoreUnavailableError (or no
        ObjectStore configured) is logged and swallowed; it NEVER raises, never rolls
        back, never blocks the tenant-visible delete on an object-store outage. A
        deferred/failed object purge is still eventually reclaimed by the periodic
        RetentionSweeper's ZDR/window passes.
        """
        now = datetime.now(tz=UTC)
        result = await self._session.execute(
            update(FileRow)
            .where(
                FileRow.id == file_id,
                FileRow.tenant_id == tenant_id,
                FileRow.deleted_at.is_(None),
            )
            .values(deleted_at=now, content=None)
            .returning(FileRow.id, FileRow.object_key, FileRow.storage_backend)
        )
        await self._session.flush()
        row = result.first()
        if row is None:
            return False
        _file_id, object_key, storage_backend = row

        if storage_backend == "s3" and object_key:
            if self._object_store is None:
                _log.warning(
                    "file soft_delete: no ObjectStore configured — s3 object key=%s for"
                    " file=%s left in place (the retention sweep is the backstop)",
                    object_key,
                    file_id,
                )
            else:
                try:
                    await self._object_store.delete(object_key)
                except ObjectStoreUnavailableError as exc:
                    _log.warning(
                        "file soft_delete: ObjectStore.delete failed for key=%s file=%s"
                        " (DB soft-delete already flushed; swallowed, the retention sweep"
                        " retries this object): %s",
                        object_key,
                        file_id,
                        exc,
                    )
        return True
