"""SQLAlchemy ORM rows for the billing/invoice bounded context.

Schema contract (invoice-generation TASK.md §3 — FROZEN @ v1):
  invoices (
    id              UUID PK (uuid7)
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
    period_start    TIMESTAMPTZ NOT NULL   -- UTC month start, inclusive
    period_end      TIMESTAMPTZ NOT NULL   -- UTC month start+1, exclusive
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','issued'))
    currency        TEXT NOT NULL DEFAULT 'USD'
    total_usd       NUMERIC(12,2) NOT NULL DEFAULT 0
    raw_total_usd   NUMERIC(14,8) NOT NULL DEFAULT 0
    tax_usd         NUMERIC(12,2) NOT NULL DEFAULT 0
    issued_at       TIMESTAMPTZ NULL
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    UNIQUE (tenant_id, period_start)
  )
  invoice_lines (
    id                  UUID PK (uuid7)
    invoice_id          UUID NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT
    model_id            TEXT NOT NULL
    team_id             UUID NULL
    key_id              UUID NOT NULL
    tags                JSONB NOT NULL DEFAULT '{}'
    amount_usd          NUMERIC(12,2) NOT NULL
    raw_amount_usd      NUMERIC(14,8) NOT NULL
    prompt_tokens       BIGINT NOT NULL DEFAULT 0
    completion_tokens   BIGINT NOT NULL DEFAULT 0
    request_count       INTEGER NOT NULL DEFAULT 0
    line_type           TEXT NOT NULL DEFAULT 'usage'
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
  )
  invoice_corrections (
    id            UUID PK (uuid7)
    invoice_id    UUID NOT NULL REFERENCES invoices(id) ON DELETE RESTRICT
    delta_usd     NUMERIC(12,2) NOT NULL
    reason        TEXT NOT NULL
    created_by    TEXT NOT NULL
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
  )

IMMUTABILITY ENFORCEMENT (M5): DB layer only — migration <invoice-generation>
installs a BEFORE UPDATE OR DELETE trigger on invoices/invoice_lines/
invoice_corrections that always RAISEs (mirrors audit_events' immutability
trigger, migration f2a4c6e8b0d3). Every row this task ever writes is inserted
already in its final, issued form — there is no draft->issued UPDATE transition
in v1 (auto-issue after the stabilization window means generation only ever
runs once the window has already passed, so it inserts status='issued' directly).
Application layer: InvoiceRepository exposes NO update/delete method for any of
the three tables — invoice_corrections is append-only by construction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base
from gateway.core.ids import uuid7


class InvoiceRow(Base):
    """One immutable monthly statement per (tenant, UTC calendar month)."""

    __tablename__ = "invoices"
    __table_args__ = (
        # Keyset pagination: GET /admin/invoices walks (period_start, id) DESC,
        # tenant-scoped (mirrors audit_events_tenant_created_id_idx).
        Index("ix_invoices_tenant_period_id", "tenant_id", "period_start", "id"),
        # Idempotent generation (M13): ON CONFLICT (tenant_id, period_start) DO
        # NOTHING targets this constraint — the flusher.py's own idempotent-insert
        # idiom, one layer up.
        UniqueConstraint("tenant_id", "period_start", name="uq_invoices_tenant_period"),
        CheckConstraint("status IN ('draft', 'issued')", name="ck_invoices_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(nullable=False)
    period_end: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="USD")
    total_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    raw_total_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 8), nullable=False, server_default="0"
    )
    tax_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, server_default="0")
    issued_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class InvoiceLineRow(Base):
    """A grouped, rounded, evidence-linkable amount within an invoice."""

    __tablename__ = "invoice_lines"
    __table_args__ = (Index("ix_invoice_lines_invoice_id", "invoice_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    key_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    tags: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, server_default="{}")
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    raw_amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    completion_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    line_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="usage")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class InvoiceCorrectionRow(Base):
    """Append-only signed-delta correction against an original, frozen invoice."""

    __tablename__ = "invoice_corrections"
    __table_args__ = (Index("ix_invoice_corrections_invoice_id", "invoice_id"),)

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    delta_usd: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
