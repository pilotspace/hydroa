"""FastAPI router for the finetune-broker domain (/v1/fine_tuning/jobs).

Contract FROZEN @ v1 (finetune-broker PLAN.md §3).

Auth: every endpoint requires ``Authorization: Bearer sk-...`` via
``SqlAlchemyKeyAuthenticator`` -> ``AuthzResult{tenant_id, key_id}``. Missing/invalid
key -> 401 (mirrors batches/files _authenticate verbatim).

Tenant isolation: every service call passes tenant_id from the authenticated key.
Cross-tenant / unknown job id -> 404 ERR_FINETUNE_JOB_NOT_FOUND, byte-identical
for get/cancel/events (T3 — no enumeration oracle).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_KEY_EXPIRED, AUTH_KEY_INVALID, FINETUNE_JOB_NOT_FOUND
from gateway.files.infrastructure.repository import FileRepository
from gateway.files.wire_id import to_wire_id
from gateway.finetune.application.use_cases import FinetuneBrokerService
from gateway.finetune.infrastructure.orm import FinetuneJobEventRow, FinetuneJobRow
from gateway.finetune.infrastructure.repository import FinetuneJobRepository
from gateway.finetune.wire_id import parse_job_wire_id, to_event_wire_id, to_job_wire_id
from gateway.keys.application.use_cases import AuthzUseCase
from gateway.keys.domain.entities import AuthzResult
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.proxy.infrastructure.key_authenticator import SqlAlchemyKeyAuthenticator

finetune_router = APIRouter(tags=["fine_tuning"])

# Singleton stateless hasher — safe to share (batches/files convention).
_hasher = Sha256SecretHasher()


# ---------------------------------------------------------------------------
# Auth dependency (copied from batches/files routers — this codebase's convention)
# ---------------------------------------------------------------------------


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


def _get_service(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FinetuneBrokerService:
    object_store = getattr(request.app.state, "object_store", None)
    return FinetuneBrokerService(
        repo=FinetuneJobRepository(session),
        files_repo=FileRepository(session, object_store=object_store),
        credential_resolver=request.app.state.tenant_credential_resolver,
        provider_port=getattr(request.app.state, "finetune_provider", None),
        completion_listener=getattr(request.app.state, "finetune_completion_listener", None),
    )


def _not_found() -> Exception:
    return FINETUNE_JOB_NOT_FOUND.exc()


def _resolve_job_id(job_id: str) -> uuid.UUID:
    """Parse a ``ftjob-<hex>`` path segment to a UUID, or raise 404 (T3: no oracle —
    malformed folds into the SAME uniform 404 as unknown/cross-tenant)."""
    parsed = parse_job_wire_id(job_id)
    if parsed is None:
        raise _not_found()
    return parsed


# ---------------------------------------------------------------------------
# Request / Response wire shapes (OpenAI fine_tuning.job / .event objects)
# ---------------------------------------------------------------------------


class CreateFineTuningJobRequest(BaseModel):
    model: str
    training_file: str
    validation_file: str | None = None
    suffix: str | None = None
    hyperparameters: dict[str, Any] | None = None


def _job_object(row: FinetuneJobRow) -> dict[str, Any]:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    finished_unix: int | None = None
    if row.finished_at is not None:
        finished = row.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        finished_unix = int(finished.timestamp())

    error_obj: dict[str, str] | None = None
    if row.status == "failed" and row.error:
        # v1's only reachable create-time terminal failure (M1 honest degradation).
        error_obj = {"code": "finetune_provider_unreachable", "message": row.error}

    return {
        "id": to_job_wire_id(row.id),
        "object": "fine_tuning.job",
        "model": row.model,
        "status": row.status,
        "training_file": to_wire_id(row.training_file_id),
        "validation_file": to_wire_id(row.validation_file_id) if row.validation_file_id else None,
        "fine_tuned_model": row.fine_tuned_model,
        "error": error_obj,
        "created_at": int(created.timestamp()),
        "finished_at": finished_unix,
        "hyperparameters": row.hyperparameters,
    }


def _event_object(row: FinetuneJobEventRow) -> dict[str, Any]:
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return {
        "id": to_event_wire_id(row.id),
        "object": "fine_tuning.job.event",
        "level": row.level,
        "message": row.message,
        "created_at": int(created.timestamp()),
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@finetune_router.post("/v1/fine_tuning/jobs", status_code=200)
async def create_finetuning_job(
    body: CreateFineTuningJobRequest,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    service: Annotated[FinetuneBrokerService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Broker a new fine-tuning job to the tenant's own BYOK provider credential.

    422 on an unsupported model or an invalid training_file/validation_file
    reference; 402 fail-closed when the tenant has no enabled provider credential
    (platform fallback never consulted). 200 even on a submit-time provider failure
    (M1 honest degradation — the job persists with status="failed").
    """
    row = await service.create_job(
        tenant_id=authz.tenant_id,
        key_id=authz.key_id,
        model=body.model,
        training_file=body.training_file,
        validation_file=body.validation_file,
        suffix=body.suffix,
        hyperparameters=body.hyperparameters,
    )
    await session.commit()
    return _job_object(row)


@finetune_router.get("/v1/fine_tuning/jobs", status_code=200)
async def list_finetuning_jobs(
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    service: Annotated[FinetuneBrokerService, Depends(_get_service)],
    limit: int = 20,
    after: str | None = None,
) -> dict[str, Any]:
    """List fine-tuning jobs for the authenticated tenant, newest first."""
    effective_limit = min(max(limit, 1), 100)
    rows, has_more = await service.list_jobs(
        tenant_id=authz.tenant_id, limit=effective_limit, after=after
    )
    return {"object": "list", "data": [_job_object(r) for r in rows], "has_more": has_more}


@finetune_router.get("/v1/fine_tuning/jobs/{job_id}", status_code=200)
async def get_finetuning_job(
    job_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    service: Annotated[FinetuneBrokerService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Poll a fine-tuning job (refresh-on-read, M7); 404 for unknown/cross-tenant."""
    resolved = _resolve_job_id(job_id)
    row = await service.get_job(tenant_id=authz.tenant_id, job_id=resolved)
    if row is None:
        raise _not_found()
    await session.commit()
    return _job_object(row)


@finetune_router.post("/v1/fine_tuning/jobs/{job_id}/cancel", status_code=200)
async def cancel_finetuning_job(
    job_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    service: Annotated[FinetuneBrokerService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """Cancel a non-terminal job; 404 unknown/cross-tenant, 409 already-terminal,
    502 provider-unreachable (status unchanged, cancel stays retryable)."""
    resolved = _resolve_job_id(job_id)
    row = await service.cancel_job(tenant_id=authz.tenant_id, job_id=resolved)
    if row is None:
        raise _not_found()
    await session.commit()
    return _job_object(row)


@finetune_router.get("/v1/fine_tuning/jobs/{job_id}/events", status_code=200)
async def list_finetuning_job_events(
    job_id: str,
    authz: Annotated[AuthzResult, Depends(_authenticate)],
    service: Annotated[FinetuneBrokerService, Depends(_get_service)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """List broker-recorded lifecycle events for a job; 404 unknown/cross-tenant."""
    resolved = _resolve_job_id(job_id)
    events = await service.list_events(tenant_id=authz.tenant_id, job_id=resolved)
    if events is None:
        raise _not_found()
    await session.commit()
    return {"object": "list", "data": [_event_object(e) for e in events]}
