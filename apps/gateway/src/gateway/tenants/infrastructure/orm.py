import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class PlanRow(Base):
    """plans — the tier catalog (plan-catalog TASK.md §3, FROZEN @ v1).

    Seeded via migration with exactly 3 rows (starter/team/enterprise) — no application
    code path creates/edits/deletes a row in v1 (superadmin tier-DEFINITION CRUD is an
    explicit non-goal). `name` is deliberately NOT a CHECK-constrained value set — the
    whole point of a table over an enum is that a 4th tier needs no migration.
    """

    __tablename__ = "plans"
    __table_args__ = (
        CheckConstraint("seat_cap IS NULL OR seat_cap > 0", name="ck_plans_seat_cap_positive"),
        CheckConstraint(
            "budget_usd_monthly_default IS NULL OR budget_usd_monthly_default > 0",
            name="ck_plans_budget_default_positive",
        ),
        CheckConstraint(
            "rpm_limit_default IS NULL OR rpm_limit_default > 0",
            name="ck_plans_rpm_default_positive",
        ),
        CheckConstraint(
            "tpm_limit_default IS NULL OR tpm_limit_default > 0",
            name="ck_plans_tpm_default_positive",
        ),
        # seat-billing TASK.md §3 (FROZEN @ v2) — additive.
        CheckConstraint(
            "seat_price_usd_monthly IS NULL OR seat_price_usd_monthly > 0",
            name="ck_plans_seat_price_positive",
        ),
        # plan-tiers-and-base-fee TASK.md §3 (FROZEN @ v1) — additive, mirrors
        # ck_plans_seat_price_positive exactly.
        CheckConstraint(
            "base_price_usd_monthly IS NULL OR base_price_usd_monthly > 0",
            name="ck_plans_base_price_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    seat_cap: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    budget_usd_monthly_default: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    rpm_limit_default: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tpm_limit_default: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # plan-enforcement TASK.md §3 (FROZEN @ v1) — additive. NULL = no plan-level model
    # restriction (mirrors ApiKeyRow.model_allowlist's own null=all-models convention).
    model_allowlist: Mapped[list[str] | None] = mapped_column(sa.JSON, nullable=True, default=None)
    # plan-enforcement TASK.md §3 (FROZEN @ v1) — additive. NOT NULL DEFAULT '[]': array of
    # feature-key strings this plan tier grants (e.g. "batch", "ml_moderation",
    # "logs_explorer", "realtime"). Migration-seeded only — no runtime plans-row CRUD.
    feature_flags: Mapped[list[str]] = mapped_column(
        sa.JSON, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    # seat-billing TASK.md §3 (FROZEN @ v2, M2) — additive. NULL = no seat pricing
    # (inert — this task writes ZERO 'seat'/'proration' invoice lines for a tenant whose
    # plan has this NULL or 0). Migration-seeded only (team=$15.00, enterprise=$40.00,
    # starter=NULL) — no runtime plans-row CRUD, mirrors every other `plans.*_default`
    # column's nullable/no-runtime-CRUD convention.
    seat_price_usd_monthly: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    # plan-tiers-and-base-fee TASK.md §3 (FROZEN @ v1, M1/M2) — additive. NULL = no flat
    # base fee (inert — InvoiceGenerator's `_load_base_price` writes ZERO 'base' invoice
    # lines for a tenant whose plan has this NULL, e.g. enterprise/unplanned). Migration-
    # seeded only (free=NULL, starter=1.00, pro=20.00, team=99.00, enterprise=NULL) — no
    # runtime plans-row CRUD, mirrors seat_price_usd_monthly's own nullable/no-runtime-CRUD
    # convention exactly.
    base_price_usd_monthly: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    # self-serve-checkout TASK.md §3 (FROZEN @ v1, I2/A3) — additive, migration-seeded only.
    # NOT NULL DEFAULT false: "enterprise = contact sales" is data-driven, NOT the ambiguous
    # base_price IS NULL test (which is ALSO true for free). Seed: free/starter/pro/team=true,
    # enterprise=false. Mirrors feature_flags' NOT-NULL-with-server_default additive shape.
    self_serve: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # self-serve-checkout TASK.md §3 (FROZEN @ v1, I3/A1) — additive, migration-seeded only.
    # NULL = no audience gate (defensive default). Seed: free/starter/pro='personal',
    # team/enterprise='business'. A personal tenant self-selecting a business-audience plan
    # is rejected (plan_account_type_mismatch) — the personal/business split becomes
    # data-driven instead of seed-convention-only. Mirrors account_type's nullable-text shape.
    audience: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # TIMESTAMPTZ per §3 Schema — a NEW table gets the tz-aware convention (mirrors
    # InviteRow's own explicit DateTime(timezone=True), NOT TenantRow/UserRow's older
    # bare-Mapped[datetime] style). No onupdate — inert in v1 (no write path updates a
    # `plans` row after seeding); kept for a future tier-CRUD endpoint.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantRow(Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("kind IN ('customer', 'platform')", name="ck_tenants_kind"),
        Index(
            "tenants_platform_kind_uidx",
            "kind",
            unique=True,
            postgresql_where=text("kind = 'platform'"),
        ),
        # plan-catalog TASK.md §3 (FROZEN @ v1) — additive.
        CheckConstraint("seat_cap IS NULL OR seat_cap > 0", name="ck_tenants_seat_cap_positive"),
        # Defense-in-depth (M3/R8): holds even if application code were bypassed —
        # mirrors this same table's own ck_tenants_kind/tenants_platform_kind_uidx precedent.
        CheckConstraint(
            "plan_id IS NULL OR kind != 'platform'", name="ck_tenants_platform_no_plan"
        ),
        # account-type-discriminator TASK.md §3 (FROZEN @ v1, M1/R2) — additive. The
        # personal|business flavor of a customer tenant; NULL on the reserved platform
        # tenant (defense-in-depth: platform can never carry an account_type, mirroring
        # ck_tenants_platform_no_plan above).
        CheckConstraint(
            "account_type IS NULL OR account_type IN ('personal', 'business')",
            name="ck_tenants_account_type",
        ),
        CheckConstraint(
            "account_type IS NULL OR kind != 'platform'",
            name="ck_tenants_platform_no_account_type",
        ),
        # service-tiers TASK.md §3 (FROZEN @ v1) — additive.
        CheckConstraint("default_tier IN ('priority', 'standard')", name="ck_tenants_default_tier"),
        # audit-remediation C3 (double-bill fix, 2026-07-14) — additive.
        CheckConstraint("billing_mode IN ('invoice', 'credits')", name="ck_tenants_billing_mode"),
        # plan-rate-enforcement TASK.md §3 (FROZEN @ v1, M0) — additive.
        CheckConstraint("rpm_limit IS NULL OR rpm_limit > 0", name="ck_tenants_rpm_limit_positive"),
        CheckConstraint("tpm_limit IS NULL OR tpm_limit > 0", name="ck_tenants_tpm_limit_positive"),
        # billing-owner-of-record TASK.md §3 (FROZEN @ v1, M1) — additive. Defense-in-depth
        # (mirrors ck_tenants_platform_no_plan / ck_tenants_platform_no_account_type): the
        # reserved platform tenant can never carry a billing owner.
        CheckConstraint(
            "billing_owner_user_id IS NULL OR kind != 'platform'",
            name="ck_tenants_platform_no_billing_owner",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    name: Mapped[str]
    # Additive column — catalog module reads this for price markup calculation.
    # Default 20.0 covers all pre-existing rows; never 0 or negative by convention.
    markup_pct: Mapped[Decimal] = mapped_column(
        Numeric(7, 4), nullable=False, server_default="20.0"
    )
    # Additive nullable column — budgets TASK.md §3.
    # NULL means unlimited; no server_default; existing rows are unaffected.
    budget_usd_monthly: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    # Response-caching additive field (response-caching migration)
    cache_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # Guardrails-core additive field (guardrails-core migration)
    # JSONB NOT NULL DEFAULT '{}'::jsonb — empty object = no guardrails enabled
    guardrail_configs: Mapped[dict[str, Any]] = mapped_column(
        sa.JSON, nullable=False, default=dict, server_default=sa.text("'{}'::jsonb")
    )
    # Semantic-cache additive field (semantic-cache migration)
    # BOOLEAN NOT NULL DEFAULT false — per-tenant opt-in for normalized near-duplicate cache
    semantic_cache_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # Batch-auto-grouping additive field (batch-auto-grouping migration, v57)
    # BOOLEAN NOT NULL DEFAULT false — per-tenant opt-in for automatic diversion of
    # eligible /v1/chat/completions requests into the batch-job-store pipeline.
    batch_grouping_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # tenant-retention-zdr TASK.md §3 (FROZEN @ v1) — additive, no backfill.
    # NULL = inherits the operator per-table defaults (byte-identical default state).
    retention_window_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # BOOLEAN NOT NULL DEFAULT false — Zero-Data-Retention mode. Fail-closed-blocks new
    # payload writes at the 5 repository choke points (see
    # gateway.tenants.application.retention_policy.raise_if_zdr) and drives the
    # sweeper's unconditional per-tenant purge pass.
    zdr_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # ISO-8601 timestamp set on the false->true transition (PUT /admin/retention-policy).
    # NOT cleared on a later true->false transition — preserves the compliance record of
    # when ZDR was most recently enabled.
    zdr_enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # Payload-capture-store additive field (payload-capture-store migration)
    # BOOLEAN NOT NULL DEFAULT false — per-tenant opt-in for PII-scrubbed request/
    # response payload capture (request_logs). OR-resolved with api_keys.capture_enabled
    # at auth time (mirrors cache_enabled's key-can-only-turn-ON precedent).
    payload_capture_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # residency-policy TASK.md §3 (FROZEN @ v2) — additive, no backfill.
    # NULL = no pin (unrestricted) — byte-identical to pre-residency-policy behavior.
    # 'us' | 'eu' | 'ap' — the four-value catalog Region Literal minus 'global'
    # (a tenant can pin to a specific region, never to "global").
    residency_region: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # ISO-8601 timestamp set whenever residency_region actually CHANGES (set/change/clear
    # all update it) — mirrors zdr_enabled_at's compliance-timestamp precedent, but unlike
    # zdr_enabled_at this one IS updated on every transition, including clearing to NULL.
    residency_region_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # service-tiers TASK.md §3 (FROZEN @ v1) — additive, NOT NULL DEFAULT 'standard'.
    # The tenant-wide fallback tier when a key carries no per-key override (M1).
    default_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")
    # mcp-connector-passthrough TASK.md §3 (FROZEN @ v1) — additive.
    # JSONB NOT NULL DEFAULT '[]'::jsonb — list[{url,label}]; empty = deny-all (secure
    # default for every existing + new tenant row, byte-identical to pre-task behavior).
    mcp_allowed_servers: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sa.text("'[]'::jsonb")
    )
    # Implementation-detail bookkeeping column (NOT in §3's Schema block) — null until
    # the first PUT /admin/mcp-servers; mirrors residency_region_updated_at's precedent.
    mcp_allowed_servers_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # updated_at — NOT in the baseline (ad14442336db created tenants with created_at only);
    # added by migration e2b7f4c9a1d8 (provider-credential-store). Declared here without
    # onupdate so `alembic check` sees no diff (server_default only), matching the migration DDL.
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Platform-tenant discriminator (platform-tenant-seed migration). DEFAULT 'customer'
    # backfills existing rows; CHECK + partial unique index (kind='platform') enforce at
    # most one platform-kind row — resolve it via get_platform_tenant(), never a raw filter.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="customer")
    # account-type-discriminator TASK.md §3 (FROZEN @ v1). The personal|business flavor of a
    # customer tenant, set at signup (default 'business'); NULL on the reserved platform tenant.
    # A personal account is a 1-member OWNER tenant on the seeded `individual` plan — reuses the
    # whole tenant/user/role/plan pipeline (no separate account entity). Existing customers are
    # backfilled 'business' by the migration; the two CHECKs in __table_args__ enforce the value
    # set + the platform-never-personal invariant.
    account_type: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # plan-catalog TASK.md §3 (FROZEN @ v1) — additive, no backfill. NULL = unplanned, the
    # universal starting state for every pre-existing AND every newly-signed-up tenant
    # (no auto-assignment at signup) until a superadmin explicitly acts.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )
    # Per-tenant seat_cap OVERRIDE (mirrors budget_usd_monthly's own nullable/no-backfill
    # convention). NULL = inherit nothing / unlimited until a plan is assigned (M8/M9
    # resolve the actual value at PUT time — this column is a bare override slot, not a
    # live join).
    seat_cap: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # claude-gateway-protocol-compat TASK.md §3 (M8) — additive, NOT NULL DEFAULT false.
    # Mirrors zdr_enabled/semantic_cache_enabled's own per-tenant boolean opt-in
    # convention (a plain tenants column, not plans.feature_flags — M8 is explicitly a
    # per-TENANT flag). Gates ONLY whether the existing FallbackModelRouter substitution
    # mechanism may ever choose a non-Anthropic candidate for a request that named
    # (directly or via alias) a Claude model over the Anthropic-wire /v1/messages
    # surface — false (default) refuses fail-closed instead of silently serving a
    # non-Claude model; every pre-existing tenant defaults to the safer, disclosed-opt-in
    # state.
    allow_non_claude_failover: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # plan-rate-enforcement TASK.md §3 (FROZEN @ v1, M0) — additive, no backfill. Per-
    # tenant rpm/tpm OVERRIDE columns (mirrors budget_usd_monthly/seat_cap's own
    # nullable-override shape exactly). NULL = no tenant-layer override — the effective
    # ceiling falls through to the assigned plan's own rpm_limit_default/tpm_limit_default
    # (resolve_entitlements' tenant-override -> plan-default -> unlimited precedence,
    # M1), independent of every other dimension. Every existing row is NULL (inert).
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # additive, NOT NULL DEFAULT 'invoice'. NOTE (audit-remediation, 2026-07-15): this
    # column NO LONGER drives billing. The double-bill fix now couples the invoice skip
    # to settings.credits_gate_enabled (the SAME knob that wires the real-time
    # PostgresCreditGuard) — see InvoiceGenerator.generate_for_tenant — so invoice-skip
    # and credit-holds share one source of truth and cannot diverge. The original design
    # gated the skip on billing_mode == 'credits', but no code path ever set that value
    # and it was unsafe in both directions (double-bill when the knob was on; revenue
    # leak when off). The column is retained (harmless, backfilled to 'invoice') for
    # possible future per-tenant billing selection; it is currently read by no code path.
    billing_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="invoice")
    # billing-owner-of-record TASK.md §3 (FROZEN @ v1, M1) — additive, no ORM-level backfill
    # (backfill is migration-only; a fresh create_all-built schema, like the migration's own
    # pre-backfill state, starts every row NULL). FK -> users.id ON DELETE RESTRICT mirrors
    # plan_id's own nullable-FK-override shape. `use_alter` + an explicit constraint name
    # (matching the migration's own `tenants_billing_owner_user_id_fkey`) breaks the
    # tenants<->users mutual-FK cycle that create_all's topological table-creation sort would
    # otherwise reject (users.tenant_id -> tenants.id is the other, immediate, half of the
    # cycle) — deferred to a post-create ALTER TABLE, exactly as Postgres already does for
    # this FK shape; the migration itself has no such ordering concern (both tables already
    # exist at this revision).
    billing_owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "users.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="tenants_billing_owner_user_id_fkey",
        ),
        nullable=True,
        default=None,
    )


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member', "
            "'superadmin')",
            name="users_role_check",
        ),
        CheckConstraint("email = lower(email)", name="users_email_lowercase_check"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT")
    )
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[str] = mapped_column(server_default=text("'owner'"))
    # auth_method — additive column (oidc-tenant-config migration a9b3c4d5e6f7).
    # Sentinel-backfilled: rows with password_hash='!sso-no-password' → 'oidc'.
    # New SSO users get 'oidc' at INSERT (set by get_or_provision_oidc_user).
    # New password users keep DEFAULT 'password'.
    auth_method: Mapped[str] = mapped_column(
        sa.VARCHAR(32), nullable=False, server_default=text("'password'"), default="password"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # deactivated_at — additive column (scim-provisioning migration 010e6f83a709).
    # NULL = active (default, all existing rows unaffected). Mirrors api_keys.revoked_at's
    # nullable-timestamp soft-revoke pattern. Set by SCIM PATCH active:false; cleared by
    # PATCH active:true (reactivation).
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class InviteRow(Base):
    """invites — a tenant-scoped, single-use, hashed-at-rest pending invite
    (member-invite-issuance TASK.md §3). Purely additive: no existing table is altered (M11).

    Both timestamp columns are explicit ``DateTime(timezone=True)`` (TIMESTAMPTZ) per the
    frozen §3 schema — mirrors device_authorizations' explicit tz-aware convention (NOT
    UserRow/TenantRow's older bare-``Mapped[datetime]`` style, which maps to a naive
    TIMESTAMP WITHOUT TIME ZONE) so create_all (test) and the migration (prod) agree, and
    comparisons against ``datetime.now(UTC)`` never hit the naive/aware asyncpg trap.
    """

    __tablename__ = "invites"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'operator', 'billing_admin', 'viewer', 'member')",
            name="invites_role_check",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'revoked')", name="invites_status_check"
        ),
        CheckConstraint("email = lower(email)", name="invites_email_lowercase_check"),
        # At most ONE live pending invite per (tenant_id, email) — mirrors
        # uq_device_authorizations_user_code_pending (M5); DB-enforced, not just app logic.
        Index(
            "uq_invites_tenant_email_pending",
            "tenant_id",
            "email",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    # Stored lowercased by application code (CreateInviteUseCase); the CHECK above is the
    # DB-level backstop, mirroring users_email_lowercase_check.
    email: Mapped[str]
    # CHECK above excludes 'superadmin' entirely — stricter than users_role_check by design
    # (M4): an invite can never mint a superadmin, even if application code were bypassed.
    role: Mapped[str]
    # SHA-256 hex digest via Sha256SecretHasher.hash() — the plaintext token is NEVER
    # persisted or logged (M3). The UNIQUE constraint doubles as this column's lookup index
    # for the (separate) sibling accept-flow's token->row resolution.
    token_hash: Mapped[str] = mapped_column(unique=True)
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    # now() + 7d at insert time (application-computed; §1 ⚠ hardcoded default, no server_default).
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DomainInviteLinkRow(Base):
    """domain_invite_links — a tenant-scoped, reusable, revocable, 30-day shareable secret
    (invite-by-domain TASK.md §3, FROZEN @ v1, SECURITY). Purely additive: no existing
    table is altered. `token_hash` is INFRA-ONLY (SHA256 hex); the plaintext is returned
    once at creation and NEVER persisted (mirrors InviteRow's token_hash discipline).

    At most ONE active link per (tenant_id, domain) via a partial unique index
    WHERE status='active' — re-create atomically supersedes (revokes) the old row (M2).
    Both timestamp columns are explicit TIMESTAMPTZ (mirrors InviteRow's convention).
    """

    __tablename__ = "domain_invite_links"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'revoked')", name="ck_domain_invite_links_status"
        ),
        CheckConstraint("domain = lower(domain)", name="ck_domain_invite_links_domain_lower"),
        Index("uq_domain_invite_links_token_hash", "token_hash", unique=True),
        Index(
            "uq_domain_invite_links_active_domain",
            "tenant_id",
            "domain",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    domain: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DomainInviteRedemptionRow(Base):
    """domain_invite_redemptions — the ephemeral per-(link, email) 6-digit-code challenge
    that proves an individual mailbox before a domain-link join is provisioned
    (invite-by-domain TASK.md §3, FROZEN @ v1, SECURITY). `code_hash` is the Option-A keyed
    HMAC at rest (never plaintext); the row is consumed (deleted) on successful provision.

    UNIQUE (link_id, email) is the UPSERT target: re-issuing a code for the same
    (link, email) supersedes the hash + refreshes expiry + resets attempt_count. ON DELETE
    CASCADE off the parent link so revoking/superseding a link cleans up its in-flight codes.
    """

    __tablename__ = "domain_invite_redemptions"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_domain_invite_redemptions_email_lower"),
        Index(
            "uq_domain_invite_redemptions_link_email",
            "link_id",
            "email",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    link_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("domain_invite_links.id", ondelete="CASCADE")
    )
    email: Mapped[str] = mapped_column(Text)
    code_hash: Mapped[str] = mapped_column(Text)
    code_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0"), default=0
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImpersonationSessionRow(Base):
    """impersonation_sessions — a time-boxed, revocable superadmin impersonation session
    (impersonation-session-lifecycle TASK.md §3 Part D, FROZEN @ v1). Mirrors AgentTokenRow's
    own nullable-revoked_at-timestamp style (agent_oauth/infrastructure/orm.py) — the THIRD
    use of this pattern after api_keys and agent_tokens.

    Both actor_* and target_* are snapshotted AT MINT TIME (never re-derived live) — actor_*
    is the REAL superadmin who minted the session; target_* is the user being impersonated.
    """

    __tablename__ = "impersonation_sessions"
    __table_args__ = (
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('explicit_end', 'expired_lazy_cleanup')",
            name="impersonation_sessions_revoked_reason_check",
        ),
        # M7's race-safety backstop: at most ONE active (revoked_at IS NULL) row per actor.
        Index(
            "uq_impersonation_sessions_actor_active",
            "actor_user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    actor_tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    actor_email: Mapped[str] = mapped_column(nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    target_role: Mapped[str] = mapped_column(nullable=False)
    target_email: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked_reason: Mapped[str | None] = mapped_column(nullable=True, default=None)


class SeatMembershipEventRow(Base):
    """seat_membership_events — append-only seat-transition ledger (seat-billing TASK.md
    §3, FROZEN @ v2). One row per `joined`/`deactivated`/`reactivated` transition, written
    in the SAME DB transaction as the triggering `users`-row mutation at exactly 5 call
    sites (M3): InviteRepository.accept, SqlAlchemyScimUserRepository.create_user/
    .set_active, _get_or_provision_sso_user (new-user branch only, v2/CR-1),
    join_verified_tenant_domain (v2/CR-1).

    NEVER updated or deleted by any code path — the seat-domain analog of `usage_records`'
    own "one ledger of truth" doctrine. `user_id` REFERENCES users(id) ON DELETE RESTRICT
    because users are never hard-deleted in this codebase (§0 GROUND, confirmed via
    scim_router.py's DELETE-is-an-alias-for-PATCH-active:false doctrine).
    """

    __tablename__ = "seat_membership_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('joined', 'deactivated', 'reactivated')",
            name="ck_seat_membership_events_event_type",
        ),
        Index(
            "ix_seat_membership_events_tenant_user_occurred",
            "tenant_id",
            "user_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    # the real transition instant, tz-aware (mirrors InviteRow's own explicit
    # DateTime(timezone=True) convention for every NEW table in this codebase).
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # row-write instant, for audit/ordering-tiebreak only — never read for pricing.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
