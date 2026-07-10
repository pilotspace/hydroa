"""Domain entities for API keys — zero framework imports."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


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
    # Teams attribution (additive — teams-core migration, nullable)
    team_id: uuid.UUID | None = None
    # Team-governance additive field — populated via LEFT JOIN teams in get_by_id()
    team_budget_usd: Decimal | None = None
    # Response-caching additive field (response-caching migration)
    cache_enabled: bool = False
    # Guardrails-core additive field (guardrails-core migration)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    guardrail_configs: dict[str, Any] = field(default_factory=dict)
    # Semantic-cache additive field (semantic-cache migration)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    semantic_cache_enabled: bool = False
    # Batch-auto-grouping additive field (batch-auto-grouping migration, v57)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    batch_grouping_enabled: bool = False
    # Payload-capture-store additive field (payload-capture-store migration)
    # Raw key-level column value in create()/list_by_tenant(); OR-resolved with
    # tenants.payload_capture_enabled in get_by_id() (mirrors cache_enabled's dual role).
    capture_enabled: bool = False


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
    # Teams attribution (additive — teams-core migration, nullable)
    team_id: uuid.UUID | None = None
    # Response-caching additive field (response-caching migration)
    cache_enabled: bool = False


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
    # Team-governance additive fields (team-governance migration, nullable)
    # Populated via LEFT JOIN teams in get_by_id() — zero extra DB reads
    team_id: uuid.UUID | None = None
    team_budget_usd: Decimal | None = None
    # Response-caching additive field (response-caching migration)
    # Effective = api_keys.cache_enabled OR tenants.cache_enabled (resolved at auth time)
    cache_enabled: bool = False
    # Guardrails-core additive field (guardrails-core migration)
    # Populated at auth time from tenants.guardrail_configs via the existing LEFT JOIN tenants.
    # Empty dict = no guardrails configured (default).
    guardrail_configs: dict[str, Any] = field(default_factory=dict)
    # Semantic-cache additive field (semantic-cache migration)
    # Populated at auth time from tenants.semantic_cache_enabled via the existing LEFT JOIN tenants.
    # Default False = semantic layer inactive (per-tenant opt-in).
    semantic_cache_enabled: bool = False
    # Batch-auto-grouping additive field (batch-auto-grouping migration, v57)
    # Populated at auth time from tenants.batch_grouping_enabled via the existing LEFT JOIN tenants.
    # Default False = diversion inactive (per-tenant opt-in, M9 byte-identical guarantee).
    batch_grouping_enabled: bool = False
    # Payload-capture-store additive field (payload-capture-store migration)
    # Effective = api_keys.capture_enabled OR tenants.payload_capture_enabled (resolved at
    # auth time, mirrors cache_enabled's OR-resolution exactly — key can only turn ON).
    payload_capture_enabled: bool = False
