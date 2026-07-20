"""GetRegistrarHintUseCase — infer a domain's DNS registrar from its NS records
(registrar-hint TASK.md §3 — FROZEN @ v1).

The ONLY place the M4 fail-open decision is made — the router and DnsNsResolver adapter
both stay dumb:
  1. normalize_domain FIRST (may raise DomainInvalidError; ZERO DNS IO on that path, M2).
  2. DnsNsResolver.lookup_ns, bounded by an explicit `asyncio.wait_for` wall-clock cap in
     ADDITION to the `timeout=` forwarded to the adapter (belt-and-suspenders design-for-
     failure, CLAUDE.md IO rule — this use case never trusts an adapter alone to enforce
     its own deadline; M3).
  3. ANY failure on that call (NsLookupFailedError, or a stray TimeoutError) degrades to
     the graceful `fallback: true` shape — NEVER propagated past this use case (M4).
  4. On success, `infer_registrar` (pure, zero-IO) decides match (M5, M11) vs. miss (M6) —
     both a miss and a failure produce the byte-identical fallback shape.

Zero DB IO (M12) — this use case takes no repository dependency at all.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from gateway.domain_capture.domain.domain_validation import normalize_domain
from gateway.domain_capture.domain.errors import NsLookupFailedError
from gateway.domain_capture.domain.ports import DnsNsResolver
from gateway.domain_capture.domain.registrar_map import infer_registrar


@dataclass(frozen=True, slots=True)
class RegistrarHintResult:
    domain: str
    registrar: str | None
    deep_link_url: str | None
    fallback: bool


def _fallback(domain: str) -> RegistrarHintResult:
    return RegistrarHintResult(domain=domain, registrar=None, deep_link_url=None, fallback=True)


class GetRegistrarHintUseCase:
    def __init__(self, dns_ns_resolver: DnsNsResolver, *, dns_timeout_seconds: float) -> None:
        self._dns_ns_resolver = dns_ns_resolver
        self._dns_timeout_seconds = dns_timeout_seconds

    async def execute(self, domain_raw: str) -> RegistrarHintResult:
        domain = normalize_domain(domain_raw)  # DomainInvalidError propagates (R1) — zero DNS IO

        try:
            nameservers = await asyncio.wait_for(
                self._dns_ns_resolver.lookup_ns(domain, timeout=self._dns_timeout_seconds),
                timeout=self._dns_timeout_seconds,
            )
        except (NsLookupFailedError, TimeoutError):
            # Fail-OPEN (M4, REQUIRED) — a timeout, NXDOMAIN, or any resolver error is
            # never a 5xx and never propagates past this use case.
            return _fallback(domain)

        hint = infer_registrar(nameservers)
        if hint is None:
            return _fallback(domain)  # a clean miss is byte-identical to a failure (M6)

        return RegistrarHintResult(
            domain=domain, registrar=hint.name, deep_link_url=hint.deep_link_url, fallback=False
        )
