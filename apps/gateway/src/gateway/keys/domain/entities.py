"""Domain entities for API keys — zero framework imports."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ApiKey:
    """Domain entity representing an issued API key (secret never stored here)."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    key_hash: str  # SHA-256 hex digest of the secret
    created_at: datetime
    revoked_at: datetime | None
    # Governance fields (all nullable — existing rows have None)
    monthly_budget_usd: Decimal | None = None
    soft_budget_usd: Decimal | None = None
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None
    rotated_from_key_id: uuid.UUID | None = None
    # Rate-limit fields (additive — rate-limits migration, nullable)
    rpm_limit: int | None = None
    tpm_limit: int | None = None


@dataclass(frozen=True, slots=True)
class ApiKeyInfo:
    """Projection for list responses — no hash or secret included."""

    key_id: uuid.UUID
    name: str
    prefix: str  # first 8 chars of "sk-<key_id_hex>" for UI display
    created_at: datetime
    revoked_at: datetime | None
    # Governance fields (all nullable)
    monthly_budget_usd: Decimal | None = None
    soft_budget_usd: Decimal | None = None
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None
    # Rate-limit fields (additive)
    rpm_limit: int | None = None
    tpm_limit: int | None = None


@dataclass(frozen=True, slots=True)
class AuthzResult:
    """Result returned by /internal/authz on success.

    Governance fields added additively (all default to None).
    Frozen v1 contract tests only assert on tenant_id and key_id — safe extension.
    """

    tenant_id: uuid.UUID
    key_id: uuid.UUID
    # Governance fields for hot-path enforcement (M8-M11)
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None
    monthly_budget_usd: Decimal | None = None
    soft_budget_usd: Decimal | None = None
    # Rate-limit fields (additive — rate-limits migration)
    rpm_limit: int | None = None
    tpm_limit: int | None = None
