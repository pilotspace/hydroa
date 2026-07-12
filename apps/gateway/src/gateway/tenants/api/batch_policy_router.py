"""Admin API router for tenant-level batch-grouping toggle.

Contract FROZEN @ v1 (batch-auto-grouping TASK.md §3):
  GET  /admin/batch-policy  — any authenticated role; returns {"enabled": bool}
  PUT  /admin/batch-policy  — owner or admin only; member → 403 ERR_AUTH_FORBIDDEN
                              body: {"enabled": bool}
                              returns: {"enabled": bool}

Mirrors cache_router.py's shape exactly (single tenant-wide toggle, no per-key
override — same RBAC tier, same re-read-after-write pattern).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.application.entitlements import check_plan_feature
from gateway.tenants.domain.entities import Identity

batch_policy_router = APIRouter(prefix="/admin/batch-policy", tags=["batches"])


class BatchPolicyGetResponse(BaseModel):
    enabled: bool


class BatchPolicyPutRequest(BaseModel):
    enabled: bool


class BatchPolicyPutResponse(BaseModel):
    enabled: bool


@batch_policy_router.get("", response_model=BatchPolicyGetResponse)
async def get_batch_policy(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchPolicyGetResponse:
    """GET /admin/batch-policy — return current tenant-level batch-grouping toggle.

    Accessible to any authenticated role (owner, admin, member).
    """
    tenant_id = identity.tenant_id
    row = (
        await session.execute(
            text("SELECT batch_grouping_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    enabled = bool(row[0]) if row is not None and row[0] is not None else False
    return BatchPolicyGetResponse(enabled=enabled)


@batch_policy_router.put("", response_model=BatchPolicyPutResponse)
async def put_batch_policy(
    body: BatchPolicyPutRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchPolicyPutResponse:
    """PUT /admin/batch-policy — set the tenant-level batch-grouping toggle.

    Requires role owner or admin; member → 403 ERR_AUTH_FORBIDDEN.

    plan-enforcement (TASK.md §3, M6/R3): ENABLING batch grouping is refused with 403
    ERR_PLAN_FEATURE_NOT_ENABLED when the tenant has an assigned plan whose feature_flags
    lacks "batch" — checked BEFORE any write (§5 Safety rule), so a rejected request
    leaves batch_grouping_enabled COMPLETELY unchanged. Disabling is never gated (only the
    forward/enabling direction needs the plan's permission). Inert for an unplanned tenant
    (M7).
    """
    tenant_id = identity.tenant_id

    if body.enabled:
        await check_plan_feature(session, tenant_id, "batch")

    await session.execute(
        text("UPDATE tenants SET batch_grouping_enabled = :enabled WHERE id = :tid"),
        {"enabled": body.enabled, "tid": str(tenant_id)},
    )
    await session.commit()

    row = (
        await session.execute(
            text("SELECT batch_grouping_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    current_enabled = bool(row[0]) if row is not None and row[0] is not None else False
    return BatchPolicyPutResponse(enabled=current_enabled)
