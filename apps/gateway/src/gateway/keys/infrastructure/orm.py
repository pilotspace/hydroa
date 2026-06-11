"""SQLAlchemy ORM row for api_keys table."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, func
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
