"""Domain ports for the MCP connector (mcp-connector-passthrough TASK.md §3 CONTRACT,
FROZEN @ v2 — CR-1 added `call_id` to `ToolCallObserver.record`) — typing.Protocol,
zero framework imports (CONVENTIONS.md/PROJECT.md).
"""

from __future__ import annotations

import uuid
from typing import Any, Literal, Protocol, runtime_checkable

from gateway.core.egress_policy import EgressPolicy
from gateway.mcp_connector.domain.entities import McpDialResult


@runtime_checkable
class ToolCallObserver(Protocol):
    """Fire-and-forget hook the sibling `tool-call-metering` task (depends-on this
    task) wires to the real pricing_unit dispatcher (M11). This task ships only a
    no-op default — it NEVER writes a `usage_records` row itself (one billing path).
    """

    async def record(
        self,
        *,
        call_id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        server_host: str,
        tool_name: str,
        status: Literal["success"],
        latency_ms: int,
    ) -> None:
        """Record one successfully-dialed (non-refused, non-blocked) tool call."""
        ...


class NoopToolCallObserver:
    """No-op default `ToolCallObserver` — this task's own shipped implementation."""

    async def record(
        self,
        *,
        call_id: uuid.UUID,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        server_host: str,
        tool_name: str,
        status: Literal["success"],
        latency_ms: int,
    ) -> None:
        return


@runtime_checkable
class McpDialer(Protocol):
    """Port for the outbound MCP-server dial — prod impl wraps httpx (M6/M7/M8).

    Raises:
        gateway.core.egress_policy.EgressDeniedError: the pinned IP (or the dialer's
            own pre-dial resolution) is denied — R4.
        gateway.mcp_connector.domain.errors.McpRedirectRejectedError: the upstream
            responded 3xx — R5.
        gateway.mcp_connector.domain.errors.McpDialTimeoutError: the dial exceeded its
            bound, or a connection-level error occurred — R6.
    """

    async def dial(
        self,
        *,
        server_url: str,
        message: dict[str, Any],
        upstream_headers: dict[str, str],
        egress_policy: EgressPolicy,
    ) -> McpDialResult: ...


__all__ = [
    "McpDialer",
    "NoopToolCallObserver",
    "ToolCallObserver",
]
