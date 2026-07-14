"""SQLAlchemy ORM rows for the compliance report-schedule bounded context.

Schema contract (compliance-report-center TASK.md §3 — FROZEN @ v1):
  tenant_report_schedules (
    tenant_id       UUID PK REFERENCES tenants(id)   -- one row per tenant
    enabled         BOOLEAN NOT NULL DEFAULT false
    cadence         TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence = 'monthly')
    day_of_month    INTEGER NOT NULL DEFAULT 1 CHECK (day_of_month BETWEEN 1 AND 28)
    window_policy   TEXT NOT NULL DEFAULT 'previous_calendar_month'
    delivery_target TEXT NOT NULL DEFAULT 'in_app'
    created_by      UUID NULL
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    last_run_at     TIMESTAMPTZ NULL
    last_run_status TEXT NULL CHECK (last_run_status IN ('success','skipped_zdr','failed'))
    next_run_at     TIMESTAMPTZ NULL
  )
  compliance_report_runs (
    id              UUID PK (uuid7)
    tenant_id       UUID NOT NULL REFERENCES tenants(id)
    period_start    TIMESTAMPTZ NOT NULL
    period_end      TIMESTAMPTZ NOT NULL
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    object_key      TEXT NOT NULL   -- gateway.objectstore.port.ObjectStore key, s3-only
    size_bytes      INTEGER NOT NULL
    format_version  TEXT NOT NULL
    source          TEXT NOT NULL DEFAULT 'scheduled' CHECK (source = 'scheduled')
    UNIQUE (tenant_id, period_start)
    INDEX (tenant_id, generated_at DESC)
  )

Both tables mirror the invoice-generation InvoiceRow / retention_policy single-row-
per-tenant idioms verbatim (see billing/infrastructure/orm.py:InvoiceRow,
tenants/api/retention_policy_router.py) — no new ORM pattern introduced.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class TenantReportScheduleRow(Base):
    """Per-tenant Art. 12 monthly-generation schedule POLICY (one row per tenant)."""

    __tablename__ = "tenant_report_schedules"
    __table_args__ = (
        CheckConstraint("day_of_month BETWEEN 1 AND 28", name="ck_report_schedules_dom"),
        CheckConstraint(
            "last_run_status IN ('success', 'skipped_zdr', 'failed') OR last_run_status IS NULL",
            name="ck_report_schedules_last_run_status",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cadence: Mapped[str] = mapped_column(Text, nullable=False, server_default="monthly")
    day_of_month: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    window_policy: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="previous_calendar_month"
    )
    delivery_target: Mapped[str] = mapped_column(Text, nullable=False, server_default="in_app")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_run_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(nullable=True)


class ComplianceReportRunRow(Base):
    """One persisted, scheduler-generated Art. 12 bundle EVIDENCE RECORD (many rows)."""

    __tablename__ = "compliance_report_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "period_start", name="uq_compliance_report_runs_tenant_period"
        ),
        # DESC on generated_at to match the migration index exactly (clean autogenerate) —
        # mirrors conversations/infrastructure/orm.py's own documented precedent.
        Index(
            "ix_compliance_report_runs_tenant_generated",
            "tenant_id",
            text("generated_at DESC"),
        ),
        CheckConstraint("source = 'scheduled'", name="ck_compliance_report_runs_source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(nullable=False)
    period_end: Mapped[datetime] = mapped_column(nullable=False)
    generated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    format_version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="scheduled")
