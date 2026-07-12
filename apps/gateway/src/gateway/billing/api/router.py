"""GET /admin/invoices — tenant-scoped invoice list/detail/evidence/export API.

Contract (invoice-generation TASK.md §3 — FROZEN @ v1):
  - Permission.INVOICES_READ gated: owner/admin/billing_admin/superadmin -> 200;
    operator/viewer/member -> 403.
  - Tenant-scoped: only the caller's own tenant_id rows, every route.
  - GET /admin/invoices: keyset-cursor pagination (period_start, id) DESC,
    limit 1..100 default 50, mirrors AuditRepository.list_for_tenant_keyset.
  - GET /admin/invoices/{id}: full detail + lines + corrections +
    corrected_total_usd (computed at read time).
  - GET /admin/invoices/{id}/lines/{line_id}/evidence: keyset-paginated
    (created_at, id) DESC re-query over usage_records — never a materialized
    row-id list.
  - GET /admin/invoices/{id}/export?format=pdf|csv: binary body, both derive
    from the SAME persisted, already-rounded figures as the detail response.
  - Unknown OR cross-tenant invoice_id/line_id -> identical 404
    ERR_INVOICE_NOT_FOUND (no leak). Bounded asyncio.timeout on every read ->
    504 ERR_INVOICE_QUERY_TIMEOUT on expiry.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.billing.application.invoice_export import export_filename, render_csv, render_pdf
from gateway.billing.domain.invoice import Invoice, InvoiceLine
from gateway.billing.infrastructure.invoice_repository import InvoiceRepository
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    CURSOR_INVALID,
    INVOICE_LINE_WRONG_TYPE,
    INVOICE_NOT_FOUND,
    INVOICE_QUERY_TIMEOUT,
    PAYLOAD_INVALID,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

invoices_router = APIRouter(prefix="/admin/invoices", tags=["billing"])

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100
_READ_TIMEOUT_SECONDS = 15.0


# ---------------------------------------------------------------------------
# Pydantic response schemas (§3 CONTRACT)
# ---------------------------------------------------------------------------


class InvoiceListItem(BaseModel):
    id: str
    period_start: str
    period_end: str
    status: str
    total_usd: str
    currency: str
    issued_at: str | None


class InvoiceLineItem(BaseModel):
    id: str
    model_id: str
    team_id: str | None
    key_id: str
    tags: dict[str, str]
    amount_usd: str
    prompt_tokens: int
    completion_tokens: int
    request_count: int
    line_type: str


class InvoiceCorrectionItem(BaseModel):
    id: str
    delta_usd: str
    reason: str
    created_by: str
    created_at: str


class InvoiceDetailResponse(BaseModel):
    id: str
    tenant_id: str
    period_start: str
    period_end: str
    status: str
    currency: str
    total_usd: str
    raw_total_usd: str
    tax_usd: str
    corrected_total_usd: str
    issued_at: str | None
    created_at: str
    lines: list[InvoiceLineItem]
    corrections: list[InvoiceCorrectionItem]


class UsageEvidenceItem(BaseModel):
    usage_record_id: str
    created_at: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str
    request_id: str | None


class InvoiceListResponse(BaseModel):
    items: list[InvoiceListItem]
    next_cursor: str | None
    has_more: bool


class UsageEvidenceListResponse(BaseModel):
    items: list[UsageEvidenceItem]
    next_cursor: str | None
    has_more: bool


class SeatEvidenceItem(BaseModel):
    event_id: str
    user_id: str
    email: str
    event_type: str
    occurred_at: str


class SeatEvidenceListResponse(BaseModel):
    items: list[SeatEvidenceItem]
    next_cursor: str | None
    has_more: bool


# ---------------------------------------------------------------------------
# Query-param parsing / cursor codec
# ---------------------------------------------------------------------------


def _parse_limit(limit: str | None) -> int:
    if limit is None:
        return _DEFAULT_LIMIT
    try:
        parsed = int(limit)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


def _parse_format(fmt: str | None) -> str:
    if fmt not in ("pdf", "csv"):
        raise PAYLOAD_INVALID.exc()
    return fmt


def _encode_cursor(primary: datetime, secondary_id: uuid.UUID) -> str:
    payload = json.dumps({"k": primary.isoformat(), "id": str(secondary_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(payload)
        primary = datetime.fromisoformat(obj["k"])
        row_id = uuid.UUID(obj["id"])
    except Exception:
        raise CURSOR_INVALID.exc() from None
    return primary, row_id


def _invoice_to_list_item(inv: Invoice) -> InvoiceListItem:
    return InvoiceListItem(
        id=str(inv.id),
        period_start=inv.period_start.isoformat(),
        period_end=inv.period_end.isoformat(),
        status=inv.status,
        total_usd=f"{inv.total_usd:.2f}",
        currency=inv.currency,
        issued_at=inv.issued_at.isoformat() if inv.issued_at is not None else None,
    )


def _line_to_item(line: InvoiceLine) -> InvoiceLineItem:
    return InvoiceLineItem(
        id=str(line.id),
        model_id=line.model_id,
        team_id=str(line.team_id) if line.team_id is not None else None,
        key_id=str(line.key_id),
        tags=line.tags,
        amount_usd=f"{line.amount_usd:.2f}",
        prompt_tokens=line.prompt_tokens,
        completion_tokens=line.completion_tokens,
        request_count=line.request_count,
        line_type=line.line_type,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@invoices_router.get("")
async def list_invoices(
    identity: Annotated[Identity, require_permission(Permission.INVOICES_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> InvoiceListResponse:
    parsed_limit = _parse_limit(limit)
    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        cursor_tuple = _decode_cursor(cursor)

    repo = InvoiceRepository(session)
    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            invoices = await repo.list_for_tenant_keyset(
                identity.tenant_id, limit=parsed_limit + 1, cursor=cursor_tuple
            )
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None

    has_more = len(invoices) > parsed_limit
    page = invoices[:parsed_limit]
    next_cursor = _encode_cursor(page[-1].period_start, page[-1].id) if has_more and page else None

    return InvoiceListResponse(
        items=[_invoice_to_list_item(inv) for inv in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


async def _get_invoice_or_404(
    repo: InvoiceRepository, tenant_id: uuid.UUID, invoice_id: str
) -> Invoice:
    try:
        parsed_id = uuid.UUID(invoice_id)
    except ValueError:
        raise INVOICE_NOT_FOUND.exc() from None
    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            invoice = await repo.get_invoice(tenant_id, parsed_id)
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None
    if invoice is None:
        raise INVOICE_NOT_FOUND.exc()
    return invoice


@invoices_router.get("/{invoice_id}")
async def get_invoice_detail(
    invoice_id: str,
    identity: Annotated[Identity, require_permission(Permission.INVOICES_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvoiceDetailResponse:
    repo = InvoiceRepository(session)
    invoice = await _get_invoice_or_404(repo, identity.tenant_id, invoice_id)

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            lines = await repo.get_lines(invoice.id)
            corrections = await repo.get_corrections(invoice.id)
            corrected_total = await repo.corrected_total(invoice.id, invoice.total_usd)
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None

    return InvoiceDetailResponse(
        id=str(invoice.id),
        tenant_id=str(invoice.tenant_id),
        period_start=invoice.period_start.isoformat(),
        period_end=invoice.period_end.isoformat(),
        status=invoice.status,
        currency=invoice.currency,
        total_usd=f"{invoice.total_usd:.2f}",
        raw_total_usd=str(invoice.raw_total_usd),
        tax_usd=f"{invoice.tax_usd:.2f}",
        corrected_total_usd=f"{corrected_total:.2f}",
        issued_at=invoice.issued_at.isoformat() if invoice.issued_at is not None else None,
        created_at=invoice.created_at.isoformat(),
        lines=[_line_to_item(line) for line in lines],
        corrections=[
            InvoiceCorrectionItem(
                id=str(c.id),
                delta_usd=f"{c.delta_usd:.2f}",
                reason=c.reason,
                created_by=c.created_by,
                created_at=c.created_at.isoformat(),
            )
            for c in corrections
        ],
    )


@invoices_router.get("/{invoice_id}/lines/{line_id}/evidence")
async def get_invoice_line_evidence(
    invoice_id: str,
    line_id: str,
    identity: Annotated[Identity, require_permission(Permission.INVOICES_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> UsageEvidenceListResponse:
    parsed_limit = _parse_limit(limit)
    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        cursor_tuple = _decode_cursor(cursor)

    repo = InvoiceRepository(session)
    invoice = await _get_invoice_or_404(repo, identity.tenant_id, invoice_id)

    try:
        parsed_line_id = uuid.UUID(line_id)
    except ValueError:
        raise INVOICE_NOT_FOUND.exc() from None

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            line = await repo.get_line(invoice.id, parsed_line_id)
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None
    if line is None:
        raise INVOICE_NOT_FOUND.exc()

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            rows = await repo.evidence_keyset(
                tenant_id=identity.tenant_id,
                invoice=invoice,
                line=line,
                limit=parsed_limit + 1,
                cursor=cursor_tuple,
            )
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None

    has_more = len(rows) > parsed_limit
    page = rows[:parsed_limit]
    next_cursor = (
        _encode_cursor(page[-1].created_at, page[-1].usage_record_id) if has_more and page else None
    )

    return UsageEvidenceListResponse(
        items=[
            UsageEvidenceItem(
                usage_record_id=str(r.usage_record_id),
                created_at=r.created_at.isoformat(),
                model_id=r.model_id,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
                cost_usd=str(r.cost_usd),
                request_id=r.request_id,
            )
            for r in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@invoices_router.get("/{invoice_id}/lines/{line_id}/seat-evidence")
async def get_seat_line_evidence(
    invoice_id: str,
    line_id: str,
    identity: Annotated[Identity, require_permission(Permission.INVOICES_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
) -> SeatEvidenceListResponse:
    """seat-billing TASK.md §3 (FROZEN @ v2, M11/M12) — additive sibling to the EXISTING
    /evidence route above (never a branch inside it, per §1 Framings weighed: a seat
    membership event has no natural slot in UsageEvidenceItem's usage-shaped response)."""
    parsed_limit = _parse_limit(limit)
    cursor_tuple: tuple[datetime, uuid.UUID] | None = None
    if cursor is not None:
        cursor_tuple = _decode_cursor(cursor)

    repo = InvoiceRepository(session)
    invoice = await _get_invoice_or_404(repo, identity.tenant_id, invoice_id)

    try:
        parsed_line_id = uuid.UUID(line_id)
    except ValueError:
        raise INVOICE_NOT_FOUND.exc() from None

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            line = await repo.get_line(invoice.id, parsed_line_id)
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None
    if line is None:
        raise INVOICE_NOT_FOUND.exc()

    # M12: a 'usage' line has real evidence at the EXISTING /evidence route above — this
    # route is never a silent alias for it.
    if line.line_type == "usage":
        raise INVOICE_LINE_WRONG_TYPE.exc()

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            rows = await repo.seat_evidence_keyset(
                tenant_id=identity.tenant_id,
                invoice=invoice,
                line=line,
                limit=parsed_limit + 1,
                cursor=cursor_tuple,
            )
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None

    has_more = len(rows) > parsed_limit
    page = rows[:parsed_limit]
    next_cursor = (
        _encode_cursor(page[-1].occurred_at, page[-1].event_id) if has_more and page else None
    )

    return SeatEvidenceListResponse(
        items=[
            SeatEvidenceItem(
                event_id=str(r.event_id),
                user_id=str(r.user_id),
                email=r.email,
                event_type=r.event_type,
                occurred_at=r.occurred_at.isoformat(),
            )
            for r in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )


@invoices_router.get("/{invoice_id}/export")
async def export_invoice(
    invoice_id: str,
    identity: Annotated[Identity, require_permission(Permission.INVOICES_READ)],
    session: Annotated[AsyncSession, Depends(get_session)],
    format: Annotated[str | None, Query()] = None,
) -> Response:
    resolved_format = _parse_format(format)
    repo = InvoiceRepository(session)
    invoice = await _get_invoice_or_404(repo, identity.tenant_id, invoice_id)

    try:
        async with asyncio.timeout(_READ_TIMEOUT_SECONDS):
            lines = await repo.get_lines(invoice.id)
    except TimeoutError:
        raise INVOICE_QUERY_TIMEOUT.exc() from None

    filename = export_filename(invoice, resolved_format)
    if resolved_format == "csv":
        body = render_csv(invoice, lines)
        media_type = "text/csv"
    else:
        body = render_pdf(invoice, lines)
        media_type = "application/pdf"

    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
