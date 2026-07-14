"""SQLAlchemy ORM rows for the agent OAuth device-grant store.

All timestamp columns are timestamptz (DateTime(timezone=True)) so create_all (test) and
the migration (prod) agree, and tz-aware comparisons never hit the naive/aware asyncpg trap.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class DeviceAuthorizationRow(Base):
    """device_authorizations — a pending/approved/denied/consumed RFC 8628 request."""

    __tablename__ = "device_authorizations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'denied', 'consumed')",
            name="device_authorizations_status_check",
        ),
        CheckConstraint(
            "interval_seconds > 0", name="device_authorizations_interval_positive_check"
        ),
        Index("ix_device_authorizations_user_code_hash", "user_code_hash"),
        # Only ONE live pending authorization may hold a given user_code at a time.
        Index(
            "uq_device_authorizations_user_code_pending",
            "user_code_hash",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    device_code_hash: Mapped[str] = mapped_column(unique=True)
    user_code_hash: Mapped[str]
    status: Mapped[str] = mapped_column(server_default=text("'pending'"))
    scope: Mapped[str] = mapped_column(server_default=text("'proxy'"))
    interval_seconds: Mapped[int] = mapped_column(Integer)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        default=None,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


class AgentTokenRow(Base):
    """agent_tokens — an issued opaque token (hashes at rest; one per authorization)."""

    __tablename__ = "agent_tokens"
    __table_args__ = (Index("ix_agent_tokens_access_token_hash", "access_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    authorization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("device_authorizations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # single mint per authorization (single-use device_code backstop)
    )
    access_token_hash: Mapped[str] = mapped_column(unique=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(unique=True, nullable=True, default=None)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scope: Mapped[str] = mapped_column(server_default=text("'proxy'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    access_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    refresh_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    # agent-identity-governance TASK.md §3 (FROZEN @ v1) — additive, nullable: every
    # existing v39 row stays NULL (unattached, byte-identical behavior). Indexed for
    # the bulk kill-switch UPDATE (WHERE principal_id = :id) and the LEFT JOIN in
    # resolve_access_token().
    principal_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_principals.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
        index=True,
    )


class AgentPrincipalRow(Base):
    """agent_principals — a named, tenant-scoped identity grouping of agent tokens.

    agent-identity-governance TASK.md §3 (FROZEN @ v1). name is UNIQUE per
    (tenant_id, name). killed_at is the kill switch: once set, no new token may be
    attached (agent_principal_killed, 409) and every previously-attached token's OWN
    revoked_at is set in the SAME transaction (M7) — resolve_access_token's existing
    fail-closed read is what makes the kill effective at both authn seams, by
    construction (no new propagation mechanism).
    """

    __tablename__ = "agent_principals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agent_principals_tenant_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(nullable=False)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )
    monthly_budget_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True, default=None
    )
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    tpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    killed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
