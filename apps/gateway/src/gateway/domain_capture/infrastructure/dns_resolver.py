"""DnsPythonTxtResolver — real DNS TXT lookup adapter (domain-capture TASK.md §3 M13 —
FROZEN @ v1).

Design-for-failure (CLAUDE.md IO rule): a NEW egress IO seam. ONE attempt, bounded
`lifetime` timeout passed straight to dnspython, fail-CLOSED — ANY `dns.exception.
DNSException` (covers NXDOMAIN, NoAnswer, Timeout/LifetimeTimeout, and every other
resolver-error subclass) is caught and re-raised as the domain-level DnsLookupFailedError;
never silently treated as "no record" success. No internal retry loop — the human
re-clicking "Verify" IS the retry mechanism (appropriate for a low-volume, human-triggered
admin action, not a background job).
"""

from __future__ import annotations

from gateway.domain_capture.domain.errors import DnsLookupFailedError, NsLookupFailedError


class DnsPythonTxtResolver:
    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — forwarded verbatim to dnspython's own `lifetime=` deadline parameter
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "TXT", lifetime=timeout)
        except dns.exception.DNSException as exc:
            raise DnsLookupFailedError(f"DNS TXT lookup failed for {name!r}: {exc}") from exc

        values: list[str] = []
        for rdata in answer:
            strings: tuple[bytes, ...] = getattr(rdata, "strings", ())
            values.append(b"".join(strings).decode("utf-8", errors="replace"))
        return values


class DnsPythonNsResolver:
    """Real DNS NS-record lookup adapter (registrar-hint TASK.md §3 M3 — FROZEN @ v1).

    Added ALONGSIDE DnsPythonTxtResolver, which stays byte-unchanged (M10) — the SAME
    dnspython call shape already proven above, swapping the "TXT" rdtype for "NS". ONE
    attempt, bounded `lifetime` timeout, no internal retry — a best-effort UI-convenience
    lookup, not the verification-blocking TXT check, so failures raise the DISTINCT
    NsLookupFailedError (fail-OPEN contract, handled by the use case) rather than reusing
    DnsLookupFailedError (fail-CLOSED, used by TXT verification only).
    """

    async def lookup_ns(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — forwarded verbatim to dnspython's own `lifetime=` deadline parameter
        import dns.asyncresolver
        import dns.exception

        try:
            answer = await dns.asyncresolver.resolve(name, "NS", lifetime=timeout)
        except dns.exception.DNSException as exc:
            raise NsLookupFailedError(f"DNS NS lookup failed for {name!r}: {exc}") from exc

        return [str(rdata.target).rstrip(".") for rdata in answer]
