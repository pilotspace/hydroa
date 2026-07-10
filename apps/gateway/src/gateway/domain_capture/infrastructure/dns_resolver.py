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

from gateway.domain_capture.domain.errors import DnsLookupFailedError


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
