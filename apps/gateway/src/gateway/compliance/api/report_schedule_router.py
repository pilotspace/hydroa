"""Compliance report-schedule + generated-reports endpoints (compliance-report-center
TASK.md §3 M15-M23 — FROZEN @ v1).

A NEW sibling router file — the FROZEN art12-bundle route (`compliance/api/router.py`)
is NEVER edited by this task; mounted alongside it in main.py.

  GET    /admin/compliance/report-schedule       AUDIT_READ  (any read-capable role)
  PUT    /admin/compliance/report-schedule       SECURITY_CONFIG (OWNER only, mirrors
                                                  PUT /admin/retention-policy verbatim)
  DELETE /admin/compliance/report-schedule       SECURITY_CONFIG (OWNER only)
  GET    /admin/compliance/reports               AUDIT_READ  (keyset list, generated_at DESC)
  GET    /admin/compliance/reports/{report_id}   AUDIT_READ  (tenant-scoped row lookup
                                                  BEFORE any object-store call, mirrors
                                                  artifacts/api/router.py:download_artifact
                                                  exactly — 404 for cross-tenant, NEVER 403;
                                                  503 ERR_OBJECT_STORE_UNAVAILABLE)

All three GET/PUT/DELETE surfaces operate ONLY on identity.tenant_id — no tenant_id
path/query param exists anywhere on this router, structurally impossible to target
another tenant (mirrors tenants/api/retention_policy_router.py's own documented
invariant verbatim).
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.compliance.application.report_schedule_generator import compute_next_run_at
from gateway.compliance.infrastructure.repository import (
    ComplianceReportRunRepository,
    ReportScheduleRepository,
)
from gateway.core.db import get_session
from gateway.core.error_catalog import OBJECT_STORE_UNAVAILABLE, PAYLOAD_INVALID
from gateway.core.errors import ProblemError
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.objectstore.port import ObjectStore
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

report_schedule_router = APIRouter(prefix="/admin/compliance", tags=["compliance"])

_DEFAULT_DAY_OF_MONTH = 1
_MIN_DAY_OF_MONTH = 1
_MAX_DAY_OF_MONTH = 28
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 100


# ---------------------------------------------------------------------------
# 404 helper — mirrors artifacts/api/router.py:_not_found's own local-construction
# idiom (a task-specific ProblemError, not a shared catalog entry).
# ---------------------------------------------------------------------------


def _report_not_found() -> ProblemError:
    return ProblemError(status=404, code="ERR_REPORT_NOT_FOUND", title="Report not found")


def _safe_filename(name: str) -> str:
    """Strip characters unsafe for Content-Disposition filename value (mirrors
    artifacts/api/router.py:_safe_filename verbatim)."""
    return re.sub(r'["\r\n\x00-\x1f\x7f]', "", name)


def _get_object_store(request: Request) -> ObjectStore | None:
    return getattr(request.app.state, "object_store", None)


# ---------------------------------------------------------------------------
# Response / request shapes
# ---------------------------------------------------------------------------


class ReportScheduleResponse(BaseModel):
    enabled: bool
    cadence: str
    day_of_month: int
    window_policy: str
    created_by: str | None
    created_at: str | None
    updated_at: str | None
    last_run_at: str | None
    last_run_status: str | None
    next_run_at: str | None


class ReportScheduleBody(BaseModel):
    """PUT body — partial-merge convention mirrors RetentionPolicyBody: absent
    day_of_month defaults to 1 (not "preserve prior value") per the frozen §3 shape."""

    enabled: bool
    day_of_month: int | None = None


class GeneratedReportItem(BaseModel):
    id: str
    period_start: str
    period_end: str
    generated_at: str
    size_bytes: int
    format_version: str


class GeneratedReportsListResponse(BaseModel):
    items: list[GeneratedReportItem]
    next_cursor: str | None
    has_more: bool


def _default_schedule_response() -> ReportScheduleResponse:
    """No row yet for this tenant -> 200 with enabled=false, every other field
    null/default — absence of a schedule is a normal state, never a 404."""
    return ReportScheduleResponse(
        enabled=False,
        cadence="monthly",
        day_of_month=_DEFAULT_DAY_OF_MONTH,
        window_policy="previous_calendar_month",
        created_by=None,
        created_at=None,
        updated_at=None,
        last_run_at=None,
        last_run_status=None,
        next_run_at=None,
    )


def _row_to_response(row) -> ReportScheduleResponse:  # type: ignore[no-untyped-def]
    return ReportScheduleResponse(
        enabled=row.enabled,
        cadence=row.cadence,
        day_of_month=row.day_of_month,
        window_policy=row.window_policy,
        created_by=str(row.created_by) if row.created_by is not None else None,
        created_at=row.created_at.isoformat() if row.created_at is not None else None,
        updated_at=row.updated_at.isoformat() if row.updated_at is not None else None,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at is not None else None,
        last_run_status=row.last_run_status,
        next_run_at=row.next_run_at.isoformat() if row.next_run_at is not None else None,
    )


# ---------------------------------------------------------------------------
# GET/PUT/DELETE /admin/compliance/report-schedule
# ---------------------------------------------------------------------------


@report_schedule_router.get("/report-schedule", response_model=ReportScheduleResponse)
async def get_report_schedule(
    identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReportScheduleResponse:
    repo = ReportScheduleRepository(session)
    row = await repo.get(identity.tenant_id)
    if row is None:
        return _default_schedule_response()
    return _row_to_response(row)


@report_schedule_router.put("/report-schedule", response_model=ReportScheduleResponse)
async def put_report_schedule(
    body: ReportScheduleBody,
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> ReportScheduleResponse:
    """OWNER only (M19). day_of_month outside 1-28 -> 422 ERR_PAYLOAD_INVALID, stored
    row UNCHANGED (R9). enabled=true (re)computes next_run_at fresh from day_of_month;
    enabled=false clears it (no further ticks)."""
    day_of_month = body.day_of_month if body.day_of_month is not None else _DEFAULT_DAY_OF_MONTH
    if day_of_month < _MIN_DAY_OF_MONTH or day_of_month > _MAX_DAY_OF_MONTH:
        raise PAYLOAD_INVALID.exc()

    now = datetime.now(UTC)
    next_run_at = compute_next_run_at(day_of_month, now=now) if body.enabled else None

    repo = ReportScheduleRepository(session)
    row = await repo.upsert(
        tenant_id=identity.tenant_id,
        enabled=body.enabled,
        day_of_month=day_of_month,
        created_by=identity.user_id,
        next_run_at=next_run_at,
        now=now.replace(tzinfo=None),
    )
    await session.commit()

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="compliance.schedule_updated",
                target_type="tenant_report_schedule",
                target_id=str(identity.tenant_id),
                result="success",
                metadata={"enabled": body.enabled, "day_of_month": day_of_month},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return _row_to_response(row)


@report_schedule_router.delete("/report-schedule", status_code=204)
async def delete_report_schedule(
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> None:
    """OWNER only (M19). Hard-deletes the schedule row (re-enable is a fresh PUT)."""
    repo = ReportScheduleRepository(session)
    existed = await repo.delete(identity.tenant_id)
    await session.commit()

    if existed:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                request.app.state.sessionmaker,
                AuditEvent(
                    id=uuid.uuid4(),
                    tenant_id=identity.tenant_id,
                    actor_user_id=identity.user_id,
                    actor_email=identity.email,
                    action="compliance.schedule_deleted",
                    target_type="tenant_report_schedule",
                    target_id=str(identity.tenant_id),
                    result="success",
                    metadata={},
                    created_at=datetime.now(UTC),
                ),
            )
        )


# ---------------------------------------------------------------------------
# GET /admin/compliance/reports (+/{report_id})
# ---------------------------------------------------------------------------


def _encode_cursor(generated_at: datetime, report_id: uuid.UUID) -> str:
    raw = json.dumps({"generated_at": generated_at.isoformat(), "id": str(report_id)}).encode(
        "utf-8"
    )
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        return datetime.fromisoformat(decoded["generated_at"]), uuid.UUID(decoded["id"])
    except Exception:
        raise PAYLOAD_INVALID.exc() from None


@report_schedule_router.get("/reports", response_model=GeneratedReportsListResponse)
async def list_generated_reports(
    identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[int | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> GeneratedReportsListResponse:
    """Keyset-paginated list of this tenant's own generated runs, generated_at DESC
    (M18) — tenant-scoped in the repository's own WHERE clause."""
    effective_limit = min(max(limit or _DEFAULT_LIST_LIMIT, 1), _MAX_LIST_LIMIT)
    decoded_cursor = _decode_cursor(cursor) if cursor is not None else None

    repo = ComplianceReportRunRepository(session)
    rows = await repo.list_for_tenant_keyset(
        identity.tenant_id, limit=effective_limit + 1, cursor=decoded_cursor
    )
    has_more = len(rows) > effective_limit
    page = rows[:effective_limit]

    next_cursor = _encode_cursor(page[-1].generated_at, page[-1].id) if has_more and page else None

    return GeneratedReportsListResponse(
        items=[
            GeneratedReportItem(
                id=str(r.id),
                period_start=r.period_start.isoformat(),
                period_end=r.period_end.isoformat(),
                generated_at=r.generated_at.isoformat(),
                size_bytes=r.size_bytes,
                format_version=r.format_version,
            )
            for r in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@report_schedule_router.get("/reports/{report_id}")
async def download_generated_report(
    report_id: uuid.UUID,
    identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> Response:
    """Tenant-scoped row lookup BEFORE any object-store call (M18) — mirrors
    download_artifact verbatim. 404 for unknown/cross-tenant/vanished-object (NEVER
    403 — a 403 would leak existence, R11). 503 ERR_OBJECT_STORE_UNAVAILABLE when the
    store itself is unreachable/unconfigured (R13)."""
    repo = ComplianceReportRunRepository(session)
    row = await repo.get_active(tenant_id=identity.tenant_id, report_id=report_id)
    if row is None:
        raise _report_not_found()

    store = _get_object_store(request)
    if store is None:
        raise OBJECT_STORE_UNAVAILABLE.exc()
    try:
        content = await store.get(row.object_key)
    except ObjectNotFoundError:
        raise _report_not_found() from None
    except ObjectStoreUnavailableError:
        raise OBJECT_STORE_UNAVAILABLE.exc() from None

    filename = _safe_filename(
        f"art12-bundle-{row.tenant_id}-{row.period_start.date().isoformat()}"
        f"-{row.period_end.date().isoformat()}.json"
    )
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
