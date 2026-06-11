"""OIDC domain entities — OidcIdTokenClaims, DomainMapping."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


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
    """Maps an email domain to a tenant UUID."""

    email_domain: str
    tenant_id: uuid.UUID
