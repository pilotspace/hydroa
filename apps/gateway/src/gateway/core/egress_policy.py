"""SSRF / IMDS / credential-exfiltration egress policy (edge-input-hardening TASK.md §3 Part B
FROZEN @ v1).

Two layered checks guard every BYOK-influenced outbound URL (Azure ``endpoint``/``authority``,
the ONLY user-influenced outbound-URL class in this gateway — confirmed at Ground):

  1. ``assert_literal_host_not_denied`` — a SYNCHRONOUS, DNS-FREE literal-IP check performed
     at WRITE time (``PUT /admin/provider-keys/azure``). A hostname (non-IP-literal) always
     passes this check — DNS resolution is deliberately deferred to request time, so this is
     a cheap first filter, NOT the authoritative layer.
  2. ``EgressPolicy.check`` — an ASYNC, DNS-RESOLVING check performed FRESH on every single
     outbound dial (never cached from write time). This is the authoritative layer: it closes
     the "approved once at write time, exploited via a later DNS rebind" class the literal
     check cannot.

``EgressPolicy`` is a ``typing.Protocol`` (PROJECT.md: domain ports are Protocols with fakes
injected via the caller) so the existing ``127.0.0.1``-stub live-verify suites (``byok_verify``,
``azure_verify``) can inject an explicit ``AllowAllEgressPolicy`` fake WITHOUT weakening or
bypassing the real, deny-by-default production wiring — every BYOK-influenced adapter defaults
(``egress_policy=None``) to the real ``DenyPrivateAndMetadataEgressPolicy``; only an EXPLICIT
override relaxes it.

Security invariants:
  - Cloud-metadata addresses (169.254.169.254, 169.254.170.2, fd00:ec2::254) are ALWAYS
    denied — never toggle-able by any setting.
  - Loopback / link-local / RFC1918 / IPv6 ULA / multicast / reserved addresses are denied
    UNLESS the operator explicitly sets ``allow_private_ranges=True`` (Azure Private Link).
  - DNS resolution failure, timeout, or any resolver error fails CLOSED (deny) — the caller
    never proceeds with an unchecked/unresolved host (PROJECT.md: no outbound IO without a
    timeout).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

#: Cloud-metadata addresses — ALWAYS denied, regardless of allow_private_ranges.
_METADATA_ADDRESSES: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS IMDS (IPv6)
    }
)


class EgressDeniedError(Exception):
    """Raised by an ``EgressPolicy`` (or the literal write-time check) when a target host is
    not permitted. Carries a stable, non-secret ``reason`` string — never a credential."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@runtime_checkable
class DnsResolver(Protocol):
    """Port for hostname resolution — injectable so tests can pin/simulate DNS answers."""

    async def resolve(self, host: str) -> list[str]:
        """Return the list of resolved IP-literal strings for *host*."""
        ...


class _GetAddrInfoResolver:
    """Default resolver — real DNS via ``asyncio``'s non-blocking ``getaddrinfo``."""

    async def resolve(self, host: str) -> list[str]:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, None)
        return [str(info[4][0]) for info in infos]


@runtime_checkable
class EgressPolicy(Protocol):
    """Port checked before every BYOK-influenced outbound dial."""

    async def check(self, url: str) -> None:
        """Raise ``EgressDeniedError`` when *url* must not be dialed; otherwise return."""
        ...


def _is_denied_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private_ranges: bool
) -> bool:
    """Return True iff *ip* must be denied under the given policy.

    Metadata addresses are ALWAYS denied (unconditional). Loopback / link-local / private
    (RFC1918/ULA) / multicast / reserved / unspecified addresses are denied unless
    ``allow_private_ranges`` is set.
    """
    if str(ip) in _METADATA_ADDRESSES:
        return True
    if allow_private_ranges:
        return False
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_scheme(scheme: str, *, allow_http: bool) -> None:
    if scheme == "https":
        return
    if scheme == "http" and allow_http:
        return
    raise EgressDeniedError(f"scheme_not_permitted:{scheme or 'missing'}")


