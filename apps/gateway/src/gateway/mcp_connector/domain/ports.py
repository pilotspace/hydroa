"""Domain ports for the MCP connector (mcp-connector-passthrough TASK.md §3 CONTRACT,
FROZEN @ v2 — CR-1 added `call_id` to `ToolCallObserver.record`) — typing.Protocol,
zero framework imports (CONVENTIONS.md/PROJECT.md).

audit-remediation (HIGH, agent-principal MCP metering unfunded): `record` gained an
Optional `agent_principal_id` keyword (default None) so the mcp-connector call site can
forward `authz.agent_principal_id` through to the tool-call-metering observer, which
forwards it byte-for-byte into `UsageRecorder.record()` — the SAME per-agent-principal
spend attribution every other billed call path (chat/non-chat) already threads. Kept
Optional-with-default so this is additive, not a breaking Protocol widening: any other
existing `ToolCallObserver` implementation (a test fake, a future caller) that omits the
keyword entirely still type-checks and behaves exactly as before.
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
        agent_principal_id: uuid.UUID | None = None,
    ) -> None:
        """Record one successfully-dialed (non-refused, non-blocked) tool call.

        agent_principal_id: the calling AuthzResult's agent-principal attribution
          (None for a token unattached to any principal, e.g. an sk- key) — mirrors
          every other billed call path's agent_principal_id passthrough.
        """
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
        agent_principal_id: uuid.UUID | None = None,
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
