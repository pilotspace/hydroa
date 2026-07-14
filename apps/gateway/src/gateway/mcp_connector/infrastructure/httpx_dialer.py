"""HttpxMcpDialer — the McpDialer prod implementation (M6/M7/M8, the highest-risk unit
in this task per TASK.md §5 Strategy step 5).

DNS-rebind close (M6, contract-level Must per Tin's freeze decision): resolves the
target hostname to a literal IP ITSELF — via an injectable resolver, exactly ONE
resolution per dial — builds a "pinned" URL whose authority HOST is that literal IP,
and passes THAT literal-IP URL to `egress_policy.check()`. Given a literal-IP host,
`DenyPrivateAndMetadataEgressPolicy.check()` (core.egress_policy, reused verbatim, NOT
forked) validates it directly with NO further DNS lookup — so the check and the actual
TCP dial that follows both operate on the SAME IP; no second, independent resolution
ever influences the connection target. TLS SNI and the `Host` header are set to the
ORIGINAL hostname (`extensions={"sni_hostname": ...}`) so certificate validation still
targets the real hostname while the TCP connection is the pinned, egress-checked IP —
the fix for the "resolve once, pass IP as host, keep Host header" trap TASK.md §5 names
(a naive version would break SNI/cert validation for https).

follow_redirects=False (M7): asserted on the client constructed for every dial; any 3xx
response is raised as `McpRedirectRejectedError` BEFORE the caller ever sees or dials a
`Location` it did not ask for.

Fresh-connection trap (TASK.md §5 known-problem fix): a pooled/keep-alive client could
silently reuse a PRE-pin connection for a different resolved IP on a LATER call to the
same host. A brand-new, unpooled `httpx.AsyncClient` is built per dial and closed
immediately after — never shared/reused across calls.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from gateway.core.egress_policy import DnsResolver, EgressDeniedError, EgressPolicy
from gateway.mcp_connector.domain.entities import McpDialResult
from gateway.mcp_connector.domain.errors import McpDialTimeoutError, McpRedirectRejectedError


class _GetAddrInfoResolver:
    """Default resolver — real DNS via asyncio's non-blocking getaddrinfo.

    A separate instance of the SAME primitive `core.egress_policy._GetAddrInfoResolver`
    uses — that class is private to its module, so this is a fresh implementation of
    the identical non-blocking getaddrinfo call, not a fork of any SECURITY logic (the
    security logic lives entirely in `DenyPrivateAndMetadataEgressPolicy`, reused
    verbatim via the injected `egress_policy` parameter).
    """

    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
        return [str(info[4][0]) for info in infos]


def _format_host_literal(ip_literal: str) -> str:
    """IPv6 literals need brackets in a URL authority; IPv4 doesn't."""
    try:
        ip = ipaddress.ip_address(ip_literal)
    except ValueError:
        return ip_literal
    return f"[{ip_literal}]" if ip.version == 6 else ip_literal


def _rewrite_host(url: str, new_host_literal: str) -> str:
    """Return `url` with its authority's host replaced by `new_host_literal` (port kept)."""
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port is not None else ""
    userinfo = ""
    if parts.username:
        userinfo = parts.username + (f":{parts.password}" if parts.password else "") + "@"
    new_netloc = f"{userinfo}{new_host_literal}{port}"
    return urlunsplit((parts.scheme, new_netloc, parts.path, parts.query, parts.fragment))


class HttpxMcpDialer:
    """Production `McpDialer` — see module docstring for the M6 DNS-rebind close."""

    def __init__(
        self,
        *,
        dial_timeout_seconds: float = 30.0,
        resolver: DnsResolver | None = None,
        resolve_timeout_s: float = 2.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._dial_timeout_seconds = dial_timeout_seconds
        self._resolver: DnsResolver = resolver if resolver is not None else _GetAddrInfoResolver()
        self._resolve_timeout_s = resolve_timeout_s
        # TEST SEAM ONLY: a fixed transport injected by tests (httpx.MockTransport).
        # Production NEVER sets this — a fresh, unpooled client is built per dial (see
        # module docstring's "fresh-connection trap" fix).
        self._transport = transport

    async def dial(
        self,
        *,
        server_url: str,
        message: dict[str, Any],
        upstream_headers: dict[str, str],
        egress_policy: EgressPolicy,
    ) -> McpDialResult:
        parts = urlsplit(server_url)
        host = parts.hostname
        if not host:
            raise EgressDeniedError("host_missing")

        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None

        if literal_ip is not None:
            pinned_ip = host
        else:
            # Exactly ONE resolution, performed by the dialer itself — never repeated.
            # No second, independent resolution ever influences the connection target.
            try:
                resolved = await asyncio.wait_for(
                    self._resolver.resolve(host), timeout=self._resolve_timeout_s
                )
            except Exception:
                raise EgressDeniedError(f"dns_resolution_failed:{host}") from None
            if not resolved:
                raise EgressDeniedError(f"dns_resolution_failed:{host}")
            pinned_ip = resolved[0]

        pinned_url = _rewrite_host(server_url, _format_host_literal(pinned_ip))
        # The SAME literal IP just resolved is what egress_policy validates — its
        # literal-IP branch performs NO further DNS lookup (core/egress_policy.py).
        await egress_policy.check(pinned_url)

        headers = {**upstream_headers, "Host": host, "Content-Type": "application/json"}
        client = httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            timeout=self._dial_timeout_seconds,
        )
        try:
            response = await asyncio.wait_for(
                client.post(
                    pinned_url,
                    json=message,
                    headers=headers,
                    extensions={"sni_hostname": host},
                ),
                timeout=self._dial_timeout_seconds,
            )
        except (TimeoutError, httpx.TimeoutException, httpx.TransportError) as exc:
            raise McpDialTimeoutError(str(exc)) from None
        finally:
            await client.aclose()

        if 300 <= response.status_code < 400:
            raise McpRedirectRejectedError(f"redirect_rejected:{response.status_code}")

        try:
            body = response.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {"result": body}
        return McpDialResult(status=response.status_code, body=body)


__all__ = ["HttpxMcpDialer"]
