"""SQLAlchemy implementation over invoices/invoice_lines/invoice_corrections.

Contract (invoice-generation TASK.md §3 — FROZEN @ v1):
  - list_for_tenant_keyset / get_invoice / get_lines / get_corrections /
    corrected_total / get_line / evidence_keyset — all read-only, tenant-scoped.
  - record_correction — the ONE write path this repository exposes; append-only
    (INSERT into invoice_corrections, never UPDATE/DELETE any table).

Deliberately exposes NO update/delete method for invoices or invoice_lines —
mirrors AuditRepository's own "deliberately exposes NO update/delete" contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.billing.application.seat_pricer import (
    MembershipEvent,
    UserMembership,
    resolve_line_contributors,
)
from gateway.billing.domain.invoice import (
    Invoice,
    InvoiceCorrection,
    InvoiceLine,
    SeatEvidenceRow,
    UsageEvidenceRow,
)
from gateway.billing.infrastructure.orm import (
    InvoiceCorrectionRow,
    InvoiceLineRow,
    InvoiceRow,
)
from gateway.core.ids import uuid7
from gateway.tenants.infrastructure.orm import SeatMembershipEventRow, UserRow
from gateway.usage.infrastructure.orm import UsageRecordRow


def _as_naive_utc(dt: datetime) -> datetime:
    """Normalize to naive UTC — mirrors invoice_generator.py's own `_as_naive_utc` idiom
    (repeated locally per-file across this codebase: reconciliation.py, retention_sweep.py,
    audit/api/router.py) so seat_pricer's period-boundary comparisons stay consistent
    regardless of a column's naive (`users.created_at`) vs tz-aware
    (`seat_membership_events.occurred_at`) storage convention."""
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


async def load_tenant_seat_membership(
    session: AsyncSession, *, tenant_id: uuid.UUID
) -> list[UserMembership]:
    """Load every `users` row under `tenant_id` + its `seat_membership_events`, normalized
    to naive UTC — the ONE shared loader both generation-time pricing
    (InvoiceGenerator.generate_for_tenant) and evidence-time bucket re-computation
    (seat_evidence_keyset below) call, so both replay the IDENTICAL M6/M7 bucket algorithm
    over the IDENTICAL input shape (M11's "re-runs the same bucket computation" doctrine).
    """
    user_rows = (
        await session.execute(
            select(UserRow.id, UserRow.created_at, UserRow.deactivated_at).where(
                UserRow.tenant_id == tenant_id
            )
        )
    ).all()
    event_rows = (
        await session.execute(
            select(
                SeatMembershipEventRow.user_id,
                SeatMembershipEventRow.event_type,
                SeatMembershipEventRow.occurred_at,
            ).where(SeatMembershipEventRow.tenant_id == tenant_id)
        )
    ).all()

    events_by_user: dict[uuid.UUID, list[MembershipEvent]] = {}
    for user_id, event_type, occurred_at in event_rows:
        events_by_user.setdefault(user_id, []).append(
            MembershipEvent(event_type=event_type, occurred_at=_as_naive_utc(occurred_at))
        )

    return [
        UserMembership(
            user_id=user_id,
            events=tuple(events_by_user.get(user_id, ())),
            fallback_created_at=_as_naive_utc(created_at),
            fallback_deactivated_at=(
                _as_naive_utc(deactivated_at) if deactivated_at is not None else None
            ),
        )
        for user_id, created_at, deactivated_at in user_rows
    ]


def _invoice_from_row(row: InvoiceRow) -> Invoice:
    return Invoice(
        id=row.id,
        tenant_id=row.tenant_id,
        period_start=row.period_start,
        period_end=row.period_end,
        status=row.status,
        currency=row.currency,
        total_usd=Decimal(str(row.total_usd)),
        raw_total_usd=Decimal(str(row.raw_total_usd)),
        tax_usd=Decimal(str(row.tax_usd)),
        issued_at=row.issued_at,
        created_at=row.created_at,
    )


def _line_from_row(row: InvoiceLineRow) -> InvoiceLine:
    return InvoiceLine(
        id=row.id,
        invoice_id=row.invoice_id,
        model_id=row.model_id,
        team_id=row.team_id,
        key_id=row.key_id,
        tags=dict(row.tags) if row.tags else {},
        amount_usd=Decimal(str(row.amount_usd)),
        raw_amount_usd=Decimal(str(row.raw_amount_usd)),
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        request_count=row.request_count,
        line_type=row.line_type,
    )


def _correction_from_row(row: InvoiceCorrectionRow) -> InvoiceCorrection:
    return InvoiceCorrection(
        id=row.id,
        invoice_id=row.invoice_id,
        delta_usd=Decimal(str(row.delta_usd)),
        reason=row.reason,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class InvoiceRepository:
    """Tenant-scoped read repository + the one append-only correction write."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_tenant_keyset(
        self,
        tenant_id: uuid.UUID,
        *,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[Invoice]:
        """Keyset page over (period_start, id) DESC — mirrors
        AuditRepository.list_for_tenant_keyset's decomposed OR/AND predicate form."""
        stmt = (
            select(InvoiceRow)
            .where(InvoiceRow.tenant_id == tenant_id)
            .order_by(desc(InvoiceRow.period_start), desc(InvoiceRow.id))
            .limit(limit)
        )
        if cursor is not None:
            cursor_period_start, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    InvoiceRow.period_start < cursor_period_start,
                    and_(
                        InvoiceRow.period_start == cursor_period_start,
                        InvoiceRow.id < cursor_id,
                    ),
                )
            )
        result = await self._session.execute(stmt)
        return [_invoice_from_row(r) for r in result.scalars().all()]

    async def get_invoice(self, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice | None:
        """Tenant-404-invisible: unmatched row (unknown OR cross-tenant id) -> None."""
        stmt = select(InvoiceRow).where(
            InvoiceRow.id == invoice_id, InvoiceRow.tenant_id == tenant_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _invoice_from_row(row) if row is not None else None

    async def get_lines(self, invoice_id: uuid.UUID) -> list[InvoiceLine]:
        stmt = (
            select(InvoiceLineRow)
            .where(InvoiceLineRow.invoice_id == invoice_id)
            .order_by(InvoiceLineRow.created_at, InvoiceLineRow.id)
        )
        result = await self._session.execute(stmt)
        return [_line_from_row(r) for r in result.scalars().all()]

    async def get_line(self, invoice_id: uuid.UUID, line_id: uuid.UUID) -> InvoiceLine | None:
        """A line not belonging to `invoice_id` (wrong invoice, or unknown) -> None."""
        stmt = select(InvoiceLineRow).where(
            InvoiceLineRow.id == line_id, InvoiceLineRow.invoice_id == invoice_id
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        return _line_from_row(row) if row is not None else None

    async def get_corrections(self, invoice_id: uuid.UUID) -> list[InvoiceCorrection]:
        stmt = (
            select(InvoiceCorrectionRow)
            .where(InvoiceCorrectionRow.invoice_id == invoice_id)
            .order_by(InvoiceCorrectionRow.created_at, InvoiceCorrectionRow.id)
        )
        result = await self._session.execute(stmt)
        return [_correction_from_row(r) for r in result.scalars().all()]

    async def corrected_total(self, invoice_id: uuid.UUID, total_usd: Decimal) -> Decimal:
        """total_usd + SUM(corrections.delta_usd) for this invoice — computed at READ
        time, never stored denormalized back onto the frozen invoice row (M6)."""
        corrections = await self.get_corrections(invoice_id)
        delta = sum((c.delta_usd for c in corrections), Decimal("0"))
        return total_usd + delta

    async def record_correction(
        self,
        *,
        invoice_id: uuid.UUID,
        delta_usd: Decimal,
        reason: str,
        created_by: str,
    ) -> uuid.UUID:
        """Append ONE new invoice_corrections row — never touches the original
        invoice or any invoice_line (M6, mirrors record_correction's own
        row-layer signed-delta-append precedent one layer up)."""
        row = InvoiceCorrectionRow(
            id=uuid7(),
            invoice_id=invoice_id,
            delta_usd=delta_usd,
            reason=reason,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return row.id

    async def evidence_keyset(
        self,
        *,
        tenant_id: uuid.UUID,
        invoice: Invoice,
        line: InvoiceLine,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[UsageEvidenceRow]:
        """Re-run the line's exact (tenant, period, grouping-key) predicate against
        usage_records — never a materialized row-id list (M7, R7 volume concern).

        team_id uses IS NOT DISTINCT FROM (NULL-safe equality) — a NULL team_id line
        must match NULL team_id rows, which plain `==` would never do in SQL's
        three-valued logic.
        """
        stmt = (
            select(UsageRecordRow)
            .where(
                UsageRecordRow.tenant_id == tenant_id,
                UsageRecordRow.created_at >= invoice.period_start,
                UsageRecordRow.created_at < invoice.period_end,
                UsageRecordRow.model_id == line.model_id,
                UsageRecordRow.key_id == line.key_id,
                UsageRecordRow.team_id.is_not_distinct_from(line.team_id),
                UsageRecordRow.tags == line.tags,
            )
            .order_by(desc(UsageRecordRow.created_at), desc(UsageRecordRow.id))
            .limit(limit)
        )
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    UsageRecordRow.created_at < cursor_created_at,
                    and_(
                        UsageRecordRow.created_at == cursor_created_at,
                        UsageRecordRow.id < cursor_id,
                    ),
                )
            )
        result = await self._session.execute(stmt)
        rows = result.scalars().all()
        return [
            UsageEvidenceRow(
                usage_record_id=r.id,
                created_at=r.created_at,
                model_id=r.model_id,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                cost_usd=Decimal(str(r.cost_usd)),
                request_id=_extract_request_id(r.raw),
            )
            for r in rows
        ]

    async def seat_evidence_keyset(
        self,
        *,
        tenant_id: uuid.UUID,
        invoice: Invoice,
        line: InvoiceLine,
        limit: int,
        cursor: tuple[datetime, uuid.UUID] | None = None,
    ) -> list[SeatEvidenceRow]:
        """Re-run the line's exact (tenant, period, bucket) predicate against
        seat_membership_events (M11, seat-billing TASK.md §3 — FROZEN @ v2) — never a
        materialized row-id list, mirrors evidence_keyset's own doctrine one domain over
        (usage_records -> seat_membership_events). For a 'proration' line, the predicate
        is exactly `user_id = key_id` (1:1, M9); for the aggregate 'seat' line, every
        full-price-bucket user_id is re-derived and queried, keyset-unioned in one stable
        (occurred_at, id) DESC order.

        Returns [] (never raises) if the current replay finds no contributing seat for
        this line — a data-integrity drift since generation time that should be
        structurally impossible given the append-only ledger, but degraded to an honest
        empty page rather than a crash (M11 defense-in-depth, mirrors M5's own posture).
        Caller (router) is responsible for the M12 line_type=='usage' -> 400 reject
        BEFORE ever calling this method.
        """
        memberships = await load_tenant_seat_membership(self._session, tenant_id=tenant_id)
        contributing = resolve_line_contributors(
            users=memberships,
            period_start=invoice.period_start,
            period_end=invoice.period_end,
            line_type=line.line_type,
            key_id=line.key_id,
        )
        if not contributing:
            return []

        stmt = (
            select(SeatMembershipEventRow, UserRow.email)
            .join(UserRow, UserRow.id == SeatMembershipEventRow.user_id)
            .where(
                SeatMembershipEventRow.tenant_id == tenant_id,
                SeatMembershipEventRow.user_id.in_(contributing),
            )
            .order_by(desc(SeatMembershipEventRow.occurred_at), desc(SeatMembershipEventRow.id))
            .limit(limit)
        )
        if cursor is not None:
            cursor_occurred_at, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    SeatMembershipEventRow.occurred_at < cursor_occurred_at,
                    and_(
                        SeatMembershipEventRow.occurred_at == cursor_occurred_at,
                        SeatMembershipEventRow.id < cursor_id,
                    ),
                )
            )
        result = await self._session.execute(stmt)
        return [
            SeatEvidenceRow(
                event_id=event_row.id,
                user_id=event_row.user_id,
                email=email,
                event_type=event_row.event_type,
                occurred_at=event_row.occurred_at,
            )
            for event_row, email in result.all()
        ]


def _extract_request_id(raw: dict[str, object]) -> str | None:
    value = raw.get("request_id") if raw else None
    return value if isinstance(value, str) else None
