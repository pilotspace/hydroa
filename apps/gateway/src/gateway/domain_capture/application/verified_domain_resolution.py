"""The ONE email-domain -> tenant routing predicate (domain-routing-unification
TASK.md §3 — FROZEN @ v1, M1/M8 + §5 safety rule).

Every surface that answers "which tenant owns this email domain?" — OIDC
login-init, SAML login-init, and BOTH admin-write gates (PUT /admin/oidc,
PUT /admin/saml) — calls THIS helper, which composes the two frozen anchors:

  normalize_domain (domain_capture/domain/domain_validation.py)
    -> DomainClaimResolver.resolve_verified_tenant (domain_capture/domain/ports.py)

so the login-time resolve and the write-time gate can never silently drift
apart (the exact two-divergent-checks failure mode §5's safety rule forbids).
Password signup already routes through resolve_verified_tenant directly on an
already-normalized domain (tenants/api/router.py) — same predicate, same impl.

A raw domain that fails normalize_domain (malformed, single-label, IP literal,
over-long) resolves to None — fail closed (M8: exact + case-normalized match
only; garbage input can never route a tenant, and never raises out of here).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from gateway.domain_capture.domain.domain_validation import normalize_domain
from gateway.domain_capture.domain.errors import DomainInvalidError

if TYPE_CHECKING:
    from gateway.domain_capture.domain.ports import DomainClaimResolver


async def resolve_verified_tenant_for_raw_domain(
    resolver: DomainClaimResolver, raw_domain: str
) -> uuid.UUID | None:
    """Return the tenant_id holding a VERIFIED claim on the normalized form of
    ``raw_domain``, else None (no verified claim, or the raw value is not a
    valid claimable hostname). Never raises. Exact-match only — a subdomain
    never matches its parent (M8)."""
    try:
        normalized = normalize_domain(raw_domain)
    except DomainInvalidError:
        return None
    return await resolver.resolve_verified_tenant(normalized)
