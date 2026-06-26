"""FastAPI router for the video generation jobs domain (/v1/video/generations).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` → ``AuthzResult{tenant_id, key_id}``.
Missing / invalid key → 401 (AUTH_KEY_INVALID).
Expired key → 401 (AUTH_KEY_EXPIRED).

Tenant isolation: every repository call passes ``tenant_id`` from the
authenticated key. Cross-tenant / unknown → 404 (ERR_VIDEO_JOB_NOT_FOUND).

Create: POST /v1/video/generations
  - Validates model (non-empty) and prompt (non-empty); 422 on failure.
  - Inserts a job row (status=queued) and spawns a background asyncio.Task.
  - Task tracked on app.state.video_jobs_tasks (set); removed on completion.
  - Returns 200 with the queued job shape immediately.

Poll:   GET /v1/video/generations/{id}
  - Returns 200 job shape; 404 for unknown or cross-tenant.

List:   GET /v1/video/generations?limit&offset
  - Returns tenant-scoped list, newest first; no video bytes in the list.

Processing (in-process asyncio.Task):
  set status=running →
  provider = app.state.video_generator  # VideoGenerator Protocol
  if None  → failed, error="no_video_provider_configured"
  else     → asyncio.wait_for(provider.generate(...), timeout)
             success  → create v45 artifact → succeeded
             TimeoutError → failed, error="timeout"
             Exception    → failed, error=str(exc)
  EACH status write uses a FRESH app.state.sessionmaker() session.
  The ENTIRE task body is wrapped; a raise never escapes (no loop crash).
  Terminal-status transitions are idempotent.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.artifacts.infrastructure.repository import ArtifactRepository
from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_KEY_EXPIRED,
    AUTH_KEY_INVALID,
    VIDEO_JOB_NOT_FOUND,
)
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator
from gateway.video.infrastructure.repository import VideoJobRepository

video_router = APIRouter(tags=["video"])

_log = logging.getLogger(__name__)

# Singleton stateless hasher — safe to share
_hasher = Sha256SecretHasher()


# ---------------------------------------------------------------------------
# Auth dependency (copied from artifacts router)
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
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VideoJobRepository:
    return VideoJobRepository(session)


def _get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateVideoJobRequest(BaseModel):
    model: str
    prompt: str
    params: dict[str, object] | None = None

    @field_validator("model")
    @classmethod
    def _validate_model(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("model must be a non-empty string")
        return v

    @field_validator("prompt")
    @classmethod
    def _validate_prompt(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("prompt must be a non-empty string")
        return v


class VideoJobResponse(BaseModel):
    id: uuid.UUID
    status: str
    model: str
    prompt: str
    result_artifact_id: uuid.UUID | None
    error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VideoJobListResponse(BaseModel):
    jobs: list[VideoJobResponse]


# ---------------------------------------------------------------------------
# Background processing task
# ---------------------------------------------------------------------------


async def _process_video_job(
    *,
    job_id: uuid.UUID,
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    model: str,
    prompt: str,
    params: dict[str, object] | None,
    sessionmaker: Any,
    video_generator: Any,
    timeout_seconds: float,
    tasks_set: set[asyncio.Task[None]],
) -> None:
    """In-process background task for video generation.

    Design-for-failure guarantees:
    - ENTIRE body is wrapped in try/except — a raise NEVER escapes (no loop crash).
    - EACH status write uses a FRESH sessionmaker() session + commit.
    - Terminal-status writes are idempotent (set_running only runs for non-terminal).
    - Tasks are tracked in tasks_set; removed from it on completion.
    - asyncio.wait_for enforces the per-job timeout (0 = unlimited).
    """
    current_task = asyncio.current_task()
    try:
        # ── set status=running ──────────────────────────────────────────
        async with sessionmaker() as session:
            repo = VideoJobRepository(session)
            await repo.set_running(job_id=job_id)
            await session.commit()

        # ── run the provider ────────────────────────────────────────────
        provider = video_generator
        if provider is None:
            # Honest degradation: no provider configured
            async with sessionmaker() as session:
                repo = VideoJobRepository(session)
                await repo.set_failed(job_id=job_id, error="no_video_provider_configured")
                await session.commit()
            return

        # Call provider with optional timeout
        try:
            if timeout_seconds > 0:
                result: tuple[bytes, str] = await asyncio.wait_for(
                    provider.generate(prompt, model, params),
                    timeout=timeout_seconds,
                )
            else:
                result = await provider.generate(prompt, model, params)
        except TimeoutError:
            async with sessionmaker() as session:
                repo = VideoJobRepository(session)
                await repo.set_failed(job_id=job_id, error="timeout")
                await session.commit()
            return
        except Exception as exc:
            async with sessionmaker() as session:
                repo = VideoJobRepository(session)
                await repo.set_failed(job_id=job_id, error=str(exc))
                await session.commit()
            return

        video_bytes, content_type = result

        # ── store as v45 artifact ────────────────────────────────────────
        async with sessionmaker() as session:
            artifact_repo = ArtifactRepository(session)
            artifact = await artifact_repo.create(
                tenant_id=tenant_id,
                key_id=key_id,
                name=f"video-{job_id}",
                content_type=content_type,
                size_bytes=len(video_bytes),
                content=video_bytes,
            )
            artifact_id = artifact.id
            await session.commit()

        # ── set status=succeeded ─────────────────────────────────────────
        async with sessionmaker() as session:
            repo = VideoJobRepository(session)
            await repo.set_succeeded(job_id=job_id, artifact_id=artifact_id)
            await session.commit()

    except Exception:
        # Last-resort: catch any unexpected exception so it never escapes
        _log.exception("video_job_task_unhandled_error", extra={"job_id": str(job_id)})
        try:
            async with sessionmaker() as session:
                repo = VideoJobRepository(session)
                await repo.set_failed(job_id=job_id, error="internal_error")
                await session.commit()
        except Exception:
            _log.exception("video_job_task_set_failed_failed", extra={"job_id": str(job_id)})
    finally:
        # Remove self from the tracking set so the lifespan doesn't try to cancel
        # a task that has already completed.
        if current_task is not None:
            tasks_set.discard(current_task)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@video_router.post(
    "/v1/video/generations",
    response_model=VideoJobResponse,
    status_code=200,
)
async def create_video_job(
    body: CreateVideoJobRequest,
    request: Request,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VideoJobRepository, Depends(_get_repo)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VideoJobResponse:
    """Submit a video generation job for the authenticated tenant.

    Creates the job row (status=queued) then spawns a tracked background task.
    Returns the queued job shape immediately (non-blocking).
    422 when model or prompt is missing/empty; 401 on auth failure.
    """
    settings: Settings = _get_settings(request)

    row = await repo.create(
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=body.model,
        prompt=body.prompt,
        params=body.params,
    )
    await session.commit()

    # Spawn the background processing task
    video_generator = getattr(request.app.state, "video_generator", None)
    tasks_set: set[asyncio.Task[None]] = request.app.state.video_jobs_tasks

    task: asyncio.Task[None] = asyncio.create_task(
        _process_video_job(
            job_id=row.id,
            tenant_id=authz.tenant_id,
            key_id=authz.key_id,
            model=row.model,
            prompt=row.prompt,
            params=row.params,
            sessionmaker=request.app.state.sessionmaker,
            video_generator=video_generator,
            timeout_seconds=settings.video_job_timeout_seconds,
            tasks_set=tasks_set,
        )
    )
    tasks_set.add(task)

    return VideoJobResponse(
        id=row.id,
        status=row.status,
        model=row.model,
        prompt=row.prompt,
        result_artifact_id=row.result_artifact_id,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@video_router.get(
    "/v1/video/generations/{job_id}",
    response_model=VideoJobResponse,
)
async def get_video_job(
    job_id: uuid.UUID,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VideoJobRepository, Depends(_get_repo)],
) -> VideoJobResponse:
    """Poll a video generation job by id.

    Returns 200 with the current job state.
    Returns 404 for unknown or cross-tenant jobs — never reveals another tenant's job.
    """
    row = await repo.get(tenant_id=authz.tenant_id, job_id=job_id)
    if row is None:
        raise VIDEO_JOB_NOT_FOUND.exc()

    return VideoJobResponse(
        id=row.id,
        status=row.status,
        model=row.model,
        prompt=row.prompt,
        result_artifact_id=row.result_artifact_id,
        error=row.error,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@video_router.get(
    "/v1/video/generations",
    response_model=VideoJobListResponse,
)
async def list_video_jobs(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    repo: Annotated[VideoJobRepository, Depends(_get_repo)],
    limit: int = 50,
    offset: int = 0,
) -> VideoJobListResponse:
    """List video generation jobs for the authenticated tenant.

    Returns jobs newest first (ix_video_jobs_tenant_created).
    No video bytes are returned — use GET /v1/artifacts/{id} to download.
    limit: default 50, capped at 200. offset: must be >= 0.
    """
    effective_limit = min(max(limit, 1), 200)
    effective_offset = max(offset, 0)

    rows = await repo.list_for_tenant(
        tenant_id=authz.tenant_id,
        limit=effective_limit,
        offset=effective_offset,
    )

    return VideoJobListResponse(
        jobs=[
            VideoJobResponse(
                id=row.id,
                status=row.status,
                model=row.model,
                prompt=row.prompt,
                result_artifact_id=row.result_artifact_id,
                error=row.error,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]
    )
