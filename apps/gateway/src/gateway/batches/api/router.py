"""FastAPI router for the batch job store domain (/v1/batches).

Contract FROZEN @ v1 (batch-job-store TASK.md §3).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` -> ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key -> 401 (AUTH_KEY_INVALID). Expired key -> 401 (AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the authenticated key.
Cross-tenant / unknown -> 404 (ERR_BATCH_JOB_NOT_FOUND) — no oracle.

Create: POST /v1/batches
  - Validates line_items (non-empty, under the cap, model+messages present per item,
    unique custom_id) -> 422 with a named code on any failure; the whole submission is
    atomic (zero rows on reject).
  - Inserts the job row (status=validating) + one item row per line item (status=pending)
    in ONE transaction, then spawns a background asyncio.Task (or durable-queue enqueue).
  - Returns 200 with the job envelope immediately (non-blocking).

Poll:   GET /v1/batches/{id}   -> 200 job shape; 404 for unknown or cross-tenant.
List:   GET /v1/batches?limit&offset -> tenant-scoped list, newest first.

Processing (in-process asyncio.Task or durable worker — see application/worker.py):
  set status=in_progress ->
  processor = app.state.batch_processor  # BatchProcessor Protocol
  if None  -> failed, error="no_batch_processor_configured" (this task's only reachable
              terminal — openai-batch-adapter/anthropic-batch-adapter plug in a real one)
  else     -> asyncio.wait_for(processor.process(job_id), timeout)
  set_failed() cascades every still-"pending" item to "errored" — a terminal job never
  leaves pending items behind. EACH status write uses a FRESH sessionmaker() session. The
  ENTIRE task body is wrapped; a raise never escapes (no loop crash).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.batches.application.worker import RedisBatchJobQueue
from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    BATCH_INPUT_AMBIGUOUS,
    BATCH_INPUT_FILE_INVALID,
    BATCH_ITEM_INVALID,
    BATCH_ITEMS_EMPTY,
    BATCH_ITEMS_TOO_MANY,
    BATCH_JOB_NOT_FOUND,
)
from gateway.files.infrastructure.repository import FileRepository
from gateway.files.wire_id import parse_wire_id, to_wire_id
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

batch_router = APIRouter(tags=["batches"])

_log = logging.getLogger(__name__)

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()

_ALL_PENDING_COUNTS: dict[str, int] = {
    "pending": 0,
    "succeeded": 0,
    "errored": 0,
    "canceled": 0,
    "expired": 0,
}


# ---------------------------------------------------------------------------
# Auth dependency (copied from video/artifacts routers — this codebase's convention)
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
    """Dependency: authenticate Bearer sk-... key -> AuthzResult.

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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchJobRepository:
    return BatchJobRepository(session)


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class LineItemRequest(BaseModel):
    custom_id: str
    body: dict[str, Any]


class CreateBatchJobRequest(BaseModel):
    # files-uploads-api PLAN.md §3 (ADDITIVE): line_items is now optional — exactly ONE of
    # {line_items, input_file_id} must be present (enforced in create_batch_job, not by
    # pydantic, so the miss is an app-level named code, not a generic 422). A pre-additive
    # client sending only line_items is byte-identical (input_file_id defaults to None).
    line_items: list[LineItemRequest] | None = None
    input_file_id: str | None = None


class BatchJobItemResponse(BaseModel):
    """Additive extension (batch-auto-grouping TASK.md §3, M7) — a single item's
    resolved result. result_body is None until the item reaches a terminal status."""

    custom_id: str
    status: str
    result_body: dict[str, Any] | None

    model_config = {"from_attributes": True}


class BatchJobResponse(BaseModel):
    id: uuid.UUID
    status: str
    item_count: int
    status_counts: dict[str, int]
    error: str | None
    created_at: datetime
    updated_at: datetime
    # files-uploads-api PLAN.md §3 (ADDITIVE) — the file-<hex> the job was created from,
    # or None for a line_items job. Default None keeps every existing construction
    # (list_batch_jobs, the line_items create path) byte-identical.
    input_file_id: str | None = None
    # Additive extension (batch-auto-grouping TASK.md §3, M7) — defaults to [] so
    # list_batch_jobs's existing per-job construction (no item-level query there,
    # by design — avoids an N+1 per list page) stays byte-identical.
    items: list[BatchJobItemResponse] = []

    model_config = {"from_attributes": True}


class BatchJobListResponse(BaseModel):
    jobs: list[BatchJobResponse]


# ---------------------------------------------------------------------------
# Submission validation (application-level — named error codes, not generic 422)
# ---------------------------------------------------------------------------


def _validate_line_items(line_items: list[LineItemRequest], *, max_items: int) -> None:
    """Reject per TASK.md §1: empty / over-cap / missing model-or-messages /
    duplicate custom_id. Raises before any row is created — atomic submission."""
    if len(line_items) == 0:
        raise BATCH_ITEMS_EMPTY.exc()
    if len(line_items) > max_items:
        raise BATCH_ITEMS_TOO_MANY.exc()

    seen_custom_ids: set[str] = set()
    for item in line_items:
        if item.custom_id in seen_custom_ids:
            raise BATCH_ITEM_INVALID.exc()
        seen_custom_ids.add(item.custom_id)

        if not item.body.get("model"):
            raise BATCH_ITEM_INVALID.exc()
        if not item.body.get("messages"):
            raise BATCH_ITEM_INVALID.exc()


