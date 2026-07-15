"""FastAPI router for the artifacts domain (/v1/artifacts).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` → ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key → 401 (AUTH_KEY_INVALID).
Expired key → 401 (AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the
authenticated key. Cross-tenant / unknown / deleted resource → 404.

Upload: POST /v1/artifacts
  - Decodes base64 content (validate=True); binascii.Error → 422.
  - Checks decoded size against settings.artifact_max_bytes (0 = disabled).
  - Over-cap → 413 BEFORE any insert.
  - Stores decoded bytes in BYTEA column.

Download: GET /v1/artifacts/{id}
  - Returns raw bytes with stored Content-Type.
  - Content-Disposition is ALWAYS attachment (NEVER inline — XSS guard).

List: GET /v1/artifacts?limit&offset
  - Returns metadata only (NO content bytes in response).
  - Newest first; deleted_at IS NULL; limit default 50 cap 200.

Delete: DELETE /v1/artifacts/{id}
  - Soft delete (sets deleted_at = now()); 204 on success, 404 otherwise.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.artifacts.content_type_policy import is_content_type_allowed
from gateway.artifacts.infrastructure.repository import ArtifactRepository
from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    ARTIFACT_CONTENT_TYPE_NOT_ALLOWED,
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    OBJECT_STORE_UNAVAILABLE,
    PAYLOAD_INPUT_TOO_LONG,
    PAYLOAD_INVALID_BASE64,
)
from gateway.core.errors import ProblemError
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.objectstore import ObjectStore
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.tenants.application.retention_policy import raise_if_zdr

artifacts_router = APIRouter(tags=["artifacts"])

_log = logging.getLogger(__name__)

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

# ---------------------------------------------------------------------------
# 404 helper
# ---------------------------------------------------------------------------


def _not_found() -> ProblemError:
    return ProblemError(
        status=404,
        code="ERR_ARTIFACT_NOT_FOUND",
        title="Artifact not found",
    )


# ---------------------------------------------------------------------------
# Filename sanitizer — strips quotes, newlines, and control chars (XSS guard)
# ---------------------------------------------------------------------------


def _safe_filename(name: str) -> str:
    """Strip characters unsafe for Content-Disposition filename value."""
    return re.sub(r'["\r\n\x00-\x1f\x7f]', "", name)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def _extract_raw_key(request: Request) -> str:
    """Extract raw Bearer token from Authorization header, or empty string."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


