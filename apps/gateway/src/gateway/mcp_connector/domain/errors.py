"""Domain-level dial-outcome exceptions for the MCP connector.

Raised by McpDialer implementations; mapped to their §3 CONTRACT status/code by the
application-layer use case (never raised directly as an HTTP error here — zero
framework imports, CONVENTIONS.md). `EgressDeniedError` (core.egress_policy) is
reused verbatim for the egress-denied case — no parallel exception class for it.
"""

from __future__ import annotations


class McpRedirectRejectedError(Exception):
    """The upstream MCP server responded with a 3xx — never followed (M7, R5)."""


class McpDialTimeoutError(Exception):
    """The dial exceeded `mcp_connector_dial_timeout_seconds`, or a connection error
    occurred that the circuit breaker should count as a failure (M8, R6)."""