# ---------------------------------------------------------------------------
# Background processing task
# ---------------------------------------------------------------------------


async def _process_batch_job(
    *,
    job_id: uuid.UUID,
    sessionmaker: Any,
    batch_processor: Any,
    timeout_seconds: float,
    tasks_set: set[asyncio.Task[None]],
) -> None:
    """In-process background task driving one batch job's status machine.

    Design-for-failure guarantees:
    - ENTIRE body is wrapped in try/except — a raise NEVER escapes (no loop crash).
    - EACH status write uses a FRESH sessionmaker() session + commit.
    - set_failed() cascades every still-pending item to errored (status guard on the job
      row itself makes the write idempotent against a re-driven/duplicated worker).
    - Tasks are tracked in tasks_set; removed from it on completion.
    - asyncio.wait_for enforces the per-job timeout (0 = unlimited).
    """
    current_task = asyncio.current_task()
    try:
        # ── set status=in_progress ──────────────────────────────────────
        async with sessionmaker() as session:
            repo = BatchJobRepository(session)
            await repo.set_in_progress(job_id=job_id)
            await session.commit()

        # ── run the processor ───────────────────────────────────────────
        if batch_processor is None:
            # Honest degradation: no BatchProcessor configured yet (this task's only
            # reachable terminal — openai-batch-adapter/anthropic-batch-adapter plug
            # in a real one later).
            async with sessionmaker() as session:
                repo = BatchJobRepository(session)
                await repo.set_failed(job_id=job_id, error="no_batch_processor_configured")
                await session.commit()
            return

        try:
            if timeout_seconds > 0:
                await asyncio.wait_for(
                    batch_processor.process(job_id),
                    timeout=timeout_seconds,
                )
            else:
                await batch_processor.process(job_id)
        except TimeoutError:
            async with sessionmaker() as session:
                repo = BatchJobRepository(session)
                await repo.set_failed(job_id=job_id, error="timeout")
                await session.commit()
            return
        except Exception as exc:
            async with sessionmaker() as session:
                repo = BatchJobRepository(session)
                await repo.set_failed(job_id=job_id, error=str(exc))
                await session.commit()
            return

        # A configured BatchProcessor is responsible for driving the job to its own
        # terminal status (openai-batch-adapter / anthropic-batch-adapter, downstream
        # tasks) — nothing further to do here on a clean return.

    except Exception:
        # Last-resort: catch any unexpected exception so it never escapes.
        _log.exception("batch_job_task_unhandled_error", extra={"job_id": str(job_id)})
        try:
            async with sessionmaker() as session:
                repo = BatchJobRepository(session)
                await repo.set_failed(job_id=job_id, error="internal_error")
                await session.commit()
        except Exception:
            _log.exception("batch_job_task_set_failed_failed", extra={"job_id": str(job_id)})
    finally:
        if current_task is not None:
            tasks_set.discard(current_task)