async def _authenticate(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthzResult:
    """Dependency: authenticate Bearer sk-... key → AuthzResult.

    Raises 401 on any failure (missing / invalid / expired).
    """
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


def _get_repo(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArtifactRepository:
    # audit-remediation: wire the ObjectStore so DELETE /v1/artifacts/{id} actually
    # purges s3 object bytes (+ clears inline BYTEA), not just soft-deletes the row.
    return ArtifactRepository(session, object_store=_get_object_store(request))


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _get_object_store(request: Request) -> ObjectStore | None:
    """The configured ObjectStore, or None (honest-degrade to inline BYTEA)."""
    return getattr(request.app.state, "object_store", None)


# ---------------------------------------------------------------------------
# Request / Response schemas (inline — no separate schemas.py)
# ---------------------------------------------------------------------------


class CreateArtifactRequest(BaseModel):
    name: str
    content_type: str
    content_base64: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name must be a non-empty string")
        return v


class ArtifactCreateResponse(BaseModel):
    id: uuid.UUID
    name: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactListItem(BaseModel):
    id: uuid.UUID
    name: str
    content_type: str
    size_bytes: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ArtifactListResponse(BaseModel):
    data: list[ArtifactListItem]
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@artifacts_router.post(
    "/v1/artifacts",
    response_model=ArtifactCreateResponse,
    status_code=201,
)
async def create_artifact(
    body: CreateArtifactRequest,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ArtifactRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArtifactCreateResponse:
    """Upload a new artifact for the authenticated tenant.

    Decodes base64 content, enforces size cap, then persists BYTEA content.
    Returns 422 on invalid base64 or empty name; 413 when decoded size exceeds cap.
    """
    # Decode and validate base64 — binascii.Error on invalid input
    try:
        decoded = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise PAYLOAD_INVALID_BASE64.exc() from None

    # Size cap check BEFORE insert
    settings = _get_settings(request)
    if settings.artifact_max_bytes > 0 and len(decoded) > settings.artifact_max_bytes:
        raise PAYLOAD_INPUT_TOO_LONG.exc() from None

    # Content-type allow-policy — 415 BEFORE any object-store put or DB write/commit.
    # Empty allow-list (default "") is a no-op: allow-all, byte-identical to pre-policy.
    if not is_content_type_allowed(body.content_type, settings.artifact_allowed_content_types):
        raise ARTIFACT_CONTENT_TYPE_NOT_ALLOWED.exc(
            content_type=body.content_type,
            detail=(
                f"content_type '{body.content_type}' is not allowed; "
                f"allowed: {settings.artifact_allowed_content_types!r}"
            ),
        ) from None

    # ZDR fail-closed gate (tenant-retention-zdr TASK.md §3 M5 / FINDINGS #1): must be
    # checked BEFORE the object-store write below, not just inside repo.create()'s own
    # raise_if_zdr — otherwise a ZDR-blocked upload still leaves the real payload bytes
    # durably sitting in the object store with no DB row for the sweeper to ever find.
    # Checked fresh, after input validation (base64/size/content-type), ahead of BOTH
    # side effects (object-store put and DB insert) so they share one gate.
    await raise_if_zdr(session, authz.tenant_id)

    # Generate the id at the call site so the s3 object key is known BEFORE the write.
    artifact_id = uuid.uuid4()
    store = _get_object_store(request)
    if store is not None:
        # ATOMICITY: write the OBJECT first, then commit the row. A failed put → no
        # row (503); a failed commit → orphan object (reaped by the sweep), never a
        # row pointing at absent bytes.
        object_key = f"artifacts/{authz.tenant_id}/{artifact_id}"
        try:
            await store.put(object_key, decoded, body.content_type)
        except ObjectStoreUnavailableError:
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
        row = await repo.create(
            id=artifact_id,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            name=body.name,
            content_type=body.content_type,
            size_bytes=len(decoded),
            storage_backend="s3",
            object_key=object_key,
            content=None,
        )
    else:
        # Honest-degrade: no object store configured → inline BYTEA (the v45 path).
        row = await repo.create(
            id=artifact_id,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            name=body.name,
            content_type=body.content_type,
            size_bytes=len(decoded),
            storage_backend="inline",
            object_key=None,
            content=decoded,
        )
    await session.commit()

    return ArtifactCreateResponse(
        id=row.id,
        name=row.name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        created_at=row.created_at,
    )


@artifacts_router.get(
    "/v1/artifacts",
    response_model=ArtifactListResponse,
)
async def list_artifacts(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ArtifactRepository, Depends(_get_repo)],
    limit: int = 50,
    offset: int = 0,
) -> ArtifactListResponse:
    """List non-deleted artifacts for the authenticated tenant, newest first.

    limit: default 50, capped at 200. offset: must be >= 0.
    The raw content bytes are NEVER returned — metadata only.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)

    rows = await repo.list_active(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
    )

    return ArtifactListResponse(
        data=[
            ArtifactListItem(
                id=row.id,
                name=row.name,
                content_type=row.content_type,
                size_bytes=row.size_bytes,
                created_at=row.created_at,
            )
            for row in rows
        ],
        limit=effective_limit,
        offset=effective_offset,
    )


@artifacts_router.get("/v1/artifacts/{artifact_id}")
async def download_artifact(
    artifact_id: uuid.UUID,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ArtifactRepository, Depends(_get_repo)],
) -> Response:
    """Download artifact content as raw bytes.

    Returns:
        200 with raw bytes, stored Content-Type, and attachment Content-Disposition.
        404 for unknown, cross-tenant, deleted, or s3-object-vanished artifacts.
        503 when an s3 row's object store is unavailable/unconfigured.

    The row lookup is tenant-scoped, so the object_key (and therefore any store
    call) is reached ONLY for a row the caller owns — no cross-tenant bytes.
    Content-Disposition is ALWAYS attachment — NEVER inline (XSS guard).
    """
    row = await repo.get_active(tenant_id=authz.tenant_id, artifact_id=artifact_id)
    if row is None:
        raise _not_found()

    if row.storage_backend == "s3":
        store = _get_object_store(request)
        if store is None or row.object_key is None:
            # The row exists but its bytes are unreachable (store unconfigured, or a
            # corrupt s3 row with no key) — honest 503, never a 404 lie or empty 200.
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
        try:
            content = await store.get(row.object_key)
        except ObjectNotFoundError:
            raise _not_found() from None
        except ObjectStoreUnavailableError:
            raise OBJECT_STORE_UNAVAILABLE.exc() from None
    else:
        content = row.content or b""

    safe = _safe_filename(row.name)
    return Response(
        content=content,
        media_type=row.content_type,
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


@artifacts_router.delete(
    "/v1/artifacts/{artifact_id}",
    status_code=204,
)
async def delete_artifact(
    artifact_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[ArtifactRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> None:
    """Soft-delete an artifact (sets deleted_at = now).

    Returns 404 for unknown, cross-tenant, or already-deleted artifacts.
    """
    deleted = await repo.soft_delete(
        tenant_id=authz.tenant_id,
        artifact_id=artifact_id,
    )
    if not deleted:
        raise _not_found()

    await session.commit()
