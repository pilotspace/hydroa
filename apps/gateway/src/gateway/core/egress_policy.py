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
  - Loopback / link-local / multicast / reserved / unspecified addresses are ALWAYS denied
    too — ``allow_private_ranges`` does NOT relax them (an IMDS IP is link-local; NAT64 and
    other transition encodings are is_reserved — keeping these classes denied under the opt-in
    closes that bypass class wholesale).
  - ``allow_private_ranges=True`` relaxes ONLY a positive allow-list of the ranges Azure
    Private Link actually uses — RFC1918 (10/8, 172.16/12, 192.168/16) and IPv6-ULA
    (fc00::/7). Every OTHER private/special address (Teredo 2001::/32, 6to4 2002::/16, discard
    100::/64 — all ``is_private`` in Python) stays denied even under the opt-in, so no IPv6
    transition scheme that Python happens to bucket as private can tunnel a metadata IP past it.
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
#: Parsed to ``ipaddress`` objects (NOT compared as raw strings) so an alternate textual
#: encoding of the SAME numeric address (e.g. an IPv4-mapped IPv6 literal) cannot slip past
#: an exact-string match — see ``_embedded_ipv4`` / ``_is_denied_ip``.
_METADATA_IPS: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] = frozenset(
    ipaddress.ip_address(addr)
    for addr in (
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "169.254.170.2",  # AWS ECS task metadata
        "fd00:ec2::254",  # AWS IMDS (IPv6)
    )
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


#: RFC 6052 well-known NAT64 prefix — ``64:ff9b::/96`` embeds an IPv4 address in its low
#: 32 bits. Python's ``ipaddress`` flags the whole prefix ``is_reserved`` (so the class-based
#: deny below already catches it), but we ALSO normalise it to the embedded IPv4 so the
#: unconditional metadata exact-match sees it even if that predicate ever changes.
_NAT64_WELL_KNOWN = ipaddress.ip_network("64:ff9b::/96")

#: The ONLY address ranges ``allow_private_ranges=True`` (Azure Private Link) relaxes — a
#: POSITIVE allow-list, deliberately NOT "everything Python calls ``is_private``". Python's
#: ``is_private`` bucket also contains IANA special-purpose prefixes that are NOT legitimate
#: Private Link targets and that can embed/tunnel a metadata IPv4 — Teredo ``2001::/32``, 6to4
#: ``2002::/16``, the discard prefix ``100::/64`` — so relaxing all of ``is_private`` reopens
#: the metadata-egress bypass class one transition scheme at a time. Enumerating exactly what
#: Private Link legitimately uses (RFC1918 + IPv6-ULA) and denying every other private/special
#: address under the opt-in closes that whole class at once.
_PRIVATE_LINK_ALLOWED: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),  # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),  # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),  # RFC1918
    ipaddress.ip_network("fc00::/7"),  # IPv6 ULA (fc00::/8 + fd00::/8)
)


