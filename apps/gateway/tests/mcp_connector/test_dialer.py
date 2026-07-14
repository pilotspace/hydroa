"""Pure-unit suite for `HttpxMcpDialer` (mcp-connector-passthrough TASK.md §3/§5,
FROZEN @ v2) — M6 (DNS-rebind close via resolve-once-then-pin), M7 (redirects never
followed), M8 (timeout -> McpDialTimeoutError, never retried).

No real network/DNS: an injectable fake `DnsResolver` (mirrors
`tests/edge_input_hardening/test_s3_egress_policy.py`'s `_FakeResolver`/
`_HangingResolver`) plus `httpx.MockTransport` stand in for the wire.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from gateway.core.egress_policy import DenyPrivateAndMetadataEgressPolicy, EgressDeniedError
from gateway.mcp_connector.domain.errors import McpDialTimeoutError, McpRedirectRejectedError
from gateway.mcp_connector.infrastructure.httpx_dialer import HttpxMcpDialer

pytestmark = pytest.mark.asyncio


class _FakeResolver:
    def __init__(self, answers: dict[str, list[str]] | None = None, *, raise_error: bool = False) -> None:
        self._answers = answers or {}
        self._raise_error = raise_error
        self.calls: list[str] = []

    async def resolve(self, host: str) -> list[str]:
        self.calls.append(host)
        if self._raise_error:
            raise OSError("simulated DNS failure")
        return self._answers.get(host, [])


class _HangingResolver:
    async def resolve(self, host: str) -> list[str]:
        self.calls: list[str] = getattr(self, "calls", [])
        await asyncio.sleep(999)
        return []


def _echo_handler(observed: list[httpx.Request]):
    def _handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"content": []}})

    return _handler


# ---------------------------------------------------------------------------
# M6: DNS-rebind close — resolve-once, pin the literal IP, SNI/Host preserved
# ---------------------------------------------------------------------------


async def test_dial_connects_to_the_exact_resolved_ip_resolver_called_once() -> None:
    observed: list[httpx.Request] = []
    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    result = await dialer.dial(
        server_url="https://mcp.acme.example/v1",
        message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
        upstream_headers={},
        egress_policy=policy,
    )

    assert result.status == 200
    assert resolver.calls == ["mcp.acme.example"], "exactly one resolution, for the right host"
    assert len(observed) == 1
    assert observed[0].url.host == "93.184.216.34", (
        "the TCP dial must target the SAME IP the dialer itself resolved — no second, "
        "independent resolution may influence the connection target"
    )


async def test_dial_preserves_original_hostname_for_sni_and_host_header() -> None:
    observed: list[httpx.Request] = []
    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    await dialer.dial(
        server_url="https://mcp.acme.example/v1",
        message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
        upstream_headers={},
        egress_policy=policy,
    )

    request = observed[0]
    assert request.headers.get("host") == "mcp.acme.example", (
        "the Host header must stay the ORIGINAL hostname even though the TCP dial "
        "targets the pinned literal IP"
    )
    assert request.extensions.get("sni_hostname") == "mcp.acme.example", (
        "TLS SNI must target the original hostname for certificate validation to succeed"
    )


async def test_dns_rebind_to_private_ip_is_denied_zero_dials() -> None:
    """The dialer resolves the hostname to a PRIVATE address (a DNS-rebind) — the pinned
    literal-IP URL passed to egress_policy.check() must be denied, and the transport must
    NEVER be invoked (the TCP dial never happens)."""
    observed: list[httpx.Request] = []
    resolver = _FakeResolver({"evil.example.com": ["10.0.0.5"]})
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(EgressDeniedError):
        await dialer.dial(
            server_url="https://evil.example.com/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert len(observed) == 0, "a DNS-rebind to a private IP must cost ZERO egress dials"


async def test_dns_rebind_to_metadata_ip_is_denied_zero_dials() -> None:
    observed: list[httpx.Request] = []
    resolver = _FakeResolver({"evil.example.com": ["169.254.169.254"]})
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(EgressDeniedError):
        await dialer.dial(
            server_url="https://evil.example.com/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert len(observed) == 0


async def test_literal_ip_host_skips_resolver_entirely() -> None:
    """A server_url whose host is ALREADY a literal IP needs no DNS — the resolver must
    never be invoked at all, and the literal IP is checked/dialed directly."""
    observed: list[httpx.Request] = []
    resolver = _FakeResolver()
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    result = await dialer.dial(
        server_url="https://93.184.216.34/v1",
        message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
        upstream_headers={},
        egress_policy=policy,
    )

    assert result.status == 200
    assert resolver.calls == [], "a literal-IP host must never invoke the resolver"


async def test_literal_private_ip_host_denied_zero_dials() -> None:
    observed: list[httpx.Request] = []
    dialer = HttpxMcpDialer(transport=httpx.MockTransport(_echo_handler(observed)))
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(EgressDeniedError):
        await dialer.dial(
            server_url="https://127.0.0.1/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert len(observed) == 0


async def test_dns_resolution_failure_denies_zero_dials() -> None:
    observed: list[httpx.Request] = []
    resolver = _FakeResolver(raise_error=True)
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(EgressDeniedError):
        await dialer.dial(
            server_url="https://nxdomain.example.com/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert len(observed) == 0


# ---------------------------------------------------------------------------
# M7: redirects are never followed
# ---------------------------------------------------------------------------


async def test_redirect_response_is_rejected_and_location_never_dialed() -> None:
    call_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        assert request.url.host != "169.254.169.254"
        return httpx.Response(
            302, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        )

    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(resolver=resolver, transport=httpx.MockTransport(_handler))
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(McpRedirectRejectedError):
        await dialer.dial(
            server_url="https://mcp.acme.example/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert call_count == 1, "exactly one request — the redirect target must never be dialed"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_every_3xx_status_is_rejected(status: int) -> None:
    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(
        resolver=resolver,
        transport=httpx.MockTransport(lambda r: httpx.Response(status, headers={"Location": "https://elsewhere.example/"})),
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(McpRedirectRejectedError):
        await dialer.dial(
            server_url="https://mcp.acme.example/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )


# ---------------------------------------------------------------------------
# M8: dial timeout / connection error -> McpDialTimeoutError, never retried
# ---------------------------------------------------------------------------


async def test_transport_timeout_raises_dial_timeout_error_single_attempt() -> None:
    attempt_count = 0

    def _handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempt_count
        attempt_count += 1
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(resolver=resolver, transport=httpx.MockTransport(_handler))
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(McpDialTimeoutError):
        await dialer.dial(
            server_url="https://mcp.acme.example/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert attempt_count == 1, "a dial timeout must NEVER be retried (M8)"


async def test_transport_connection_error_raises_dial_timeout_error() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated connection refused", request=request)

    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(resolver=resolver, transport=httpx.MockTransport(_handler))
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(McpDialTimeoutError):
        await dialer.dial(
            server_url="https://mcp.acme.example/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )


async def test_slow_dns_resolution_times_out_denied_zero_dials() -> None:
    observed: list[httpx.Request] = []
    dialer = HttpxMcpDialer(
        resolver=_HangingResolver(),
        resolve_timeout_s=0.05,
        transport=httpx.MockTransport(_echo_handler(observed)),
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    with pytest.raises(EgressDeniedError):
        await dialer.dial(
            server_url="https://slow.example.com/v1",
            message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
            upstream_headers={},
            egress_policy=policy,
        )

    assert len(observed) == 0


# ---------------------------------------------------------------------------
# Upstream credential header is forwarded to the ACTUAL dial (never withheld from the
# legitimate upstream call — M13 only forbids it from CAPTURE/AUDIT metadata, tested in
# test_call_flow.py, not here)
# ---------------------------------------------------------------------------


async def test_upstream_headers_are_forwarded_to_the_dial() -> None:
    observed: list[httpx.Request] = []
    resolver = _FakeResolver({"mcp.acme.example": ["93.184.216.34"]})
    dialer = HttpxMcpDialer(
        resolver=resolver, transport=httpx.MockTransport(_echo_handler(observed))
    )
    policy = DenyPrivateAndMetadataEgressPolicy()

    await dialer.dial(
        server_url="https://mcp.acme.example/v1",
        message={"jsonrpc": "2.0", "id": 1, "method": "tools/call"},
        upstream_headers={"Authorization": "Bearer upstream-secret-token"},
        egress_policy=policy,
    )

    assert observed[0].headers.get("authorization") == "Bearer upstream-secret-token"
