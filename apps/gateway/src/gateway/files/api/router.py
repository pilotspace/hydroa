"""FastAPI router for the files domain (/v1/files) — OpenAI-wire file uploads.

files-uploads-api PLAN.md §3 (FROZEN @ v1). Auth + tenant-scope + honest-degrade
mirror gateway.artifacts.api.router; the wire (multipart ingress, ``file-<hex>`` ids,
``purpose`` vocabulary, ``status`` field, unix ``created_at``) is this task's own.

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` -> ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key -> 401 (ERR_AUTH_INVALID_KEY). Expired key -> 401 (ERR_AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the authenticated key.
Cross-tenant / unknown / deleted / malformed-id file -> 404 (ERR_FILE_NOT_FOUND). No oracle.

Upload: POST /v1/files (multipart/form-data: ``file`` part + ``purpose`` form field)
  - purpose not in {batch,vision,user_data} -> 422 ERR_FILE_PURPOSE_UNSUPPORTED.
  - zero-byte file -> 422 ERR_FILE_EMPTY.
  - size > settings.files_max_bytes (>0) -> 413 ERR_FILE_TOO_LARGE, BEFORE any store put or row.
  - ZDR tenant -> 403 ERR_ZDR_PAYLOAD_BLOCKED, no bytes persisted (checked before store put).
  - stores via the ObjectStore (s3) or honest-degrades to inline BYTEA.

Content: GET /v1/files/{file_id}/content
  - raw bytes, stored Content-Type, Content-Disposition ALWAYS attachment (XSS guard).
  - s3 row whose store is unreachable -> 503 ERR_OBJECT_STORE_UNAVAILABLE (never a 404 lie).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    FILE_EMPTY,
    FILE_NOT_FOUND,
    FILE_PURPOSE_UNSUPPORTED,
    FILE_TOO_LARGE,
    OBJECT_STORE_UNAVAILABLE,
)
from gateway.core.errors import ProblemError
from gateway.files.infrastructure.orm import FileRow
from gateway.files.infrastructure.repository import FileRepository
from gateway.files.wire_id import parse_wire_id, to_wire_id
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.objectstore import ObjectStore
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.tenants.application.retention_policy import raise_if_zdr

files_router = APIRouter(tags=["files"])

_log = logging.getLogger(__name__)

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

# The exact OpenAI purpose vocabulary this milestone supports (M6). assistants and
# every other value are rejected 422 ERR_FILE_PURPOSE_UNSUPPORTED. "fine-tune" added
# additively (finetune-broker PLAN.md §3 M8, FROZEN @ v1) — every existing purpose
# stays byte-identical.
_SUPPORTED_PURPOSES = frozenset({"batch", "vision", "user_data", "fine-tune"})


def _not_found() -> ProblemError:
    return FILE_NOT_FOUND.exc()


def _safe_filename(name: str) -> str:
    """Strip characters unsafe for a Content-Disposition filename value."""
    return re.sub(r'["\r\n\x00-\x1f\x7f]', "", name)


def _extract_raw_key(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _authenticate(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzResult:
    """Dependency: authenticate Bearer sk-... key -> AuthzResult. 401 on any failure."""
    raw_key = _extract_raw_key(request)
    repo = SqlAlchemyApiKeyRepository(session)
    authz_use_case = AuthzUseCase(repo, _hasher)
    authenticator = SqlAlchemyKeyAuthenticator(authz_use_case)
    try:
        authz = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID.exc() from None
    if authz.expires_at is not None:
        exp = authz.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if exp <= datetime.now(tz=UTC):
            raise AUTH_KEY_EXPIRED.exc() from None
    return authz


def _get_object_store(request: Request) -> ObjectStore | None:
    """The configured ObjectStore, or None (honest-degrade to inline BYTEA)."""
    return getattr(request.app.state, "object_store", None)


def _get_repo(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FileRepository:
    return FileRepository(session, object_store=_get_object_store(request))


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _file_object(row: FileRow) -> dict[str, Any]:
    """The OpenAI File object wire shape (created_at as a unix int)."""
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": to_wire_id(row.id),
        "object": "file",
        "bytes": row.bytes,
        "created_at": int(created.timestamp()),
        "filename": row.filename,
        "purpose": row.purpose,
        "status": row.status,
    }


def _resolve_file_id(file_id: str) -> uuid.UUID:
    """Parse a ``file-<hex>`` path segment to a UUID, or raise 404 (no oracle)."""
    parsed = parse_wire_id(file_id)
    if parsed is None:
        raise _not_found()
    return parsed


@files_router.post("/v1/files", status_code=200)
async def create_file(
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[FileRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str, Form()],
) -> dict[str, Any]:
    """Upload a new file for the authenticated tenant.

    Validates purpose / non-empty / size cap, ZDR-gates, then persists bytes via the
    ObjectStore (s3) or inline BYTEA. Returns the OpenAI File object (200).
    """
    # purpose vocabulary — 422 BEFORE reading bytes / any side effect.
    if purpose not in _SUPPORTED_PURPOSES:
        raise FILE_PURPOSE_UNSUPPORTED.exc() from None

    settings = _get_settings(request)
    # Bounded read (defence-in-depth): read at most files_max_bytes+1 bytes so an oversize
    # upload never fully materializes in the handler's memory — this holds even if the outer
    # BodySizeLimitMiddleware /v1/files cap is ever raised or removed. A file at or below the
    # cap is read in full (read(cap+1) returns the whole body when it is <= cap); a single
    # byte over the cap is enough to reject with the contracted 413 BEFORE any object-store
    # put or DB row. 0 = disabled -> no per-file cap, read the whole body. The outer guard
    # (main._files_route_cap) sits a headroom above this so THIS check owns the boundary.
    max_bytes = settings.files_max_bytes
    if max_bytes > 0:
        data = await file.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FILE_TOO_LARGE.exc() from None
    else:
        data = await file.read()

    # zero-byte / missing file part -> 422.
    if len(data) == 0:
        raise FILE_EMPTY.exc() from None

    # ZDR fail-closed gate — checked BEFORE the object-store write below (not only inside
    # repo.create's own raise_if_zdr) so a ZDR-blocked upload never leaves payload bytes
    # sitting in the object store with no DB row (artifacts precedent / defence in depth).
    await raise_if_zdr(session, authz.tenant_id)

    content_type = file.content_type or "application/octet-stream"
    filename = file.filename or "upload"

    # Generate the id at the call site so the s3 object key is known BEFORE the write.
    file_id = uuid.uuid4()
    store = _get_object_store(request)
    if store is not None:
        # ATOMICITY: write the OBJECT first, then commit the row. A failed put -> no
        # row (503); a failed commit -> orphan object (reaped by the sweep), never a
        # row pointing at absent bytes (artifacts precedent).
        object_key = f"files/{authz.tenant_id}/{file_id}"
        try:
            await store.put(object_key, data, content_type)
        except ObjectStoreUnavailableError:
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
        row = await repo.create(
            id=file_id,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            filename=filename,
            purpose=purpose,
            bytes=len(data),
            content_type=content_type,
            storage_backend="s3",
            object_key=object_key,
            content=None,
        )
    else:
        row = await repo.create(
            id=file_id,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            filename=filename,
            purpose=purpose,
            bytes=len(data),
            content_type=content_type,
            storage_backend="inline",
            object_key=None,
            content=data,
        )
    await session.commit()

    return _file_object(row)


@files_router.get("/v1/files", status_code=200)
async def list_files(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[FileRepository, Depends(_get_repo)],
    purpose: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List non-deleted files for the tenant, newest first, metadata only (M2).

    Optional ``purpose`` filter. limit: default 50, capped at 200; offset >= 0.
    The raw content bytes are NEVER returned in the list envelope.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)
    rows = await repo.list_active(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
        purpose=purpose,
    )
    return {"object": "list", "data": [_file_object(row) for row in rows]}


@files_router.get("/v1/files/{file_id}", status_code=200)
async def retrieve_file(
    file_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[FileRepository, Depends(_get_repo)],
) -> dict[str, Any]:
    """Retrieve the File object (M3). 404 for unknown / cross-tenant / deleted / malformed."""
    row = await repo.get_active(tenant_id=authz.tenant_id, file_id=_resolve_file_id(file_id))
    if row is None:
        raise _not_found()
    return _file_object(row)


@files_router.get("/v1/files/{file_id}/content", status_code=200)
async def download_file_content(
    file_id: str,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[FileRepository, Depends(_get_repo)],
) -> Response:
    """Download file content as raw bytes (M4).

    200 with stored Content-Type + attachment Content-Disposition (ALWAYS attachment —
    XSS guard). 404 for unknown / cross-tenant / deleted / s3-object-vanished. 503 when
    an s3 row's object store is unavailable/unconfigured (honest, never a 404 lie).
    """
    row = await repo.get_active(tenant_id=authz.tenant_id, file_id=_resolve_file_id(file_id))
    if row is None:
        raise _not_found()

    if row.storage_backend == "s3":
        store = _get_object_store(request)
        if store is None or row.object_key is None:
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
        try:
            content = await store.get(row.object_key)
        except ObjectNotFoundError:
            raise _not_found() from None
        except ObjectStoreUnavailableError:
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
    else:
        content = row.content or b""

    safe = _safe_filename(row.filename)
    return Response(
        content=content,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@files_router.delete("/v1/files/{file_id}", status_code=200)
async def delete_file(
    file_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[FileRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Soft-delete a file (M5) -> {id, object:'file', deleted:true}. 404 otherwise."""
    resolved = _resolve_file_id(file_id)
    deleted = await repo.soft_delete(tenant_id=authz.tenant_id, file_id=resolved)
    if not deleted:
        raise _not_found()
    await session.commit()
    return {"id": to_wire_id(resolved), "object": "file", "deleted": True}
