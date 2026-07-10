"""Admin API router for the tenant-level payload-capture toggle.

Contract FROZEN @ v1 (payload-capture-store TASK.md §3) — mirrors
tenants/api/cache_router.py's shape exactly:
  GET  /admin/capture  — any authenticated role; returns {"enabled": bool}
  PUT  /admin/capture  — owner or admin only; member -> 403
                         body: {"enabled": bool}   (required, unlike /admin/cache's
                         optional-partial body — capture has exactly one toggle field)
                         returns: {"enabled": bool}
                         422 ERR_PAYLOAD_INVALID  — non-boolean enabled
                         403 ERR_AUTH_FORBIDDEN   — member role
                         409 ERR_CAPTURE_ZDR_BLOCKED — enabling=true while under ZDR
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import CAPTURE_ZDR_BLOCKED
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.logs.domain.ports import ZdrOverridePort
from gateway.logs.infrastructure.zdr_noop import AlwaysAllowCapture
from gateway.tenants.domain.entities import Identity

capture_router = APIRouter(prefix="/admin/capture", tags=["capture"])


class CaptureGetResponse(BaseModel):
    enabled: bool


class CapturePutRequest(BaseModel):
    # strict=True: this is a fresh, single-purpose field (no existing lax-parsed
    # precedent to preserve, unlike cache_router.py's CachePutRequest) — deliberately
    # rejects lax-coercible strings like "yes"/"1" as ERR_PAYLOAD_INVALID rather than
    # silently accepting them as True (the §2 scenario's own literal example: PUT
    # {"enabled": "yes"} must 422, not silently coerce).
    enabled: bool = Field(strict=True)


class CapturePutResponse(BaseModel):
    enabled: bool


def get_zdr_port(request: Request) -> ZdrOverridePort:
    """Resolve ZdrOverridePort from app.state — allows test injection.

    Defaults to the permissive AlwaysAllowCapture (no tenant is ZDR) until the
    sibling tenant-retention-zdr task wires a real implementation onto app.state.
    """
    port = getattr(request.app.state, "zdr_port", None)
    if port is None:
        port = AlwaysAllowCapture()
    return port


@capture_router.get("", response_model=CaptureGetResponse)
async def get_capture(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CaptureGetResponse:
    """GET /admin/capture — return the current tenant-level capture toggle.

    Accessible to any authenticated role (owner, admin, member).
    """
    tenant_id = identity.tenant_id
    row = (
        await session.execute(
            text("SELECT payload_capture_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    enabled = bool(row[0]) if row is not None and row[0] is not None else False
    return CaptureGetResponse(enabled=enabled)


@capture_router.put("", response_model=CapturePutResponse)
async def put_capture(
    body: CapturePutRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    zdr_port: Annotated[ZdrOverridePort, Depends(get_zdr_port)],
) -> CapturePutResponse:
    """PUT /admin/capture — set the tenant-level capture toggle.

    Requires role owner or admin; member -> 403 ERR_AUTH_FORBIDDEN.
    Rejects enabling=true for a tenant under Zero-Data-Retention (409
    ERR_CAPTURE_ZDR_BLOCKED) — the toggle must never read as "on" when it can never
    actually capture anything (honest-degradation, not cosmetic). The stored value is
    left UNCHANGED on rejection.
    """
    tenant_id = identity.tenant_id

    if body.enabled:
        # Bounded — mirrors SqlAlchemyPayloadCapture.capture()'s own asyncio.wait_for
        # around this exact port call (verify fix 2026-07-10): a ZdrOverridePort
        # implementation that hangs must not hang this admin request forever. A
        # timeout is a TimeoutError, caught by the same except-Exception below and
        # mapped to the identical fail-closed 409 — a bounded, honest error rather
        # than an indefinite hang.
        settings = getattr(request.app.state, "settings", None)
        timeout_seconds = getattr(settings, "capture_persist_timeout_seconds", 3.0)
        try:
            is_zdr = await asyncio.wait_for(zdr_port.is_zdr(tenant_id), timeout=timeout_seconds)
        except Exception:
            # Defense-in-depth mirror of SqlAlchemyPayloadCapture's own fail-closed
            # posture: an unconfirmable OR unbounded ZDR status blocks the enable,
            # never accepts it.
            is_zdr = True
        if is_zdr:
            raise CAPTURE_ZDR_BLOCKED.exc()

    await session.execute(
        text("UPDATE tenants SET payload_capture_enabled = :enabled WHERE id = :tid"),
        {"enabled": body.enabled, "tid": str(tenant_id)},
    )
    await session.commit()

    row = (
        await session.execute(
            text("SELECT payload_capture_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    current_enabled = bool(row[0]) if row is not None and row[0] is not None else False
    return CapturePutResponse(enabled=current_enabled)
