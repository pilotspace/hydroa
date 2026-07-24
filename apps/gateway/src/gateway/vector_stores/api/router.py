"""FastAPI router for the vector-stores domain (/v1/vector_stores) — OpenAI-wire CRUD.

vector-store-core PLAN.md §3 (FROZEN @ v1). Auth + tenant-scope mirror
``gateway.files.api.router``; the wire (``vs_<32hex>`` ids, zeroed file_counts,
metadata-only container) is this task's own.

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` -> ``AuthzResult{tenant_id, key_id}``.
Missing/invalid key -> 401 (ERR_AUTH_INVALID_KEY). Expired key -> 401 (ERR_AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the authenticated key.
Cross-tenant/unknown/malformed vector-store id -> 404 (ERR_VECTOR_STORE_NOT_FOUND). No oracle.

Create: POST /v1/vector_stores {name?, metadata?}
  - name > 256 chars -> 422 ERR_VECTOR_STORE_NAME_TOO_LONG, nothing persisted.
  - metadata not a mapping of <=16 string:string pairs (key<=64, value<=512) ->
    422 ERR_VECTOR_STORE_METADATA_INVALID, nothing persisted.
  - the container is metadata-only (M8 ZDR compose) — a ZDR tenant may create one;
    the payload gate (raise_if_zdr) is the wave-2 chunk-write choke point, not here.
  - writes ZERO usage_records (M7 — no provider cost on this surface).

Wire error envelope: the OpenAI SDK expects a bare ``{"error": {"code": ...}}`` body for
this surface — NOT the gateway's RFC-9457 problem+json shape — so every domain (422/404)
failure is returned as a JSONResponse here directly, mirroring the precedent set by
``gateway.usage.api.openai_usage_router`` (also a /v1/* OpenAI-wire surface with its own
envelope). Auth failures (401) still raise ProblemError via ``AUTH_KEY_INVALID``/
``AUTH_KEY_EXPIRED`` — the contract only pins their status code, not their body shape.

M9 byte-identical default path: this router + its side-effect ORM import are the
ONLY new plumbing — no middleware, no proxy-path change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    VECTOR_STORE_METADATA_INVALID,
    VECTOR_STORE_NAME_TOO_LONG,
    VECTOR_STORE_NOT_FOUND,
)
from gateway.core.error_catalog import ErrorSpec
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.vector_stores.infrastructure.orm import VectorStoreRow
from gateway.vector_stores.infrastructure.repository import VectorStoreRepository
from gateway.vector_stores.wire_id import parse_wire_id, to_wire_id

vector_stores_router = APIRouter(tags=["vector_stores"])

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

_MAX_NAME_LEN = 256
_MAX_METADATA_PAIRS = 16
_MAX_METADATA_KEY_LEN = 64
_MAX_METADATA_VALUE_LEN = 512
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


def _err(spec: ErrorSpec) -> JSONResponse:
    """Render an ErrorSpec as the OpenAI-wire ``{"error": {"code": ...}}`` envelope."""
    return JSONResponse(
        status_code=spec.status,
        content={"error": {"code": spec.code, "message": spec.title_template}},
    )


def _not_found() -> JSONResponse:
    return _err(VECTOR_STORE_NOT_FOUND)


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


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VectorStoreRepository:
    return VectorStoreRepository(session)


def _validate_name(name: str | None) -> JSONResponse | None:
    if name is not None and len(name) > _MAX_NAME_LEN:
        return _err(VECTOR_STORE_NAME_TOO_LONG)
    return None


def _validate_metadata(metadata: Any) -> tuple[dict[str, str], JSONResponse | None]:
    """Validate + normalize the optional metadata field (absent -> {}).

    Reject: not a mapping · > 16 pairs · key > 64 chars · value > 512 chars ·
    any non-string value. Returns (normalized_metadata, error_response_or_None).
    """
    if metadata is None:
        return {}, None
    if not isinstance(metadata, dict):
        return {}, _err(VECTOR_STORE_METADATA_INVALID)
    if len(metadata) > _MAX_METADATA_PAIRS:
        return {}, _err(VECTOR_STORE_METADATA_INVALID)
    for key, value in metadata.items():
        if not isinstance(key, str) or not isinstance(value, str):
            return {}, _err(VECTOR_STORE_METADATA_INVALID)
        if len(key) > _MAX_METADATA_KEY_LEN or len(value) > _MAX_METADATA_VALUE_LEN:
            return {}, _err(VECTOR_STORE_METADATA_INVALID)
    return metadata, None


def _zero_file_counts() -> dict[str, int]:
    return {"in_progress": 0, "completed": 0, "failed": 0, "cancelled": 0, "total": 0}


def _vector_store_object(row: VectorStoreRow) -> dict[str, Any]:
    """The OpenAI vector_store object wire shape (created_at as a unix int).

    file_counts is zeroed here — this task's container never attaches files;
    wave-2 computes it live (COUNT..GROUP BY vsf.status) with no contract change.
    """
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": to_wire_id(row.id),
        "object": "vector_store",
        "created_at": int(created.timestamp()),
        "name": row.name,
        "usage_bytes": row.usage_bytes,
        "status": row.status,
        "file_counts": _zero_file_counts(),
        "metadata": row.metadata_ or {},
    }


def _resolve_vector_store_id(vector_store_id: str) -> uuid.UUID | None:
    """Parse a ``vs_<32hex>`` path segment to a UUID, or None if malformed (no oracle)."""
    return parse_wire_id(vector_store_id)


@vector_stores_router.post("/v1/vector_stores", status_code=200, response_model=None)
async def create_vector_store(
    body: dict[str, Any],
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """Create a vector store for the authenticated tenant (M1).

    Both ``name`` and ``metadata`` are optional (OpenAI wire); validated BEFORE any
    persistence — a rejected request writes nothing (Reject clauses).
    """
    name = body.get("name")
    name_error = _validate_name(name)
    if name_error is not None:
        return name_error
    metadata, metadata_error = _validate_metadata(body.get("metadata"))
    if metadata_error is not None:
        return metadata_error

    row = await repo.create(
        id=uuid.uuid4(),
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        name=name,
        metadata=metadata,
    )
    await session.commit()
    return _vector_store_object(row)


@vector_stores_router.get("/v1/vector_stores", status_code=200)
async def list_vector_stores(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """List the tenant's vector stores, newest first (M2).

    limit: default 20, capped at 100 (OpenAI default); offset >= 0. ``has_more`` is
    a limit+1 probe, never COUNT(*).
    """
    effective_limit = min(max(limit, 1), _MAX_LIMIT)
    effective_offset = max(offset, 0)
    rows = await repo.list_active(
        tenant_id=authz.tenant_id,
        limit=effective_limit + 1,
        offset=effective_offset,
    )
    has_more = len(rows) > effective_limit
    page = rows[:effective_limit]
    return {
        "object": "list",
        "data": [_vector_store_object(row) for row in page],
        "has_more": has_more,
    }


@vector_stores_router.get("/v1/vector_stores/{vector_store_id}", status_code=200, response_model=None)
async def retrieve_vector_store(
    vector_store_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
) -> dict[str, Any] | JSONResponse:
    """Retrieve the vector_store object (M3). 404 for unknown/cross-tenant/malformed."""
    resolved = _resolve_vector_store_id(vector_store_id)
    if resolved is None:
        return _not_found()
    row = await repo.get_active(tenant_id=authz.tenant_id, vector_store_id=resolved)
    if row is None:
        return _not_found()
    return _vector_store_object(row)


@vector_stores_router.delete("/v1/vector_stores/{vector_store_id}", status_code=200, response_model=None)
async def delete_vector_store(
    vector_store_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """HARD delete a vector store (M4) -> {id, object:'vector_store.deleted', deleted:true}.

    404 otherwise. FK CASCADE wipes vector_store_files + vector_store_chunks.
    """
    resolved = _resolve_vector_store_id(vector_store_id)
    if resolved is None:
        return _not_found()
    deleted = await repo.delete(tenant_id=authz.tenant_id, vector_store_id=resolved)
    if not deleted:
        return _not_found()
    await session.commit()
    return {"id": to_wire_id(resolved), "object": "vector_store.deleted", "deleted": True}