def assert_literal_host_not_denied(
    url: str,
    *,
    allow_private_ranges: bool = False,
    allow_http: bool = False,
) -> None:
    """Synchronous, DNS-free write-time check: deny an https/http URL whose HOST is a
    literal IP address in a denied range, or whose scheme is not permitted.

    A non-IP hostname (the common case — a real Azure resource name) ALWAYS passes this
    check; DNS resolution is deliberately deferred to the request-time ``EgressPolicy``
    check. This function performs NO network I/O.

    Raises:
        EgressDeniedError: the scheme is not permitted, or the host is a literal IP in a
            denied range (metadata unconditionally; private ranges unless opted in).
    """
    parts = urlsplit(url)
    _check_scheme(parts.scheme, allow_http=allow_http)

    host = parts.hostname
    if not host:
        raise EgressDeniedError("host_missing")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — a hostname. DNS resolution deferred to request time.
        return

    if _is_denied_ip(ip, allow_private_ranges=allow_private_ranges):
        raise EgressDeniedError(f"literal_ip_denied:{host}")


class DenyPrivateAndMetadataEgressPolicy:
    """Real, deny-by-default ``EgressPolicy`` — the production wiring's implicit default.

    Re-resolves the target hostname FRESH on every ``check()`` call (never cached) and
    denies metadata addresses unconditionally, plus loopback/link-local/private/ULA/
    multicast/reserved addresses unless ``allow_private_ranges`` is set. DNS resolution
    failure or timeout fails CLOSED (deny).
    """

    def __init__(
        self,
        *,
        allow_private_ranges: bool = False,
        allow_http: bool = False,
        resolve_timeout_s: float = 2.0,
        resolver: DnsResolver | None = None,
    ) -> None:
        self._allow_private_ranges = allow_private_ranges
        self._allow_http = allow_http
        self._resolve_timeout_s = resolve_timeout_s
        self._resolver: DnsResolver = resolver if resolver is not None else _GetAddrInfoResolver()

    async def check(self, url: str) -> None:
        parts = urlsplit(url)
        _check_scheme(parts.scheme, allow_http=self._allow_http)

        host = parts.hostname
        if not host:
            raise EgressDeniedError("host_missing")

        # A literal IP host needs no DNS — check it directly.
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            literal_ip = None

        if literal_ip is not None:
            if _is_denied_ip(literal_ip, allow_private_ranges=self._allow_private_ranges):
                raise EgressDeniedError(f"resolved_ip_denied:{host}")
            return

        # Hostname — resolve FRESH, every call. Never cached from a prior check.
        try:
            resolved = await asyncio.wait_for(
                self._resolver.resolve(host), timeout=self._resolve_timeout_s
            )
        except (TimeoutError, OSError, socket.gaierror):
            # FAIL CLOSED: a resolver error/timeout must never let an unchecked host
            # through — PROJECT.md's design-for-failure IO rule.
            raise EgressDeniedError(f"dns_resolution_failed:{host}") from None

        if not resolved:
            raise EgressDeniedError(f"dns_resolution_failed:{host}")

        for ip_str in resolved:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                raise EgressDeniedError(f"dns_resolution_failed:{host}") from None
            if _is_denied_ip(ip, allow_private_ranges=self._allow_private_ranges):
                raise EgressDeniedError(f"resolved_ip_denied:{host}->{ip_str}")


class AllowAllEgressPolicy:
    """TEST-ONLY no-op policy — NEVER wired by default in production.

    Injected explicitly by the ``byok_verify``/``azure_verify`` live-verify suites so their
    real ``127.0.0.1`` stub round-trips are unaffected by the deny-by-default production
    policy.
    """

    async def check(self, url: str) -> None:
        return


__all__ = [
    "AllowAllEgressPolicy",
    "DenyPrivateAndMetadataEgressPolicy",
    "DnsResolver",
    "EgressDeniedError",
    "EgressPolicy",
    "assert_literal_host_not_denied",
]
