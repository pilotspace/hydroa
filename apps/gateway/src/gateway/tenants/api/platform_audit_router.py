"""FastAPI router for the superadmin tenant Activity tab (tenant-activity-tab TASK.md §3 —
FROZEN @ v1).

Endpoint (under /admin/platform/tenants/{tenant_id}/audit):
  GET ""  — list the target tenant's real audit-event history, newest-first, paginated.

A SECOND, superadmin-scoped, any-target-tenant door onto the SAME audit_events trail the
tenant's own self-service GET /admin/audit (usage/api/router.py:get_audit) already exposes —
this route adds no new capability to the underlying AuditRepository, only a new caller.

Security (server-side, authoritative), in this exact order (M1):
  1. require_superadmin (Depends)                       — role-only gate, no target
  2. authorize_tenant_scope(identity, tenant_id)         — cheap in-memory check (same disclosed
     redundancy as every sibling platform_*_router.py: this whole tree is SUPERADMIN-only, so
     authorize_tenant_scope's reject-branch is unreachable here today, but it's the semantically
     correct predicate for "may I act on tenant_id")
  3. get_tenant_by_id(session, tenant_id) -> 404 TENANT_NOT_FOUND if the target tenant_id does
     not resolve to a real row — BEFORE any audit query
Both 2+3 are bundled in one locally-declared _require_target_tenant helper, byte-identical in
shape to platform_users_router.py's own (§0 Ground).

Pagination (M3): limit/offset are manually-parsed string Query params (not FastAPI-native int
coercion) so a bad value maps to 422 ERR_PAYLOAD_INVALID rather than FastAPI's own 422 shape —
mirrors get_audit's own _parse_pagination exactly (default limit=50, range 1-100; default
offset=0, >=0). Declared LOCALLY (_parse_audit_pagination) rather than imported from
usage/api/router.py — avoids a router-module-to-router-module import for a helper that is
trivial to redeclare (R10; matches platform_users_router.py's own DTO-redeclaration convention).

Data (M4): both AuditRepository calls are scoped by the PATH tenant_id — NEVER
identity.tenant_id (the superadmin's own reserved platform tenant) — wrapped in one
asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS) block (30.0s, a NEW local constant — not
cross-file-imported from usage/api/router.py's own _AUDIT_READ_TIMEOUT_SECONDS), mirroring
get_audit's own already-shipped IO design-for-failure guard verbatim (CLAUDE.md's global IO rule).

Response (M5): AuditListResponse/AuditEventItem are redeclared locally (not imported across the
router-module boundary), field-for-field identical to get_audit's own shape.

Audit (M6): on the success path only, this route calls emit_platform_audit(...) once
(action="platform.audit.list", target_type="audit", target_id=None) — matching the
zero-exception 15/15 existing platform_*_router.py convention (every platform route audits,
including pure reads).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.audit.infrastructure.audit_repository import AuditRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import PAYLOAD_INVALID, TENANT_NOT_FOUND
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.repository import get_tenant_by_id

platform_audit_router = APIRouter(
    prefix="/admin/platform/tenants/{tenant_id}/audit", tags=["platform-admin"]
)

_AUDIT_DEFAULT_LIMIT = 50
_AUDIT_MAX_LIMIT = 100
_AUDIT_READ_TIMEOUT_SECONDS = 30.0


# ---------------------------------------------------------------------------
# Schemas (re-declared locally, NOT imported from usage/api/router.py — see module
# docstring; matches platform_users_router.py's own DTO-redeclaration convention)
# ---------------------------------------------------------------------------


class AuditEventItem(BaseModel):
    """One row in the paginated audit log — field-for-field identical to get_audit's own."""

    id: str
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    result: str
    metadata: dict[str, object]
    created_at: str


class AuditListResponse(BaseModel):
    """Envelope: { items, total } — mirrors AuditListResponse's own shape in usage/api/router.py."""

    items: list[AuditEventItem]
    total: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _require_target_tenant(
    identity: Identity, tenant_id: uuid.UUID, session: AsyncSession
) -> None:
    """M1(b)+(c): authorize_tenant_scope THEN tenant-existence check — BEFORE any audit query.

    Byte-identical in shape to platform_users_router.py's own _require_target_tenant.
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()


def _parse_audit_pagination(limit: str | None, offset: str | None) -> tuple[int, int]:
    """Parse limit (1..100, default 50) + offset (>=0, default 0) — LOCAL, NOT imported from
    usage/api/router.py (R10). Same bounds/behavior as get_audit's own _parse_pagination.

    Parsed manually (not via FastAPI int coercion) so a bad value maps to the catalog
    ERR_PAYLOAD_INVALID code rather than FastAPI's own 422 shape.
    """
    parsed_limit = _AUDIT_DEFAULT_LIMIT
    if limit is not None:
        try:
            parsed_limit = int(limit)
        except ValueError:
            raise PAYLOAD_INVALID.exc() from None
        if parsed_limit < 1 or parsed_limit > _AUDIT_MAX_LIMIT:
            raise PAYLOAD_INVALID.exc()

    parsed_offset = 0
    if offset is not None:
        try:
            parsed_offset = int(offset)
        except ValueError:
            raise PAYLOAD_INVALID.exc() from None
        if parsed_offset < 0:
            raise PAYLOAD_INVALID.exc()

    return parsed_limit, parsed_offset


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants/{tenant_id}/audit
# ---------------------------------------------------------------------------


@platform_audit_router.get("", response_model=AuditListResponse)
async def get_platform_tenant_audit(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    limit: Annotated[str | None, Query()] = None,
    offset: Annotated[str | None, Query()] = None,
) -> AuditListResponse:
    """GET .../audit — list the target tenant's real audit-event history. SUPERADMIN only.

    A second, superadmin-scoped, any-target-tenant door onto the SAME audit_events trail the
    tenant's own self-service GET /admin/audit already exposes (tenant_id = PATH value, never
    identity.tenant_id). Newest-first, paginated. READ-ONLY. Also audits its OWN successful read
    (M6) — matches the zero-exception 15/15 existing platform_*_router.py convention.
    """
    await _require_target_tenant(identity, tenant_id, session)
    parsed_limit, parsed_offset = _parse_audit_pagination(limit, offset)

    repo = AuditRepository(session)
    async with asyncio.timeout(_AUDIT_READ_TIMEOUT_SECONDS):
        total = await repo.count_for_tenant(tenant_id)
        events = await repo.list_for_tenant_paged(tenant_id, parsed_limit, parsed_offset)

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.audit.list",
        target_type="audit",
        target_id=None,
        metadata={},
    )

    return AuditListResponse(
        items=[
            AuditEventItem(
                id=str(e.id),
                actor_email=e.actor_email,
                action=e.action,
                target_type=e.target_type,
                target_id=e.target_id,
                result=e.result,
                metadata=e.metadata,
                created_at=e.created_at.isoformat(),
            )
            for e in events
        ],
        total=total,
    )
