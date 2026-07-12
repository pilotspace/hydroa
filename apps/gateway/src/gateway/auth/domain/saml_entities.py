"""SAML domain entities — SamlProviderConfig, PendingSamlRequest, SamlAssertionClaims.

Mirrors auth/domain/entities.py's OIDC shapes for the SAML parallel vertical
(saml-sso TASK.md §1 Framings — new files only, zero edits to OIDC files).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SamlProviderConfig:
    """Per-tenant SAML IdP configuration (manual entry — TASK.md §1 Framings).

    sp_entity_id / acs_url are NEVER persisted (TASK.md §3 Part C note) — always
    computed at read time from Settings + tenant_id (M2: server-derived, never
    admin-settable, the structural tenant-isolation control — Ground R6).
    """

    tenant_id: uuid.UUID
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str  # PEM, public material — NOT a secret, no Fernet encryption
    sp_entity_id: str  # server-derived: f"{base}/tenant/{tenant_id}"
    acs_url: str  # server-derived: settings.saml_acs_url
    email_domains: list[str] = field(default_factory=list)
    email_attribute_name: str = "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
    enabled: bool = True


@dataclass(frozen=True)
class PendingSamlRequest:
    """Server-side tenant-binding record (TASK.md §3 Part D `saml:pending:{request_id}`).

    The ACS endpoint resolves tenant identity EXCLUSIVELY through this record —
    never a cookie (Ground R1: SameSite=Lax does not survive a cross-site POST)
    and never any unverified field inside the SAMLResponse (M3).
    """

    tenant_id: uuid.UUID
    sp_entity_id: str
    idp_entity_id: str
    created_at: str  # ISO8601 — informational only, TTL is enforced by Redis


@dataclass(frozen=True)
class SamlAssertionClaims:
    """Resolved, validated claims extracted from a fully-verified SAML assertion.

    Constructed ONLY after the full M5 validation set has passed — every field
    here is trustworthy (extracted from the SAME node the signature covers, per
    M4's XSW defense; TASK.md §0 Ground R2).
    """

    assertion_id: str
    name_id: str | None
    name_id_format: str | None
    attributes: dict[str, list[str]]
    not_on_or_after: str  # ISO8601, from Conditions — used for the M5.6 replay-cache TTL
