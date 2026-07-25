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

import asyncio
import logging
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
    FILE_NOT_FOUND,
    VECTOR_STORE_FILE_ID_REQUIRED,
    VECTOR_STORE_FILE_PURPOSE_INVALID,
    VECTOR_STORE_METADATA_INVALID,
    VECTOR_STORE_NAME_TOO_LONG,
    VECTOR_STORE_NOT_FOUND,
)
from gateway.core.error_catalog import ErrorSpec
from gateway.files.infrastructure.repository import FileRepository
from gateway.files.wire_id import parse_wire_id as parse_file_wire_id
from gateway.files.wire_id import to_wire_id as to_file_wire_id
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.tenants.application.retention_policy import raise_if_zdr
from gateway.vector_stores.infrastructure.file_repository import VectorStoreFileRepository
from gateway.vector_stores.infrastructure.orm import VectorStoreFileRow, VectorStoreRow
from gateway.vector_stores.infrastructure.repository import VectorStoreRepository
from gateway.vector_stores.wire_id import parse_wire_id, to_wire_id

vector_stores_router = APIRouter(tags=["vector_stores"])

_log = logging.getLogger(__name__)

# vector-store-files PLAN.md §3 (FROZEN @ v1, M1 Reject): a file attached to a
# vector store must carry one of these purposes — mirrors the /v1/files purpose
# vocabulary additive-"assistants" entry (M9), plus the pre-existing "user_data".
_ATTACHABLE_PURPOSES = frozenset({"assistants", "user_data"})
_DEFAULT_FILES_LIMIT = 20
_MAX_FILES_LIMIT = 100

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


