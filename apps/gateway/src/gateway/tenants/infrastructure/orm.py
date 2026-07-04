import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


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
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # updated_at — NOT in the baseline (ad14442336db created tenants with created_at only);
    # added by migration e2b7f4c9a1d8 (provider-credential-store). Declared here without
    # onupdate so `alembic check` sees no diff (server_default only), matching the migration DDL.
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    # Platform-tenant discriminator (platform-tenant-seed migration). DEFAULT 'customer'
    # backfills existing rows; CHECK + partial unique index (kind='platform') enforce at
    # most one platform-kind row — resolve it via get_platform_tenant(), never a raw filter.
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="customer")


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