async def dispatch_batch_job(
    *,
    job_id: uuid.UUID,
    app_state: Any,
    settings: Settings,
    batch_processor: object | None,
) -> None:
    """Spawn the background processing task for job_id — durable queue if enabled,
    else inline asyncio.create_task. The ONE dispatch path shared by create_batch_job
    (below) and BatchDiversionAdapter (automatic diversion from /v1/chat/completions,
    batch-auto-grouping TASK.md §5, proxy/infrastructure/batch_diversion.py) —
    extracted so there is never a second, drifting copy of the durable-vs-inline
    branching.

    app_state duck-types FastAPI's app.state: needs .batch_jobs_tasks, .sessionmaker,
    and (only when settings.batch_durable_queue_enabled) .batch_job_queue.
    """
    tasks_set: set[asyncio.Task[None]] = app_state.batch_jobs_tasks

    if settings.batch_durable_queue_enabled:
        # Durable path: enqueue to Redis so the BatchJobWorker picks it up. Fail-open:
        # if the Redis enqueue raises (Redis down / network error) OR app_state.batch_job_queue
        # was never wired (settings/lifespan drift), fall back to the inline
        # asyncio.create_task so no job is dropped. The attribute access is INSIDE the try —
        # a missing attribute must take the same fallback path as an enqueue failure, not
        # escape as an uncaught 500 that leaves the row stuck in "validating" forever.
        try:
            _queue: RedisBatchJobQueue = app_state.batch_job_queue
            await _queue.enqueue(job_id)
            return
        except Exception:
            _log.warning(
                "batch enqueue failed; falling back to in-process task for job %s",
                job_id,
            )

    # Default inline path.
    task: asyncio.Task[None] = asyncio.create_task(
        _process_batch_job(
            job_id=job_id,
            sessionmaker=app_state.sessionmaker,
            batch_processor=batch_processor,
            timeout_seconds=settings.batch_job_timeout_seconds,
            tasks_set=tasks_set,
        )
    )
    tasks_set.add(task)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@batch_router.post(
    "/v1/batches",
    response_model=BatchJobResponse,
    status_code=200,
)
async def create_batch_job(
    body: CreateBatchJobRequest,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[BatchJobRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchJobResponse:
    """Submit a batch of chat-completion line items for the authenticated tenant.

    Validates the submission (422 with a named code on any failure, zero rows created).
    Creates the job row (status=validating) + one item row per line item (status=pending)
    in one transaction, then spawns a tracked background task (or durable-queue enqueue).
    Returns the job envelope immediately (non-blocking). 401 on auth failure.
    """
    settings: Settings = _get_settings(request)

    # files-uploads-api PLAN.md §3 (ADDITIVE): exactly ONE of {line_items, input_file_id}.
    # BOTH present or NEITHER present -> 422 ERR_BATCH_INPUT_AMBIGUOUS.
    has_line_items = body.line_items is not None
    has_input_file = body.input_file_id is not None
    if has_line_items == has_input_file:
        raise BATCH_INPUT_AMBIGUOUS.exc()

    resolved_input_file_id: uuid.UUID | None = None
    input_file_wire: str | None = None
    line_items: list[dict[str, Any]] = []

    if has_input_file:
        # Validate the file reference. ONE uniform code (no enumeration oracle) for
        # malformed / absent / cross-tenant / wrong-purpose — a cross-tenant file is
        # indistinguishable from an absent one because get_active is tenant-scoped.
        assert body.input_file_id is not None
        file_uuid = parse_wire_id(body.input_file_id)
        if file_uuid is None:
            raise BATCH_INPUT_FILE_INVALID.exc()
        file_row = await FileRepository(session).get_active(
            tenant_id=authz.tenant_id, file_id=file_uuid
        )
        if file_row is None or file_row.purpose != "batch":
            raise BATCH_INPUT_FILE_INVALID.exc()
        resolved_input_file_id = file_uuid
        input_file_wire = to_wire_id(file_uuid)
        # JSONL -> line-item EXPANSION + provider dispatch stay in v58's scope; this task
        # records the reference and creates the job (line_items empty, item_count=0).
    else:
        assert body.line_items is not None
        _validate_line_items(body.line_items, max_items=settings.batch_max_items_per_job)
        line_items = [{"custom_id": item.custom_id, "body": item.body} for item in body.line_items]

    row = await repo.create(
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        line_items=line_items,
        input_file_id=resolved_input_file_id,
    )
    await session.commit()

    await dispatch_batch_job(
        job_id=row.id,
        app_state=request.app.state,
        settings=settings,
        batch_processor=getattr(request.app.state, "batch_processor", None),
    )

    # Every item was just created status=pending, in this same transaction — the
    # breakdown is deterministic without a re-query.
    status_counts = {**_ALL_PENDING_COUNTS, "pending": row.item_count}

    return BatchJobResponse(
        id=row.id,
        status=row.status,
        item_count=row.item_count,
        status_counts=status_counts,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        input_file_id=input_file_wire,
    )


@batch_router.get(
    "/v1/batches/{job_id}",
    response_model=BatchJobResponse,
)
async def get_batch_job(
    job_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[BatchJobRepository, Depends(_get_repo)],
) -> BatchJobResponse:
    """Poll a batch job by id.

    Returns 200 with the current job state + a per-status item count breakdown +
    per-item results (batch-auto-grouping TASK.md §3, M7 — additive).
    Returns 404 for unknown or cross-tenant jobs — never reveals another tenant's job.
    """
    row = await repo.get(tenant_id=authz.tenant_id, job_id=job_id)
    if row is None:
        raise BATCH_JOB_NOT_FOUND.exc()

    status_counts = await repo.status_counts(job_id=row.id)
    item_rows = await repo.list_items(tenant_id=authz.tenant_id, job_id=row.id)

    return BatchJobResponse(
        id=row.id,
        status=row.status,
        item_count=row.item_count,
        status_counts=status_counts,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
        input_file_id=to_wire_id(row.input_file_id) if row.input_file_id else None,
        items=[
            BatchJobItemResponse(
                custom_id=item.custom_id,
                status=item.status,
                result_body=item.result_body,
            )
            for item in item_rows
        ],
    )


@batch_router.get(
    "/v1/batches",
    response_model=BatchJobListResponse,
)
async def list_batch_jobs(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[BatchJobRepository, Depends(_get_repo)],
    limit: int = 50,
    offset: int = 0,
) -> BatchJobListResponse:
    """List batch jobs for the authenticated tenant.

    Returns jobs newest first (ix_batch_jobs_tenant_created).
    limit: default 50, capped at 200. offset: must be >= 0.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)

    rows = await repo.list_for_tenant(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
    )

    jobs = []
    for row in rows:
        status_counts = await repo.status_counts(job_id=row.id)
        jobs.append(
            BatchJobResponse(
                id=row.id,
                status=row.status,
                item_count=row.item_count,
                status_counts=status_counts,
                error=row.error,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
        )

    return BatchJobListResponse(jobs=jobs)
