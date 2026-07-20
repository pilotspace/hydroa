"""domain-capture Protocol ports (domain-capture TASK.md §3 — FROZEN @ v1).

Zero framework imports (backend-architect discipline, §0 Honors — mirrors
tenants/domain/ports.py's zero-infra-import shape exactly).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from gateway.domain_capture.domain.entities import DomainClaim


class DomainClaimRepository(Protocol):
    async def create_or_reissue(
        self,
        *,
        tenant_id: uuid.UUID,
        domain: str,
        verification_token: str,
        expires_at: datetime,
        created_by_user_id: uuid.UUID,
    ) -> DomainClaim:
        """Create a NEW pending claim, or reissue (fresh token+expiry) an existing
        NOT-YET-verified row for (tenant_id, domain) — never duplicates a row, never
        regresses an already-verified row back to pending (M2)."""
        ...

    async def list_for_tenant(self, tenant_id: uuid.UUID) -> list[DomainClaim]:
        """Tenant-scoped list, newest first — never a cross-tenant row (M5)."""
        ...

    async def get_own(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim | None:
        """Tenant-scoped lookup — unknown id and cross-tenant id are indistinguishable
        (both return None) — the caller raises DomainClaimNotFoundError (R9)."""
        ...

    async def mark_verified(self, *, claim_id: uuid.UUID) -> DomainClaim:
        """Atomically flip status: pending -> verified (one UPDATE ... WHERE
        status='pending'). Raises DomainAlreadyVerifiedError if a DIFFERENT tenant's
        claim on the same domain won the race first (M1's partial unique index, R7);
        raises DomainClaimNotFoundError if the row no longer exists."""
        ...

    async def revoke(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> bool:
        """Delete a tenant-owned claim in ANY status. Returns True iff a row was
        deleted — False (unknown id OR cross-tenant) is the caller's cue to raise
        DomainClaimNotFoundError (R9)."""
        ...

    async def has_verified_claim_by_other_tenant(
        self, *, domain: str, tenant_id: uuid.UUID
    ) -> bool:
        """M4 UX pre-check — True iff a DIFFERENT tenant already holds a verified claim
        on `domain`. NOT the structural defense (that is M1's partial unique index) —
        just an early, friendlier 409 before any write is attempted."""
        ...

    # ── domain-verify-notify TASK.md §3 (FROZEN @ v1, SECURITY) — additive ──────────

    async def request_notify(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> DomainClaim:
        """Set notify_requested_at (idempotent — a repeat opt-in preserves the ORIGINAL
        timestamp, a true no-op). Tenant-scoped; raises DomainClaimNotFoundError if the
        row does not exist for this tenant (defensive — the caller already checked via
        get_own before calling this)."""
        ...

    async def clear_notify(self, *, claim_id: uuid.UUID, tenant_id: uuid.UUID) -> None:
        """Clear notify_requested_at (idempotent — a repeat opt-out is a no-op).
        Tenant-scoped."""
        ...

    async def mark_notified(self, *, claim_id: uuid.UUID) -> bool:
        """Atomic conditional claim: ONE `UPDATE ... SET notified_at=now() WHERE id=:id
        AND notified_at IS NULL RETURNING id`. Returns True iff THIS call won the claim
        (the caller — and only the caller — that gets True may dispatch the email);
        False means another caller already claimed it — the exactly-once-email guard
        (R-sec-3), safe under overlapping ticks/replicas regardless of replica count."""
        ...

    async def list_notify_candidates(self, now: datetime) -> list[DomainClaim]:
        """The bounded scheduler input: claims where notify_requested_at IS NOT NULL AND
        status='pending' AND notified_at IS NULL AND expires_at > now. Naturally bounded
        (R-sec-4) — no claim stays a candidate forever; expiry is the ceiling."""
        ...


class DomainClaimResolver(Protocol):
    async def resolve_verified_tenant(self, domain: str) -> uuid.UUID | None:
        """Indexed point lookup: the tenant_id holding a VERIFIED claim on `domain`, or
        None if no verified claim exists (M8) — a pending/expired/revoked claim NEVER
        matches (M15)."""
        ...


class DnsTxtResolver(Protocol):
    async def lookup_txt(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — dnspython's own `lifetime=` deadline parameter, forwarded verbatim
        """ONE bounded-timeout DNS TXT lookup for `name`. Raises DnsLookupFailedError on
        ANY resolver error, NXDOMAIN, empty answer, or timeout — fails CLOSED, never
        returns a partial/best-effort result (M13, R8). No internal retry."""
        ...


class DnsNsResolver(Protocol):
    async def lookup_ns(self, name: str, *, timeout: float) -> list[str]:  # noqa: ASYNC109 — mirrors DnsTxtResolver's own forwarded `lifetime=` deadline parameter
        """ONE bounded-timeout DNS NS-record lookup for `name` (registrar-hint TASK.md §3
        M3 — FROZEN @ v1). Raises NsLookupFailedError on ANY resolver error, NXDOMAIN,
        empty answer, or timeout. Deliberately mirrors DnsTxtResolver's shape (bounded
        timeout, no retry, one Protocol method) WITHOUT its fail-CLOSED discipline — the
        CALLER (GetRegistrarHintUseCase) is responsible for catching NsLookupFailedError
        into a graceful fallback (M4); this port itself still just raises on failure."""
        ...
