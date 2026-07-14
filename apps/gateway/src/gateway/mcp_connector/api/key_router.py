"""Admin API router for per-key MCP server allow-list overrides (mcp-connector-
passthrough TASK.md §3 — FROZEN @ v1, M2, R2, R3, R7, R8).

Mirrors `keys/api/key_guardrail_router.py`'s shape+idiom exactly: owner/admin only via
`require_owner_or_admin`, an unknown/cross-tenant/revoked key_id collapses to one 404
(no existence leak), race-safe `UPDATE ... RETURNING id`.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.egress_policy import EgressDeniedError, assert_literal_host_not_denied
from gateway.core.error_catalog import (
    KEY_NOT_FOUND,
    MCP_SERVER_LIST_TOO_LONG,
    MCP_SERVER_URL_INVALID,
)
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.mcp_connector.infrastructure.repository import (
    McpServerPolicyRepository,
    validate_server_count,
)
from gateway.tenants.domain.entities import Identity

mcp_key_router = APIRouter(prefix="/admin/keys/{key_id}/mcp-servers", tags=["mcp"])

_repo = McpServerPolicyRepository()


class McpServerEntryBody(BaseModel):
    url: str
    label: str = ""


class McpKeyServersPutBody(BaseModel):
    servers: list[McpServerEntryBody] | None = None


class McpKeyServersResponse(BaseModel):
    servers: list[dict[str, str]] | None
    source: Literal["key", "tenant"]


def _validate_servers(entries: list[McpServerEntryBody]) -> list[dict[str, str]]:
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


@mcp_key_router.get("", response_model=McpKeyServersResponse)
async def get_key_mcp_servers(
    key_id: uuid.UUID,
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> McpKeyServersResponse:
    """GET /admin/keys/{key_id}/mcp-servers — any authenticated tenant role (M2)."""
    override = await _repo.get_key_override(session, key_id=key_id, tenant_id=identity.tenant_id)
    if override == "__not_found__":
        raise KEY_NOT_FOUND.exc()
    if override is not None:
        return McpKeyServersResponse(servers=override, source="key")

    tenant_result = await _repo.get_tenant_servers(session, identity.tenant_id)
    tenant_servers = tenant_result[0] if tenant_result is not None else []
    return McpKeyServersResponse(servers=tenant_servers, source="tenant")


@mcp_key_router.put("", response_model=McpKeyServersResponse)
async def put_key_mcp_servers(
    key_id: uuid.UUID,
    body: McpKeyServersPutBody,
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> McpKeyServersResponse:
    """PUT /admin/keys/{key_id}/mcp-servers — owner/admin only (M2, R7).

    `servers: null` clears back to tenant inheritance; `servers: []` is an explicit
    empty override — this key may call NO server (never silently reinterpreted as
    "inherit tenant", M2).
    """
    validated = _validate_servers(body.servers) if body.servers is not None else None

    found = await _repo.put_key_override(
        session, key_id=key_id, tenant_id=identity.tenant_id, servers=validated
    )
    if not found:
        raise KEY_NOT_FOUND.exc()

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="mcp_server_policy.key_put",
                target_type="api_key",
                target_id=str(key_id),
                result="success",
                metadata={"key_id": str(key_id), "count": len(validated) if validated else 0},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return McpKeyServersResponse(servers=validated, source="key")


@mcp_key_router.delete("", status_code=204)
async def delete_key_mcp_servers(
    key_id: uuid.UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """DELETE /admin/keys/{key_id}/mcp-servers — owner/admin only; clears the
    override, reverting to tenant inheritance (M2, R7)."""
    found = await _repo.delete_key_override(session, key_id=key_id, tenant_id=identity.tenant_id)
    if not found:
        raise KEY_NOT_FOUND.exc()

    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="mcp_server_policy.key_delete",
                target_type="api_key",
                target_id=str(key_id),
                result="success",
                metadata={"key_id": str(key_id)},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return Response(status_code=204)


__all__ = ["mcp_key_router"]
