"""Domain entities for API keys — zero framework imports."""

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ApiKey:
    """Domain entity representing an issued API key (secret never stored here)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    key_hash: str  # SHA-256 hex digest of the secret
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class ApiKeyInfo:
    """Projection for list responses — no hash or secret included."""

    key_id: uuid.UUID
    name: str
    prefix: str  # first 8 chars of "sk-<key_id_hex>" for UI display
    created_at: datetime
    revoked_at: datetime | None


@dataclass(frozen=True, slots=True)
class AuthzResult:
    """Result returned by /internal/authz on success."""

    tenant_id: uuid.UUID
    key_id: uuid.UUID
