"""OIDC domain entities — OidcIdTokenClaims, DomainMapping, OidcProviderConfig.

(domain-routing-unification TASK.md §3 M4 — FROZEN @ v2/CR-v2: DomainMapping
is RETAINED. Env GATEWAY_OIDC_DOMAIN_MAPPING is a trusted OPERATOR-set routing
source consulted as a FALLBACK when no per-tenant DB config is pinned; a
verified tenant_domain_claims row always takes precedence when one exists.
v1 deleted this entity outright — CR-v2 (2026-07-18) reverted that deletion
after the wholesale removal broke ~23 legacy env-routing tests.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OidcIdTokenClaims:
    """Parsed claims extracted from an OIDC ID token (no signature verification in v4)."""

    sub: str
    email: str
    iss: str
    aud: str | list[str]  # aud can be a string or a list
    exp: int
    nonce: str | None  # may be absent in some IdPs; validated when present


@dataclass(frozen=True)
class DomainMapping:
    """Maps an email domain to a tenant UUID (operator-set via
    GATEWAY_OIDC_DOMAIN_MAPPING — a fallback consulted only when no
    per-tenant DB OIDC config is pinned; see module docstring)."""

    email_domain: str
    tenant_id: uuid.UUID


@dataclass(frozen=True)
class OidcProviderConfig:
    """Per-tenant OIDC IdP configuration.

    SECURITY INVARIANT: client_secret is PLAINTEXT here (domain layer only).
    It is NEVER persisted directly; the infrastructure layer Fernet-encrypts
    it before any INSERT/UPDATE. It is NEVER serialized to JSON or logged.
    """

    tenant_id: uuid.UUID
    issuer: str
    client_id: str
    client_secret: str  # PLAINTEXT; in-memory only; NEVER serialized or logged
    authorize_url: str  # empty string = derive from issuer + "/authorize"
    token_url: str
    jwks_url: str
    email_domains: list[str] = field(default_factory=list)
    enabled: bool = True
