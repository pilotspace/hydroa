"""Admin API router for the tenant-wide MCP server allow-list (mcp-connector-
passthrough TASK.md §3 — FROZEN @ v1, M1, R2, R3, R7).

Mirrors `tenants/api/residency_policy_router.py`'s shape+idiom exactly: OWNER-only via
the SAME reused `Permission.SECURITY_CONFIG` (no new Permission enum member —
PROJECT.md DDD note), fire-and-forget `record_audit` on every successful write, both
operate ONLY on the caller's own `identity.tenant_id` (no tenant_id path/query param —
structurally impossible to target another tenant).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.egress_policy import EgressDeniedError, assert_literal_host_not_denied
from gateway.core.error_catalog import MCP_SERVER_LIST_TOO_LONG, MCP_SERVER_URL_INVALID
from gateway.keys.api.deps import get_identity
from gateway.mcp_connector.infrastructure.repository import (
    McpServerPolicyRepository,
    validate_server_count,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

mcp_admin_router = APIRouter(prefix="/admin/mcp-servers", tags=["mcp"])

_repo = McpServerPolicyRepository()


class McpServerEntryBody(BaseModel):
    url: str
    label: str = ""


class McpServersPutBody(BaseModel):
    servers: list[McpServerEntryBody]


class McpServersResponse(BaseModel):
    servers: list[dict[str, str]]
    updated_at: str | None


def _validate_servers(entries: list[McpServerEntryBody]) -> list[dict[str, str]]:
    """R2 (write-time literal-IP/scheme check, M3) + R3 (50-entry cap)."""
    if not validate_server_count(entries):
        raise MCP_SERVER_LIST_TOO_LONG.exc()
    servers: list[dict[str, str]] = []
    for entry in entries:
        try:
            assert_literal_host_not_denied(entry.url)
        except EgressDeniedError:
            raise MCP_SERVER_URL_INVALID.exc() from None
        servers.append({"url": entry.url, "label": entry.label})
    return servers


@mcp_admin_router.get("", response_model=McpServersResponse)
async def get_mcp_servers(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> McpServersResponse:
    """GET /admin/mcp-servers — any authenticated tenant role (M1)."""
    result = await _repo.get_tenant_servers(session, identity.tenant_id)
    servers, updated_at = result if result is not None else ([], None)
    return McpServersResponse(
        servers=servers, updated_at=updated_at.isoformat() if updated_at is not None else None
    )


@mcp_admin_router.put("", response_model=McpServersResponse)
async def put_mcp_servers(
    body: McpServersPutBody,
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> McpServersResponse:
    """PUT /admin/mcp-servers — OWNER only (M1, R7).

    Wholesale replace. Duplicate URLs are stored without erroring (last-write-wins for
    that url is acceptable — the write is not rejected). Any 422 leaves the row
    unchanged and records no audit event.
    """
    validated = _validate_servers(body.servers)

    tenant_id = identity.tenant_id
    updated_at = await _repo.put_tenant_servers(session, tenant_id, validated)

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="mcp_server_policy.put",
                target_type="mcp_server_policy",
                target_id=str(tenant_id),
                result="success",
                metadata={"count": len(validated)},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return McpServersResponse(servers=validated, updated_at=updated_at.isoformat())


__all__ = ["mcp_admin_router"]
