"""Domain entities for the MCP connector — zero framework imports (CONVENTIONS.md).

mcp-connector-passthrough TASK.md §3 CONTRACT (FROZEN @ v1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class McpServerEntry:
    """One allow-listed MCP server — `{url, label}` (§3 CONTRACT wire shape)."""

    url: str
    label: str


@dataclass(frozen=True, slots=True)
class McpDialResult:
    """The outcome of a successful (non-redirect, non-timeout) MCP dial.

    `body` is the parsed JSON-RPC response body (dict) — never the raw credential
    header (M13); the dialer never returns `upstream_headers` back to the caller.
    """

    status: int
    body: dict[str, Any] = field(default_factory=dict)
