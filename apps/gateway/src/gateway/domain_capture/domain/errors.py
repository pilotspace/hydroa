"""domain-capture domain errors (domain-capture TASK.md §3 — FROZEN @ v1).

Zero framework imports — the api/ layer translates each of these to an ErrorSpec.exc().
"""

from __future__ import annotations


class DomainCaptureError(Exception):
    """Base for domain-capture domain failures."""


class DomainInvalidError(DomainCaptureError):
    """Malformed / single-label / IP-literal domain string (M3, R1)."""


class DomainAlreadyVerifiedError(DomainCaptureError):
    """A DIFFERENT tenant already holds a verified claim on this domain — used both as the
    M4 UX pre-check rejection (R2) AND the M6 cross-tenant verify-race rejection (R7)."""


class DomainClaimNotFoundError(DomainCaptureError):
    """Unknown claim_id OR a claim_id belonging to a different tenant — deliberately
    indistinguishable (R9), mirrors InviteNotFoundError's own docstring precedent."""


class DomainClaimExpiredError(DomainCaptureError):
    """now > claim.expires_at at verify time (R6) — caller must re-POST to reissue."""


class DomainVerificationFailedError(DomainCaptureError):
    """DNS TXT record missing or did not match this claim's own stored token (R5)."""


class DnsLookupFailedError(DomainCaptureError):
    """DNS resolver error, NXDOMAIN, empty answer, or timeout — fail CLOSED, never treated
    as a verified match (M13, R8)."""


class DomainClaimRateLimitedError(DomainCaptureError):
    """Per-tenant rate limit exceeded on claim-create or claim-verify (M14, R10)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"domain claim rate limited; retry after {retry_after}s")
        self.retry_after = retry_after


class NsLookupFailedError(DomainCaptureError):
    """NS-record resolver error, NXDOMAIN, empty answer, or timeout for the registrar-hint
    endpoint (registrar-hint TASK.md §3 M4 — FROZEN @ v1). Deliberately a DISTINCT class
    from DnsLookupFailedError: THIS error fails OPEN — GetRegistrarHintUseCase catches it
    into a graceful `fallback: true` response, never a 5xx — the opposite discipline from
    DnsLookupFailedError's fail-CLOSED contract, kept as a separate class so the two
    handling rules can never be confused at some future call site."""
