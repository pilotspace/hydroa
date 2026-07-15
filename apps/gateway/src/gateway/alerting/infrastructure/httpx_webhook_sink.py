"""HttpxWebhookSink — production httpx adapter for WebhookSink Protocol.

SSRF / egress hardening (audit-remediation H3 + Blocker 2 DNS-rebind close): the webhook
target is an operator-configured URL (`Settings.alert_webhook_url`), but it is still
server-side outbound HTTP dialed on a background loop with no human in the request path —
the same class of risk `core.egress_policy` (edge-input-hardening TASK.md §3 Part B,
FROZEN @ v1) already guards for BYOK-influenced adapters and the MCP dialer
(`mcp_connector/infrastructure/httpx_dialer.py`).

DNS-rebind close (mirrors httpx_dialer.py verbatim in shape): a bare `egress_policy.
check(url)` followed by `client.post(url)` resolves the hostname TWICE — once inside the
check, once inside httpx at connect time — so a rebinding resolver can answer benign to
the check and metadata/RFC1918 to the actual dial. We therefore resolve the hostname
ONCE here, build a "pinned" URL whose authority host is that literal IP, and pass THAT to
`egress_policy.check()`; given a literal-IP host the policy validates it directly with NO
further DNS lookup, so the check and the TCP dial that follows operate on the SAME IP. TLS
SNI and the `Host` header are kept as the ORIGINAL hostname (`extensions={"sni_hostname":
...}`) so certificate validation still targets the real hostname. `follow_redirects=False`
is enforced on the owned client and any 3xx is rejected by hand — the sink never dials a
`Location` it did not egress-check. The policy is checked FRESH before every dial (never
cached) and fails CLOSED.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from urllib.parse import urlsplit, urlunsplit

import httpx

from gateway.core.egress_policy import (
    DenyPrivateAndMetadataEgressPolicy,
    DnsResolver,
    EgressDeniedError,
    EgressPolicy,
)

_log = logging.getLogger(__name__)

_TIMEOUT = 10.0  # seconds
_RESOLVE_TIMEOUT = 2.0  # seconds


class _GetAddrInfoResolver:
    """Default resolver — real DNS via asyncio's non-blocking getaddrinfo.

    A fresh implementation of the same non-blocking primitive the MCP dialer uses
    (core.egress_policy._GetAddrInfoResolver is private to its module) — NOT a fork of
    any security logic, which lives entirely in the injected `egress_policy`.
    """

    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
        return [str(info[4][0]) for info in infos]


def _host_only(url: str) -> str:
    """Return only the host portion of a URL for safe logging."""
    try:
        return urlsplit(url).hostname or url
    except Exception:
        return "<url>"


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


class HttpxWebhookSink:
    """POST JSON payloads to a webhook URL using httpx, SSRF-hardened.

    Raises `gateway.core.egress_policy.EgressDeniedError` when the target (resolved to a
    literal IP FIRST) is an internal/link-local/metadata address, or on a rejected
    redirect — fail CLOSED, checked fresh before every dial. Raises httpx.HTTPError on
    connection errors. Returns HTTP status code on success.
    """

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        egress_policy: EgressPolicy | None = None,
        resolver: DnsResolver | None = None,
    ) -> None:
        # follow_redirects=False on the owned client — a 3xx is rejected by hand below.
        self._client = client or httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False)
        self._owns_client = client is None
        # Deny-by-default production wiring — only an EXPLICIT override (tests) relaxes it.
        self._egress_policy: EgressPolicy = (
            egress_policy if egress_policy is not None else DenyPrivateAndMetadataEgressPolicy()
        )
        self._resolver: DnsResolver = resolver if resolver is not None else _GetAddrInfoResolver()

    async def post_json(self, url: str, payload: dict[str, object]) -> int:
        """POST payload as JSON; return HTTP status code.

        Resolves the host ONCE, pins the dial to that literal IP, egress-checks the
        pinned IP, keeps Host/SNI as the original hostname, and rejects any 3xx — all
        BEFORE trusting the connection target. Raises `EgressDeniedError` (fail CLOSED)
        on a denied target or a rejected redirect.
        """
        parts = urlsplit(url)
        host = parts.hostname
        if not host:
            raise EgressDeniedError("host_missing")

        try:
            ipaddress.ip_address(host)
            pinned_ip = host  # already a literal IP — no resolution needed
        except ValueError:
            # Exactly ONE resolution, performed here — never repeated by the dial.
            try:
                resolved = await asyncio.wait_for(
                    self._resolver.resolve(host), timeout=_RESOLVE_TIMEOUT
                )
            except Exception:
                raise EgressDeniedError(f"dns_resolution_failed:{host}") from None
            if not resolved:
                raise EgressDeniedError(f"dns_resolution_failed:{host}") from None
            pinned_ip = resolved[0]

        pinned_url = _rewrite_host(url, _format_host_literal(pinned_ip))
        # The SAME literal IP just resolved is what the policy validates — its literal-IP
        # branch performs NO further DNS lookup, so check and dial share one IP.
        await self._egress_policy.check(pinned_url)

        _log.debug("webhook_sink: POSTing to %s (pinned %s)", host, pinned_ip)
        response = await self._client.post(
            pinned_url,
            json=payload,
            headers={"Host": host},
            extensions={"sni_hostname": host},
            # Per-request override so a redirect is NEVER auto-followed even if an
            # (injected, test-seam) client was built with follow_redirects=True — the
            # manual 3xx reject below then always gets to see the raw redirect first.
            follow_redirects=False,
        )
        if 300 <= response.status_code < 400:
            # Fail CLOSED — never follow a Location the sink did not egress-check.
            raise EgressDeniedError(f"redirect_rejected:{response.status_code}")
        return response.status_code

    async def aclose(self) -> None:
        """Close the underlying httpx client if owned by this instance."""
        if self._owns_client:
            await self._client.aclose()