def _in_private_link_allowlist(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """True iff *ip* is in a range ``allow_private_ranges`` legitimately relaxes (RFC1918/ULA).

    The version guard is required: ``IPv6Address in IPv4Network`` raises ``TypeError``. An
    IPv4-mapped-RFC1918 literal is matched via its embedded-IPv4 candidate (see ``_is_denied_ip``),
    not its v6 spelling, so this deliberately checks only same-version membership.
    """
    return any(ip.version == net.version and ip in net for net in _PRIVATE_LINK_ALLOWED)


def _embedded_ipv4(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | None:
    """Return the IPv4 address embedded in an IPv6 IPv4-mapped / IPv4-compatible / 6to4 /
    NAT64-well-known literal, else None.

    A denied IPv4 address (metadata, RFC1918, loopback, …) has several equivalent IPv6
    textual encodings — ``::ffff:169.254.169.254`` (IPv4-mapped), ``::169.254.169.254``
    (IPv4-compatible, deprecated), ``2002:a9fe:a9fe::`` (6to4), ``64:ff9b::a9fe:a9fe``
    (RFC 6052 NAT64) — that ``str()`` differently and whose IPv6 range predicates do not
    always flag them. Collapsing to the embedded IPv4 lets the unconditional metadata
    exact-match cover the whole representation class instead of one literal spelling.

    Note: this normalisation is a BELT-AND-SUSPENDERS layer for the metadata exact-set. The
    authoritative defence against transition-scheme encodings is ``_is_denied_ip`` denying the
    link-local / reserved / multicast classes unconditionally AND relaxing only a positive
    RFC1918/ULA allow-list under ``allow_private_ranges`` (so Teredo ``2001::/32``, 6to4
    ``2002::/16``, discard ``100::/64`` — all ``is_private`` in Python — stay denied under the
    opt-in). Residual NOT closable by address inspection alone: an operator-configured RFC 8215
    local NAT64 prefix drawn from ULA space (e.g. ``fd00:...``) is indistinguishable from a
    legitimate Private Link ULA target; if such NAT64 infra is ever deployed on the egress
    path it must be denied at the network layer or via an explicit prefix deny-list.
    """
    if not isinstance(ip, ipaddress.IPv6Address):
        return None
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip.sixtofour is not None:
        return ip.sixtofour
    if ip in _NAT64_WELL_KNOWN:
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    # IPv4-compatible ``::a.b.c.d`` (deprecated): high 96 bits zero, low 32 bits the IPv4.
    # Guard ``> 0xFFFF`` so ``::``/``::1``/``::ffff`` are left to the native IPv6 predicates
    # (loopback/unspecified) rather than mis-normalised.
    as_int = int(ip)
    if as_int >> 32 == 0 and as_int > 0xFFFF:
        return ipaddress.IPv4Address(as_int & 0xFFFFFFFF)
    return None


def _is_denied_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private_ranges: bool
) -> bool:
    """Return True iff *ip* must be denied under the given policy.

    Metadata addresses are ALWAYS denied (unconditional). So are the link-local / loopback /
    multicast / reserved / unspecified address CLASSES — even when ``allow_private_ranges`` is
    set. That handles the reserved-class transition encodings (e.g. NAT64 ``64:ff9b::/96`` is
    ``is_reserved``, IPv4-mapped metadata embeds a ``is_link_local`` IPv4).

    For the remaining ``is_private`` bucket, ``allow_private_ranges`` relaxes ONLY a POSITIVE
    allow-list (RFC1918 + IPv6-ULA — exactly what Azure Private Link uses); every OTHER private
    address stays denied under the opt-in. This is deliberate: Python's ``is_private`` also
    contains Teredo ``2001::/32``, 6to4 ``2002::/16`` and the discard prefix ``100::/64``, none
    of which are Private Link targets and any of which can tunnel/embed a metadata IPv4 — so a
    blanket "relax all ``is_private``" would reopen the bypass one transition scheme at a time.

    Public (globally-routable) addresses are the normal egress case and are always allowed.
    Every predicate is evaluated over BOTH the address as given AND any IPv4 it embeds via an
    IPv6 transition encoding (``_embedded_ipv4``), so a denied address cannot hide behind an
    alternate representation.
    """
    candidates: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
    embedded = _embedded_ipv4(ip)
    if embedded is not None:
        candidates.append(embedded)

    # Metadata is ALWAYS denied — check every representation, never toggle-able.
    if any(candidate in _METADATA_IPS for candidate in candidates):
        return True
    # These CLASSES are never a legitimate Private Link target — denied even under the opt-in.
    if any(
        candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_multicast
        or candidate.is_reserved
        or candidate.is_unspecified
        for candidate in candidates
    ):
        return True
    if allow_private_ranges:
        # The RFC1918/ULA allow-list rescue applies ONLY to an address that legitimately NAMES
        # a private host: the address itself, or its CANONICAL IPv4-mapped form
        # (``::ffff:a.b.c.d``). A tunnelling/transition encoding (6to4 ``2002::/16``, NAT64,
        # IPv4-compatible) is NOT a spelling of the embedded host — dialing it routes via tunnel
        # infrastructure to somewhere ELSE — so it must NOT earn the rescue off its embedded
        # IPv4. Those forms therefore fall through to the ``is_private`` deny below (6to4 is
        # ``is_private``; NAT64/compatible are already denied above as ``is_reserved``). Using
        # the full ``_embedded_ipv4`` set here would let an attacker 6to4-wrap an RFC1918 IP to
        # slip past the opt-in — the exact class this task has repeatedly had to close.
        rescue: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = [ip]
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            rescue.append(ip.ipv4_mapped)
        if any(_in_private_link_allowlist(candidate) for candidate in rescue):
            return False
        return any(candidate.is_private for candidate in candidates)
    return any(candidate.is_private for candidate in candidates)


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
