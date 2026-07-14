"""SQLAlchemy ORM row for api_keys table."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class ApiKeyRow(Base):
    """ORM row for the api_keys table.

    Schema mirrors §3 CONTRACT (baseline + key-governance migration):
      id uuid PK (uuid7, explicit at construction — no column default)
      tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
      name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200)
      key_hash text NOT NULL
      created_at timestamptz NOT NULL DEFAULT now()
      revoked_at timestamptz NULL
      -- governance fields (key-governance migration, additive):
      monthly_budget_usd  NUMERIC(12,2) NULL
      soft_budget_usd     NUMERIC(12,2) NULL  CHECK soft <= hard when both non-null
      expires_at          TIMESTAMPTZ   NULL
      model_allowlist     JSONB         NULL
      rotated_from_key_id UUID          NULL FK self-ref -> api_keys(id) ON DELETE SET NULL
    """

    __tablename__ = "api_keys"
    __table_args__ = (
        CheckConstraint(
            "length(name) BETWEEN 1 AND 200",
            name="api_keys_name_length_check",
        ),
        CheckConstraint(
            "soft_budget_usd IS NULL OR monthly_budget_usd IS NULL"
            " OR soft_budget_usd <= monthly_budget_usd",
            name="api_keys_soft_lte_hard_check",
        ),
        CheckConstraint(
            "rpm_limit IS NULL OR rpm_limit > 0",
            name="api_keys_rpm_limit_positive_check",
        ),
        CheckConstraint(
            "tpm_limit IS NULL OR tpm_limit > 0",
            name="api_keys_tpm_limit_positive_check",
        ),
        CheckConstraint(
            "tier IS NULL OR tier IN ('priority', 'standard')",
            name="api_keys_tier_check",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str]
    key_hash: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Governance fields — key-governance migration, all nullable
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    soft_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    model_allowlist: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=None)
    rotated_from_key_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Rate-limit fields — rate-limits migration, all nullable
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    # Teams attribution — teams-core migration, nullable (ON DELETE SET NULL)
    # NULL = un-teamed key; existing keys unaffected (backward-compatible additive column)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    # Response-caching additive field (response-caching migration)
    cache_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # Per-key guardrail policy override (per-key-guardrail-policies migration, additive).
    # NULL = no override, inherit tenant guardrail_configs (default for all existing +
    # new keys — byte-identical). Non-NULL (including {}) = explicit key-level override,
    # wholesale, no field merge with tenant. Mirrors model_allowlist's nullable-JSONB shape.
    guardrail_policy: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
    # Payload-capture-store additive field (payload-capture-store migration)
    capture_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    # service-tiers additive field (TASK.md §3, FROZEN @ v1) — OPTIONAL per-key
    # override; NULL = inherit tenants.default_tier (M1). CHECK constraint below
    # mirrors the tier|standard convention of PostgreSQL string enums used elsewhere
    # in this codebase (e.g. tenants.kind).
    tier: Mapped[str | None] = mapped_column(sa.Text, nullable=True, default=None)
    # mcp-connector-passthrough TASK.md §3 (FROZEN @ v1) — additive, nullable JSONB.
    # NULL = no override, inherit tenant mcp_allowed_servers (default for all existing +
    # new keys — byte-identical). Non-NULL (including []) = explicit key-level override,
    # wholesale, no field merge with tenant. Mirrors guardrail_policy's nullable-JSONB shape.
    mcp_allowed_servers_override: Mapped[list[Any] | None] = mapped_column(
        JSONB, nullable=True, default=None
    )
