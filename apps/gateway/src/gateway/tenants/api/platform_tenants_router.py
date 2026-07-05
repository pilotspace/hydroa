"""FastAPI router for the superadmin cross-tenant directory (platform-tenant-directory
TASK.md §3 — FROZEN @ v1).

Endpoints:
  GET /admin/platform/tenants               — list/search every tenant (require_superadmin)
  GET /admin/platform/tenants/{tenant_id}    — view any one tenant (authorize_tenant_scope)

Security (server-side, authoritative):
  - Bulk list has no single target_tenant_id, so it is gated by require_superadmin — a
    role-only check, deliberately NOT authorize_tenant_scope and NOT a Permission (§1
    Framings weighed).
  - Get-one DOES have a natural target_tenant_id, so it is gated by
    authorize_tenant_scope(identity, tenant_id) — this route wires that predicate's first
    real caller; it is SUPERADMIN-only in practice here since every other role's own
    tenant_id resolves through the ordinary tenant-scoped surfaces instead.
  - Audit: both routes call emit_platform_audit() on their success path — the bulk list as a
    system-level event (target_tenant_id=None, action="platform.tenant.list") and the get-one
    read scoped to the target tenant (action="platform.tenant.view") — added by
    admin-console-audit TASK.md §3 (FROZEN @ v1).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import TENANT_NOT_FOUND
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.repository import get_tenant_by_id, list_tenants

platform_tenants_router = APIRouter(prefix="/admin/platform/tenants", tags=["platform-admin"])

_MAX_LIMIT = 200
_DEFAULT_LIMIT = 50


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TenantSummaryResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    created_at: datetime


class TenantDirectoryListResponse(BaseModel):
    tenants: list[TenantSummaryResponse]
    total: int


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants
# ---------------------------------------------------------------------------


@platform_tenants_router.get("", response_model=TenantDirectoryListResponse)
async def list_platform_tenants(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    q: str | None = Query(default=None),
    limit: int = Query(default=_DEFAULT_LIMIT),
    offset: int = Query(default=0),
) -> TenantDirectoryListResponse:
    """GET /admin/platform/tenants — list/search every tenant. SUPERADMIN only.

    `limit` above _MAX_LIMIT clamps rather than 400s (R4) — an overly-generous limit is
    not a client error.
    """
    clamped_limit = min(limit, _MAX_LIMIT)
    rows, total = await list_tenants(session, q=q, limit=clamped_limit, offset=offset)
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.tenant.list",
        target_type="tenant",
        target_id=None,
        metadata={},
    )
    return TenantDirectoryListResponse(
        tenants=[
            TenantSummaryResponse(id=r.id, name=r.name, kind=r.kind, created_at=r.created_at)
            for r in rows
        ],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants/{tenant_id}
# ---------------------------------------------------------------------------


@platform_tenants_router.get("/{tenant_id}", response_model=TenantSummaryResponse)
async def get_platform_tenant_by_id(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> TenantSummaryResponse:
    """GET /admin/platform/tenants/{tenant_id} — view any one tenant. SUPERADMIN only.

    Also runs the caller through authorize_tenant_scope (its first real production
    caller) — redundant with require_superadmin's role check today (this route tree is
    SUPERADMIN-only, unlike authorize_tenant_scope's general same-tenant allowance), but
    it is the semantically correct predicate for "may I act on tenant_id" and is what the
    milestone's own rationale names this task as wiring.
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.tenant.view",
        target_type="tenant",
        target_id=str(tenant_id),
        metadata={},
    )
    return TenantSummaryResponse(id=row.id, name=row.name, kind=row.kind, created_at=row.created_at)
