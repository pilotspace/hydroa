"""Admin API router for tenant-level response cache toggle.

Contract FROZEN @ v4 (response-caching TASK.md §3):
  GET  /admin/cache  — any authenticated role; returns {"enabled": bool}
  PUT  /admin/cache  — owner or admin only; member → 403
                       body: {"enabled": bool}
                       returns: {"enabled": bool}
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.domain.entities import Identity

cache_router = APIRouter(prefix="/admin/cache", tags=["cache"])


class CacheGetResponse(BaseModel):
    enabled: bool


class CachePutRequest(BaseModel):
    enabled: bool


class CachePutResponse(BaseModel):
    enabled: bool


@cache_router.get("", response_model=CacheGetResponse)
async def get_cache(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CacheGetResponse:
    """GET /admin/cache — return current tenant-level cache toggle.

    Accessible to any authenticated role (owner, admin, member).
    """
    tenant_id = identity.tenant_id
    row = (
        await session.execute(
            text("SELECT cache_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    enabled = bool(row[0]) if row is not None and row[0] is not None else False
    return CacheGetResponse(enabled=enabled)


@cache_router.put("", response_model=CachePutResponse)
async def put_cache(
    body: CachePutRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CachePutResponse:
    """PUT /admin/cache — set the tenant-level cache toggle.

    Requires role owner or admin; member → 403 ERR_AUTH_FORBIDDEN.
    Accepts { enabled: bool }.
    """
    tenant_id = identity.tenant_id

    await session.execute(
        text("UPDATE tenants SET cache_enabled = :val WHERE id = :tid"),
        {"val": body.enabled, "tid": str(tenant_id)},
    )
    await session.commit()

    return CachePutResponse(enabled=body.enabled)
