"""Admin API router for tenant-level response cache toggle.

Contract FROZEN @ v4 (response-caching TASK.md §3):
  GET  /admin/cache  — any authenticated role; returns {"enabled": bool}
  PUT  /admin/cache  — owner or admin only; member → 403
                       body: {"enabled": bool}
                       returns: {"enabled": bool}

Extended @ v5 (semantic-cache TASK.md §3) — ADDITIVE:
  GET  /admin/cache  — returns {"enabled": bool, "semantic_enabled": bool}
  PUT  /admin/cache  — body: {"enabled"?: bool, "semantic_enabled"?: bool}
                       returns: {"enabled": bool, "semantic_enabled": bool}
  Both fields optional in PUT body; absent = no change to that field.
  v4 frozen tests only assert body["enabled"] — additive fields do not break them.
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
    semantic_enabled: bool = False


class CachePutRequest(BaseModel):
    enabled: bool | None = None
    semantic_enabled: bool | None = None


class CachePutResponse(BaseModel):
    enabled: bool
    semantic_enabled: bool = False


@cache_router.get("", response_model=CacheGetResponse)
async def get_cache(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CacheGetResponse:
    """GET /admin/cache — return current tenant-level cache toggle.

    Accessible to any authenticated role (owner, admin, member).
    Returns {"enabled": bool, "semantic_enabled": bool}.
    """
    tenant_id = identity.tenant_id
    row = (
        await session.execute(
            text("SELECT cache_enabled, semantic_cache_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    enabled = bool(row[0]) if row is not None and row[0] is not None else False
    semantic_enabled = bool(row[1]) if row is not None and row[1] is not None else False
    return CacheGetResponse(enabled=enabled, semantic_enabled=semantic_enabled)


@cache_router.put("", response_model=CachePutResponse)
async def put_cache(
    body: CachePutRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CachePutResponse:
    """PUT /admin/cache — set the tenant-level cache and/or semantic cache toggle.

    Requires role owner or admin; member → 403 ERR_AUTH_FORBIDDEN.
    Accepts { enabled?: bool, semantic_enabled?: bool }.
    Absent fields are not changed (sentinel/PATCH semantics).
    Returns {"enabled": bool, "semantic_enabled": bool}.
    """
    tenant_id = identity.tenant_id

    # Build SET clause dynamically — only update fields that were provided
    set_parts = []
    params: dict[str, object] = {"tid": str(tenant_id)}
    if body.enabled is not None:
        set_parts.append("cache_enabled = :cache_enabled")
        params["cache_enabled"] = body.enabled
    if body.semantic_enabled is not None:
        set_parts.append("semantic_cache_enabled = :semantic_cache_enabled")
        params["semantic_cache_enabled"] = body.semantic_enabled

    if set_parts:
        # set_parts contains only hard-coded column=:param strings — no user input in SQL.
        sql = f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = :tid"  # noqa: S608
        await session.execute(text(sql), params)
        await session.commit()

    # Re-read the current state to return accurate values
    row = (
        await session.execute(
            text("SELECT cache_enabled, semantic_cache_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    current_enabled = bool(row[0]) if row is not None and row[0] is not None else False
    current_semantic = bool(row[1]) if row is not None and row[1] is not None else False
    return CachePutResponse(enabled=current_enabled, semantic_enabled=current_semantic)
