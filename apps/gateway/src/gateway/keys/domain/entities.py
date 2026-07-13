"""Domain entities for API keys — zero framework imports."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal


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
    # per-key-guardrail-policies (M1-M3): now the RESOLVED value — key.guardrail_policy
    # when non-NULL (wholesale override, tenant never consulted), else tenant.guardrail_configs
    # byte-identically to pre-task behavior.
    guardrail_configs: dict[str, Any] = field(default_factory=dict)
    # Semantic-cache additive field (semantic-cache migration)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    semantic_cache_enabled: bool = False
    # Batch-auto-grouping additive field (batch-auto-grouping migration, v57)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    batch_grouping_enabled: bool = False
    # Per-key-guardrail-policies additive field: which layer the RESOLVED
    # guardrail_configs above actually came from — "key" (non-NULL override),
    # "tenant" (inherited, tenant has a non-empty config), or "none" (neither
    # configured). Populated in get_by_id() at zero extra DB cost; threaded onward
    # into AuthzResult.policy_source for the sibling guardrail-analytics task.
    guardrail_policy_source: Literal["key", "tenant", "none"] = "none"
    # tenant-retention-zdr additive field (tenant-retention-zdr TASK.md §3, FROZEN @ v1)
    # Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads.
    zdr_enabled: bool = False
    # Payload-capture-store additive field (payload-capture-store migration)
    # Raw key-level column value in create()/list_by_tenant(); OR-resolved with
    # tenants.payload_capture_enabled in get_by_id() (mirrors cache_enabled's dual role).
    capture_enabled: bool = False
    # plan-enforcement additive fields (TASK.md §3, FROZEN @ v1) — populated via a 4th
    # outerjoin(PlanRow, TenantRow.plan_id == PlanRow.id) in get_by_id(), zero extra DB
    # reads. plan_id None = unplanned (M7, grandfathered). plan_model_allowlist mirrors
    # model_allowlist's own null=all-models convention. plan_name feeds M9's upgrade_hint.
    plan_id: uuid.UUID | None = None
    plan_model_allowlist: list[str] | None = None
    plan_name: str | None = None
    # service-tiers additive field (TASK.md §3, FROZEN @ v1) — DUAL ROLE, mirrors
    # cache_enabled/capture_enabled's own dual convention: the RAW key-level
    # override value (NULL = inherit tenant default) in create()/update()/
    # list_by_tenant(); the RESOLVED effective tier in get_by_id() (zero extra DB
    # reads, via the existing LEFT JOIN tenants — M1).
    tier: Literal["priority", "standard"] | None = None
    # Populated ONLY by get_by_id() (mirrors guardrail_policy_source's own
    # get_by_id()-only population) — which layer the resolved `tier` above came
    # from. Meaningless (default "tenant") on the create()/update() raw-override role.
    tier_source: Literal["key", "tenant"] = "tenant"


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
    # service-tiers additive field (TASK.md §3, FROZEN @ v1) — raw key-level override.
    tier: Literal["priority", "standard"] | None = None


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
    # Per-key-guardrail-policies additive field (freeze question #3, decided: add now).
    # Mirrors ApiKey.guardrail_policy_source — which layer guardrail_configs above
    # resolved from. Default "none" preserves byte-identical construction for every
    # existing AuthzResult(...) call site that predates this task.
    policy_source: Literal["key", "tenant", "none"] = "none"
    # tenant-retention-zdr additive field (tenant-retention-zdr TASK.md §3, FROZEN @ v1)
    # Populated at auth time from tenants.zdr_enabled via the existing LEFT JOIN tenants.
    # Default False = ZDR inactive. M5's five gated repositories re-check this FRESH per
    # call (gateway.tenants.application.retention_policy.raise_if_zdr) rather than trust
    # this value alone — this field is used for M6 (cache-write skip), where the same
    # per-request freshness the LEFT JOIN already provides is sufficient.
    zdr_enabled: bool = False
    # Payload-capture-store additive field (payload-capture-store migration)
    # Effective = api_keys.capture_enabled OR tenants.payload_capture_enabled (resolved at
    # auth time, mirrors cache_enabled's OR-resolution exactly — key can only turn ON).
    payload_capture_enabled: bool = False
    # plan-enforcement additive fields (TASK.md §3, FROZEN @ v1) — populated at auth time
    # via the existing 3-table LEFT JOIN, extended with a 4th outerjoin to `plans`. Zero
    # extra DB reads. plan_id None = unplanned (M7, grandfathered-unlimited).
    # plan_model_allowlist mirrors model_allowlist's own null=all-models convention (M4).
    # plan_name feeds M9's structured upgrade_hint.
    plan_id: uuid.UUID | None = None
    plan_model_allowlist: list[str] | None = None
    plan_name: str | None = None
    # service-tiers additive fields (TASK.md §3, FROZEN @ v1) — resolved at auth time:
    # tier = api_keys.tier if not NULL else tenants.default_tier (the EXISTING LEFT
    # JOIN tenants in ApiKeyRepository.get_by_id — zero extra DB reads, M1).
    # tier_source mirrors policy_source's key/tenant discriminator shape.
    tier: Literal["priority", "standard"] = "standard"
    tier_source: Literal["key", "tenant"] = "tenant"
