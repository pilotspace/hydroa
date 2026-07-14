"""POST /v1/mcp/call — MCP connector egress passthrough (mcp-connector-passthrough
TASK.md §3 — FROZEN @ v1).

Auth reuses `CompositeKeyAuthenticator` UNCHANGED (M14): an sk- API key or a
device-OAuth agent token both resolve to one AuthzResult — no new credential class.
Any caller-supplied tenant_id/key_id-shaped body field is ignored outright (M4);
resolution is ALWAYS keyed off the authenticated identity.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from gateway.core.error_catalog import AUTH_KEY_INVALID
from gateway.keys.domain.errors import InvalidApiKeyError
from gateway.mcp_connector.api.deps import get_mcp_authenticator, get_mcp_call_use_case
from gateway.mcp_connector.application.use_cases import McpCallUseCase
from gateway.proxy.infrastructure.composite_key_authenticator import CompositeKeyAuthenticator

mcp_proxy_router = APIRouter(prefix="/v1/mcp", tags=["mcp"])


class McpCallBody(BaseModel):
    """§3 CONTRACT wire shape. `message` is an opaque JSON-RPC 2.0 envelope — this
    gateway never interprets its shape beyond extracting `params.name` for tracing.
    Any extraneous body field (e.g. tenant_id/key_id) is simply ignored — Pydantic
    drops unknown fields by default; resolution never reads a body-supplied identity."""

    server_url: str
    message: dict[str, Any]
    upstream_headers: dict[str, str] = {}


def _extract_raw_key(request: Request) -> str:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return ""


@mcp_proxy_router.post("/call")
async def post_mcp_call(
    body: McpCallBody,
    request: Request,
    authenticator: Annotated[CompositeKeyAuthenticator, Depends(get_mcp_authenticator)],
    use_case: Annotated[McpCallUseCase, Depends(get_mcp_call_use_case)],
) -> dict[str, Any]:
    raw_key = _extract_raw_key(request)
    try:
        authz = await authenticator.authenticate(raw_key)
    except InvalidApiKeyError:
        raise AUTH_KEY_INVALID.exc() from None

    outcome = await use_case.execute(
        authz=authz,
        server_url=body.server_url,
        message=body.message,
        upstream_headers=body.upstream_headers,
    )
    return outcome.body


__all__ = ["mcp_proxy_router"]
