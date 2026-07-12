"""SQLAlchemy ORM rows for credit_ledger + tenant_credit_balances.

Schema contract (credits-ledger TASK.md §3 — FROZEN @ v1). See migration
d3f7a9c1b5e8_credit_ledger.py for the DDL (including the immutability RULEs on
credit_ledger, mirroring audit_events).

These rows are used for READS (history/balance) and by test fixtures; the
row-locked WRITE path (check_and_hold/settle/release/topup) uses raw `text()`
SQL against the same tables inside one transaction (see
infrastructure/ledger_store.py) so the SELECT ... FOR UPDATE lock, the INSERT,
and the balance UPDATE are byte-identical across every call site, mirroring
the usage_records `ON CONFLICT (id) DO NOTHING` insert idiom.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Index, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from gateway.core.db import Base


class TenantCreditBalanceRow(Base):
    """Denormalized cache of SUM(credit_ledger.amount_usd) for one tenant (M7).

    PRIMARY KEY tenant_id — one row per tenant, created lazily on first hold/topup.
    Every credit_ledger INSERT updates this row in the SAME transaction via
    SELECT ... FOR UPDATE; the row lock is what serializes concurrent admissions
    for the SAME tenant and closes the TOCTOU window (M3).
    """

    __tablename__ = "tenant_credit_balances"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    balance_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, default=Decimal("0"))
    grace_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False, default=Decimal("0"))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)


class CreditLedgerRow(Base):
    """Append-only credit ledger row — immutable at the DB level (RULE, M8/R7).

    entry_type: 'topup' | 'hold' | 'settle' | 'release' | 'correction'.
    amount_usd: signed delta; tenant balance = SUM(amount_usd) for that tenant (M7).
    """

    __tablename__ = "credit_ledger"

    __table_args__ = (
        Index("credit_ledger_tenant_created_idx", "tenant_id", "created_at"),
        Index(
            "credit_ledger_request_idx",
            "tenant_id",
            "request_id",
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        Index(
            "credit_ledger_tenant_idem_uq",
            "tenant_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    entry_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    balance_after_usd: Mapped[Decimal] = mapped_column(Numeric(14, 8), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