def _vector_store_object(
    row: VectorStoreRow, *, file_counts: dict[str, int] | None = None
) -> dict[str, Any]:
    """The OpenAI vector_store object wire shape (created_at as a unix int).

    file_counts defaults to zeroed (a freshly created container has none); wave-2
    (vector-store-files PLAN.md §3, M8) computes it LIVE (COUNT..GROUP BY
    vsf.status) for retrieve/list — pre-authorized by this docstring, no contract
    change.
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
        "file_counts": file_counts if file_counts is not None else _zero_file_counts(),
        "metadata": row.metadata_ or {},
    }


def _resolve_vector_store_id(vector_store_id: str) -> uuid.UUID | None:
    """Parse a ``vs_<32hex>`` path segment to a UUID, or None if malformed (no oracle)."""
    return parse_wire_id(vector_store_id)


def _not_found_file() -> JSONResponse:
    return _err(FILE_NOT_FOUND)


def _get_file_repo(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VectorStoreFileRepository:
    return VectorStoreFileRepository(session)


def _vector_store_file_object(row: VectorStoreFileRow) -> dict[str, Any]:
    """The OpenAI vector_store.file object wire shape (vector-store-files §3).

    ``id`` is the ATTACHED FILE's own wire id (``file-<32hex>``) — NOT the vsf
    row's internal id — mirroring OpenAI's vector-store-file object shape.
    """
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": to_file_wire_id(row.file_id),
        "object": "vector_store.file",
        "created_at": int(created.timestamp()),
        "vector_store_id": to_wire_id(row.vector_store_id),
        "usage_bytes": row.usage_bytes,
        "status": row.status,
        "last_error": row.last_error,
    }


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
    file_repo: Annotated[VectorStoreFileRepository, Depends(_get_file_repo)],
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
    data = []
    for row in page:
        counts = await file_repo.file_counts(row.id)
        data.append(_vector_store_object(row, file_counts=counts))
    return {
        "object": "list",
        "data": data,
        "has_more": has_more,
    }


@vector_stores_router.get("/v1/vector_stores/{vector_store_id}", status_code=200, response_model=None)
async def retrieve_vector_store(
    vector_store_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    file_repo: Annotated[VectorStoreFileRepository, Depends(_get_file_repo)],
) -> dict[str, Any] | JSONResponse:
    """Retrieve the vector_store object (M3). 404 for unknown/cross-tenant/malformed.

    file_counts is LIVE (vector-store-files PLAN.md §3, M8).
    """
    resolved = _resolve_vector_store_id(vector_store_id)
    if resolved is None:
        return _not_found()
    row = await repo.get_active(tenant_id=authz.tenant_id, vector_store_id=resolved)
    if row is None:
        return _not_found()
    counts = await file_repo.file_counts(row.id)
    return _vector_store_object(row, file_counts=counts)


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


# ---------------------------------------------------------------------------
# vector-store-files PLAN.md §3 (FROZEN @ v1) — async attach + background worker
# ---------------------------------------------------------------------------


async def _enqueue_or_fallback(request: Request, row_id: uuid.UUID) -> None:
    """Enqueue row_id for ingestion; fail-open to an inline drive (M1).

    Mirrors the batches durable-queue router's dispatch_batch_job fail-open
    idiom: if the wired queue is missing (Redis down / lifespan never ran, e.g.
    under ASGITransport tests) or the enqueue raises, fall back to an inline
    asyncio.create_task drive so no job is ever dropped.
    """
    app_state = request.app.state
    queue = getattr(app_state, "vector_store_ingest_queue", None)
    if queue is not None:
        try:
            await queue.enqueue(row_id)
            return
        except Exception:
            _log.warning(
                "vector_store ingest enqueue failed; falling back to an inline drive for row %s",
                row_id,
            )

    redis_client = getattr(app_state, "redis_client", None)
    if redis_client is None:
        _log.error(
            "vector_store ingest: no queue and no redis_client wired — row %s stays"
            " in_progress until the next attach/recover_orphans pass",
            row_id,
        )
        return

    # Import here (not at module top) to avoid a hard import-time dependency of
    # this router on the worker module in the common (queue-already-wired) path.
    from gateway.vector_stores.application.ingest_worker import (  # noqa: PLC0415
        RedisVectorStoreIngestQueue,
        VectorStoreIngestWorker,
    )

    fallback_queue = queue if queue is not None else RedisVectorStoreIngestQueue(redis_client)
    worker = VectorStoreIngestWorker(
        sessionmaker=app_state.sessionmaker,
        queue=fallback_queue,
        settings=app_state.settings,
        get_embedder=lambda: getattr(app_state, "vector_store_embedder", None),
        object_store=getattr(app_state, "object_store", None),
    )
    tasks_set: set[Any] | None = getattr(app_state, "vector_store_ingest_tasks", None)
    if tasks_set is None:
        tasks_set = set()
        app_state.vector_store_ingest_tasks = tasks_set

    async def _drive_and_discard() -> None:
        try:
            await worker.drive(row_id)
        except Exception:
            _log.exception(
                "vector_store ingest: inline fallback drive failed for row %s", row_id
            )
        finally:
            tasks_set.discard(task)

    task = asyncio.create_task(_drive_and_discard())
    tasks_set.add(task)


async def _resolve_attachable_file(
    session: AsyncSession, *, tenant_id: uuid.UUID, file_id: str
) -> tuple[Any, JSONResponse | None]:
    """Resolve + validate the file reference for attach. Returns (file_row, error)."""
    parsed = parse_file_wire_id(file_id)
    if parsed is None:
        return None, _not_found_file()
    file_row = await FileRepository(session).get_active(tenant_id=tenant_id, file_id=parsed)
    if file_row is None:
        return None, _not_found_file()
    if file_row.purpose not in _ATTACHABLE_PURPOSES:
        return None, _err(VECTOR_STORE_FILE_PURPOSE_INVALID)
    return file_row, None


@vector_stores_router.post(
    "/v1/vector_stores/{vector_store_id}/files", status_code=200, response_model=None
)
async def attach_vector_store_file(
    vector_store_id: str,
    body: dict[str, Any],
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    file_repo: Annotated[VectorStoreFileRepository, Depends(_get_file_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any] | JSONResponse:
    """NON-BLOCKING attach (M1): validate, commit `in_progress`, enqueue, return
    immediately — NO chunking/embed work happens in this request path.

    M7 retry/healing semantics on re-attach: a `failed` row CAS-flips back to
    `in_progress` and re-enqueues; an `in_progress` row is returned as-is AND
    re-enqueued (heals crash-stranded rows — safe, the worker's terminal skip +
    finalize CAS make a duplicate drive a no-op); a `completed` row is returned
    as-is, never re-run.
    """
    resolved_store = _resolve_vector_store_id(vector_store_id)
    if resolved_store is None:
        return _not_found()
    store_row = await repo.get_active(tenant_id=authz.tenant_id, vector_store_id=resolved_store)
    if store_row is None:
        return _not_found()

    file_id = body.get("file_id")
    if not isinstance(file_id, str) or not file_id:
        return _err(VECTOR_STORE_FILE_ID_REQUIRED)

    file_row, file_error = await _resolve_attachable_file(
        session, tenant_id=authz.tenant_id, file_id=file_id
    )
    if file_error is not None:
        return file_error
    assert file_row is not None

    # M5: ZDR fail-closed at the attach entry — 403, ZERO rows of any kind.
    await raise_if_zdr(session, authz.tenant_id)

    row = await file_repo.get_or_create(
        tenant_id=authz.tenant_id,
        vector_store_id=resolved_store,
        file_id=file_row.id,
    )

    should_enqueue = False
    if row.status == "in_progress":
        should_enqueue = True
    elif row.status == "failed":
        flipped = await file_repo.retry_if_failed(row.id)
        if flipped:
            row.status = "in_progress"
            row.last_error = None
            should_enqueue = True
    # status == "completed": returned as-is, never re-run.

    await session.commit()

    if should_enqueue:
        await _enqueue_or_fallback(request, row.id)

    return _vector_store_file_object(row)


@vector_stores_router.get(
    "/v1/vector_stores/{vector_store_id}/files/{file_id}",
    status_code=200,
    response_model=None,
)
async def retrieve_vector_store_file(
    vector_store_id: str,
    file_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    file_repo: Annotated[VectorStoreFileRepository, Depends(_get_file_repo)],
) -> dict[str, Any] | JSONResponse:
    """Poll the vector_store.file object (M3). 404 for unknown/cross-tenant/malformed
    store OR file id — uniform, no oracle."""
    resolved_store = _resolve_vector_store_id(vector_store_id)
    if resolved_store is None:
        return _not_found()
    store_row = await repo.get_active(tenant_id=authz.tenant_id, vector_store_id=resolved_store)
    if store_row is None:
        return _not_found()

    resolved_file = parse_file_wire_id(file_id)
    if resolved_file is None:
        return _not_found_file()

    row = await file_repo.get_by_file(
        tenant_id=authz.tenant_id, vector_store_id=resolved_store, file_id=resolved_file
    )
    if row is None:
        return _not_found_file()
    return _vector_store_file_object(row)


@vector_stores_router.get(
    "/v1/vector_stores/{vector_store_id}/files", status_code=200, response_model=None
)
async def list_vector_store_files(
    vector_store_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VectorStoreRepository, Depends(_get_repo)],
    file_repo: Annotated[VectorStoreFileRepository, Depends(_get_file_repo)],
    limit: int = _DEFAULT_FILES_LIMIT,
    offset: int = 0,
) -> dict[str, Any] | JSONResponse:
    """List attached files for a store, newest first (M4). limit<=100, limit+1 probe."""
    resolved_store = _resolve_vector_store_id(vector_store_id)
    if resolved_store is None:
        return _not_found()
    store_row = await repo.get_active(tenant_id=authz.tenant_id, vector_store_id=resolved_store)
    if store_row is None:
        return _not_found()

    effective_limit = min(max(limit, 1), _MAX_FILES_LIMIT)
    effective_offset = max(offset, 0)
    rows = await file_repo.list_for_store(
        tenant_id=authz.tenant_id,
        vector_store_id=resolved_store,
        limit=effective_limit + 1,
        offset=effective_offset,
    )
    has_more = len(rows) > effective_limit
    page = rows[:effective_limit]
    return {
        "object": "list",
        "data": [_vector_store_file_object(row) for row in page],
        "has_more": has_more,
    }
