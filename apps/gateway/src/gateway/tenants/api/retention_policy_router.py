"""Admin API router for per-tenant retention window + Zero-Data-Retention (ZDR) mode.

Contract FROZEN @ v1 (tenant-retention-zdr TASK.md §3):
  GET  /admin/retention-policy — any authenticated role (get_identity); returns the
                                  tenant's current policy + computed effective_window_days
                                  per swept table + operator_ceiling_days (informational).
  PUT  /admin/retention-policy — requires Permission.SECURITY_CONFIG (OWNER only);
                                  partial-merge body { window_days?, zdr_enabled? },
                                  absent keys preserved (guardrail_router.py convention).

Both operate ONLY on the caller's own identity.tenant_id (no tenant_id path/query
param exists — structurally impossible to target another tenant). A caller whose
identity.tenant_id does not resolve to a real tenants row (e.g. deleted/orphaned) gets
404 ERR_TENANT_NOT_FOUND — never a leak (R5).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import RETENTION_WINDOW_INVALID, TENANT_NOT_FOUND
from gateway.keys.api.deps import get_identity
from gateway.tenants.application.retention_policy import effective_window_map
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

retention_policy_router = APIRouter(prefix="/admin/retention-policy", tags=["retention"])


class RetentionPolicyBody(BaseModel):
    """PUT body — partial merge: absent keys preserve existing values."""

    window_days: int | None = None
    zdr_enabled: bool | None = None


class RetentionPolicyResponse(BaseModel):
    window_days: int | None
    effective_window_days: dict[str, int]
    zdr_enabled: bool
    zdr_enabled_at: str | None
    operator_ceiling_days: int


async def _fetch_tenant_row(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[int | None, bool, datetime | None] | None:
    row = (
        await session.execute(
            text(
                "SELECT retention_window_days, zdr_enabled, zdr_enabled_at"
                " FROM tenants WHERE id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
    ).first()
    if row is None:
        return None
    return (row[0], bool(row[1]), row[2])


def _build_response(
    row: tuple[int | None, bool, datetime | None], settings: Any
) -> RetentionPolicyResponse:
    window_days, zdr_enabled, zdr_enabled_at = row
    return RetentionPolicyResponse(
        window_days=window_days,
        effective_window_days=effective_window_map(
            tenant_window_days=window_days, settings=settings
        ),
        zdr_enabled=zdr_enabled,
        zdr_enabled_at=zdr_enabled_at.isoformat() if zdr_enabled_at is not None else None,
        operator_ceiling_days=settings.retention_tenant_window_ceiling_days,
    )


@retention_policy_router.get("", response_model=RetentionPolicyResponse)
async def get_retention_policy(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> RetentionPolicyResponse:
    """GET /admin/retention-policy — any authenticated role."""
    row = await _fetch_tenant_row(session, identity.tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    return _build_response(row, request.app.state.settings)


@retention_policy_router.put("", response_model=RetentionPolicyResponse)
async def put_retention_policy(
    body: RetentionPolicyBody,
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> RetentionPolicyResponse:
    """PUT /admin/retention-policy — OWNER only (Permission.SECURITY_CONFIG).

    422 ERR_RETENTION_WINDOW_INVALID when window_days <= 0 or exceeds
    operator_ceiling_days. zdr_enabled_at is set only on a false->true transition and
    is NOT cleared on a later true->false transition (preserves the compliance record
    of when ZDR was most recently enabled).
    """
    settings = request.app.state.settings
    tenant_id = identity.tenant_id

    current = await _fetch_tenant_row(session, tenant_id)
    if current is None:
        raise TENANT_NOT_FOUND.exc()

    fields_set = body.model_fields_set
    new_window_days, new_zdr_enabled, new_zdr_enabled_at = current

    if "window_days" in fields_set:
        wd = body.window_days
        if wd is not None:
            if wd <= 0 or wd > settings.retention_tenant_window_ceiling_days:
                raise RETENTION_WINDOW_INVALID.exc()
        new_window_days = wd

    zdr_transitioned_on = False
    if "zdr_enabled" in fields_set and body.zdr_enabled is not None:
        if body.zdr_enabled and not new_zdr_enabled:
            zdr_transitioned_on = True
            new_zdr_enabled_at = datetime.now(UTC)
        new_zdr_enabled = body.zdr_enabled

    await session.execute(
        text(
            "UPDATE tenants SET retention_window_days = :wd, zdr_enabled = :zdr,"
            " zdr_enabled_at = :zdr_at WHERE id = :tid"
        ),
        {
            "wd": new_window_days,
            "zdr": new_zdr_enabled,
            "zdr_at": new_zdr_enabled_at,
            "tid": str(tenant_id),
        },
    )
    await session.commit()

    # Audit emit — fail-open fire-and-forget (M10; mirrors budgets/api/router.py PUT).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="retention_policy.update",
                target_type="retention_policy",
                target_id=str(tenant_id),
                result="success",
                metadata={
                    "window_days": new_window_days,
                    "zdr_enabled": new_zdr_enabled,
                    "zdr_transitioned_on": zdr_transitioned_on,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    return _build_response((new_window_days, new_zdr_enabled, new_zdr_enabled_at), settings)
