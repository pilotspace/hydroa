"""GET /admin/platform/margin/* — operator margin dashboard (margin-dashboard TASK.md §3
CONTRACT — FROZEN @ v1).

Productizes the existing `usage/application/reconciliation.py` substrate (already serving
the mTLS-gated `/ops/reconciliation`) into a superadmin-only, JWT-gated platform-console
surface: per-tenant/per-model margin (`/by-tenant-model`), a windowed trend (`/trend`), and
a three-way tie-out against issued invoices (`/tie-out`), plus a `/summary` that is
BYTE-IDENTICAL to `/ops/reconciliation`'s own totals for the same window (both call the
SAME `reconcile_window` — no second aggregation, M1/M2).

Central rule (M3, the whole task's reason to exist): margin is a REAL, computed number only
for `cost_basis='provider'` rows (only OpenRouter reports an authoritative upstream cost
today — `recorder.py:_safe_provider_cost`). A `cost_basis='catalog'` bucket NEVER receives a
fabricated or zeroed margin — it carries `has_provider_cost_data=False` and `margin=null`,
its billed revenue reported separately as `catalog_billed_total`. This task NEVER imports or
calls `resolve_markup_pct` or any pricing function (M2) — every number here is a sum of
already-persisted `cost_usd`/`provider_cost`, never a re-resolved price.

Auth: `require_superadmin` (role-only JWT dependency) FIRST on every route — the platform-
console auth surface, NEVER `require_ops` (mTLS, unreachable from the browser). Every route
audits its read via `emit_platform_audit(...)` on the success path (M9, `target_tenant_id=
None` for these operator-wide reads; an optional `tenant_id=` filter is carried in metadata,
not as target_tenant_id, since the read still spans whatever the caller queried).

Reads only — this router has NO write path at all (Schema: NO new tables, NO migration).
Every one of the 4 reads is bounded by `asyncio.timeout` (M8, mirrors `/ops/reconciliation`'s
own 30s bound) → `ERR_MARGIN_QUERY_TIMEOUT` (504) on expiry.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import CURSOR_INVALID, MARGIN_QUERY_TIMEOUT, PAYLOAD_INVALID
from gateway.tenants.domain.authz import require_superadmin
from gateway.tenants.domain.entities import Identity

# Reuse the sibling reconciliation endpoints' window-bounds validator VERBATIM — the §3
# contract requires window/date validation byte-identical to /admin/usage/spend and
# /ops/reconciliation (422 ERR_PAYLOAD_INVALID). It is module-private to the usage router
# (refactoring its home is out of this task's scope), hence the explicit ignore — the SAME
# pattern ops/api/router.py already uses for the identical import.
from gateway.usage.api.router import _compute_window_bounds  # pyright: ignore[reportPrivateUsage]
from gateway.usage.application.reconciliation import (
    MarginTrendPoint,
    TenantLedgerPeriod,
    TenantModelReconciliation,
    reconcile_by_tenant_model,
    reconcile_trend,
    reconcile_window,
    tie_out_ledger_by_tenant,
)

margin_router = APIRouter(prefix="/admin/platform/margin", tags=["platform-admin", "margin"])

# Bounded timeout for every read-only cross-tenant aggregate (IO design-for-failure),
# mirroring /ops/reconciliation's own _OPS_RECON_TIMEOUT_SECONDS exactly.
_MARGIN_QUERY_TIMEOUT_SECONDS = 30.0

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 100


# ---------------------------------------------------------------------------
# Schemas (§3 CONTRACT)
# ---------------------------------------------------------------------------


class MarginSummaryResponse(BaseModel):
    window_from: str
    window_to: str
    provider_cost_total: str
    billed_total: str
    catalog_billed_total: str
    margin: str | None
    has_provider_cost_data: bool
    drift: str
    unbilled_upstream_cost: str
    unbilled_rows: int


class TenantModelMarginItem(BaseModel):
    tenant_id: str
    model_id: str
    provider_cost_total: str
    billed_total: str
    catalog_billed_total: str
    margin: str | None
    has_provider_cost_data: bool
    unbilled_upstream_cost: str
    unbilled_rows: int


class ByTenantModelResponse(BaseModel):
    items: list[TenantModelMarginItem]
    next_cursor: str | None
    has_more: bool


class MarginTrendPointResponse(BaseModel):
    bucket_start: str
    provider_cost_total: str
    billed_total: str
    catalog_billed_total: str
    margin: str | None
    has_provider_cost_data: bool


class TrendResponse(BaseModel):
    granularity: str
    points: list[MarginTrendPointResponse]


class TieOutItemResponse(BaseModel):
    tenant_id: str
    invoice_id: str | None
    invoice_status: str
    invoiced_total_usd: str | None
    invoiced_raw_total_usd: str | None
    ledger_billed_total_usd: str
    provider_cost_total_usd: str
    drift_invoiced_vs_ledger: str | None
    tie_out_status: str


class TieOutResponse(BaseModel):
    period_start: str
    period_end: str
    items: list[TieOutItemResponse]


# ---------------------------------------------------------------------------
# Query-param parsing / cursor codec
# ---------------------------------------------------------------------------


def _parse_limit(raw: str | None) -> int:
    if raw is None:
        return _DEFAULT_LIMIT
    try:
        parsed = int(raw)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


def _parse_uuid_filter(raw: str | None) -> uuid.UUID | None:
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise PAYLOAD_INVALID.exc(detail=f"tenant_id is not a valid UUID: {raw!r}") from None


def _encode_cursor(tenant_id: str, model_id: str) -> str:
    payload = json.dumps({"tid": tenant_id, "mid": model_id}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_cursor(raw: str) -> tuple[str, str]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(payload)
        tenant_id = str(obj["tid"])
        model_id = str(obj["mid"])
    except Exception:
        raise CURSOR_INVALID.exc() from None
    return tenant_id, model_id


def _parse_period(raw: str | None) -> tuple[datetime.datetime, datetime.datetime]:
    """R4: `period=YYYY-MM` -> (period_start, period_end) UTC calendar-month bounds.

    `period_end` = `period_start` + 1 calendar month (matches invoice-generation's own M1
    convention exactly, cited directly in this task's §3 CONTRACT).
    """
    if not raw:
        raise PAYLOAD_INVALID.exc(detail="period is required, format YYYY-MM")
    parts = raw.split("-")
    if len(parts) != 2:
        raise PAYLOAD_INVALID.exc(detail=f"period must be YYYY-MM, got {raw!r}")
    year_str, month_str = parts
    shape_ok = (
        year_str.isdigit() and month_str.isdigit() and len(year_str) == 4 and len(month_str) == 2
    )
    if not shape_ok:
        raise PAYLOAD_INVALID.exc(detail=f"period must be YYYY-MM, got {raw!r}")
    year, month = int(year_str), int(month_str)
    if month < 1 or month > 12:
        raise PAYLOAD_INVALID.exc(detail=f"period must be a real calendar month, got {raw!r}")
    period_start = datetime.datetime(year, month, 1, tzinfo=datetime.UTC)
    if month == 12:
        period_end = datetime.datetime(year + 1, 1, 1, tzinfo=datetime.UTC)
    else:
        period_end = datetime.datetime(year, month + 1, 1, tzinfo=datetime.UTC)
    return period_start, period_end


# ---------------------------------------------------------------------------
# Cross-context read: invoices for a period (billing/infrastructure/orm.py:InvoiceRow —
# invoice-generation §3 FROZEN, already merged). A cross-tenant read this task's own §3
# names as Build's discretion ("a new InvoiceRepository method or a standalone query in the
# margin module — non-load-bearing on this contract's shape") — a standalone query here, so
# invoice-generation's own frozen files stay untouched.
# ---------------------------------------------------------------------------


async def _fetch_invoices_for_period(
    session: AsyncSession, period_start: datetime.datetime
) -> dict[uuid.UUID, tuple[uuid.UUID, str, Decimal, Decimal]]:
    """tenant_id -> (invoice_id, status, total_usd, raw_total_usd) for that UTC calendar month."""
    # invoice_generator.py writes `period_start` as naive UTC (its own `_as_naive_utc`
    # convention) — normalize the same way here so the equality match holds regardless of
    # whether the underlying `invoices.period_start` column is TIMESTAMPTZ (the real
    # migration) or a naive TIMESTAMP (the fast create_all() test schema).
    naive_period_start = (
        period_start.astimezone(datetime.UTC).replace(tzinfo=None)
        if period_start.tzinfo is not None
        else period_start
    )
    rows = (
        await session.execute(
            text(
                "SELECT id, tenant_id, status, total_usd, raw_total_usd"
                " FROM invoices WHERE period_start = :period_start"
            ),
            {"period_start": naive_period_start},
        )
    ).fetchall()
    return {
        uuid.UUID(str(row[1])): (
            uuid.UUID(str(row[0])),
            str(row[2]),
            Decimal(str(row[3])),
            Decimal(str(row[4])),
        )
        for row in rows
    }


# ---------------------------------------------------------------------------
# GET /admin/platform/margin/summary
# ---------------------------------------------------------------------------


@margin_router.get("/summary", response_model=MarginSummaryResponse)
async def get_margin_summary(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    window: Annotated[str, Query()] = "month",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> MarginSummaryResponse:
    """M1: byte-identical to /ops/reconciliation's own summary fields for the same window —
    both call reconcile_window, no second aggregation (M2). `margin`/`has_provider_cost_data`
    are derived in THIS response-mapping layer from the existing provider_cost_total/
    billed_total/unbilled_rows fields only, per §3's own Access-pattern note — no new SQL."""
    window_start, window_end, _granularity = _compute_window_bounds(window, start, end)

    try:
        async with asyncio.timeout(_MARGIN_QUERY_TIMEOUT_SECONDS):
            summary = await reconcile_window(session, window_start, window_end, tenant_id=None)
    except TimeoutError:
        raise MARGIN_QUERY_TIMEOUT.exc() from None

    has_provider_cost_data = (
        summary.provider_cost_total != Decimal("0")
        or summary.billed_total != Decimal("0")
        or summary.unbilled_rows > 0
    )
    margin = summary.billed_total - summary.provider_cost_total if has_provider_cost_data else None

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.margin.view_summary",
        target_type="margin",
        target_id=None,
        metadata={
            "window": window,
            "window_from": summary.window_from.isoformat(),
            "window_to": summary.window_to.isoformat(),
        },
    )

    return MarginSummaryResponse(
        window_from=summary.window_from.isoformat(),
        window_to=summary.window_to.isoformat(),
        provider_cost_total=str(summary.provider_cost_total),
        billed_total=str(summary.billed_total),
        catalog_billed_total=str(summary.catalog_billed_total),
        margin=str(margin) if margin is not None else None,
        has_provider_cost_data=has_provider_cost_data,
        drift=str(summary.drift),
        unbilled_upstream_cost=str(summary.unbilled_upstream_cost),
        unbilled_rows=summary.unbilled_rows,
    )


# ---------------------------------------------------------------------------
# GET /admin/platform/margin/by-tenant-model
# ---------------------------------------------------------------------------


def _item_to_response(item: TenantModelReconciliation) -> TenantModelMarginItem:
    return TenantModelMarginItem(
        tenant_id=str(item.tenant_id),
        model_id=item.model_id,
        provider_cost_total=str(item.provider_cost_total),
        billed_total=str(item.billed_total),
        catalog_billed_total=str(item.catalog_billed_total),
        margin=str(item.margin) if item.margin is not None else None,
        has_provider_cost_data=item.has_provider_cost_data,
        unbilled_upstream_cost=str(item.unbilled_upstream_cost),
        unbilled_rows=item.unbilled_rows,
    )


@margin_router.get("/by-tenant-model", response_model=ByTenantModelResponse)
async def get_margin_by_tenant_model(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    window: Annotated[str, Query()] = "month",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    tenant_id: Annotated[str | None, Query()] = None,
) -> ByTenantModelResponse:
    """M4/M10: the one paginated list endpoint — keyset over (tenant_id, model_id) ASC,
    limit 1..100 default 50, opaque base64 cursor. reconcile_by_tenant_model itself takes no
    limit/cursor (§3 Access pattern) — the keyset slice happens here, in the application
    layer, over the full ordered aggregate."""
    window_start, window_end, _granularity = _compute_window_bounds(window, start, end)
    parsed_limit = _parse_limit(limit)
    tenant_filter = _parse_uuid_filter(tenant_id)
    cursor_tuple = _decode_cursor(cursor) if cursor is not None else None

    try:
        async with asyncio.timeout(_MARGIN_QUERY_TIMEOUT_SECONDS):
            rows = await reconcile_by_tenant_model(session, window_start, window_end)
    except TimeoutError:
        raise MARGIN_QUERY_TIMEOUT.exc() from None

    if tenant_filter is not None:
        rows = tuple(r for r in rows if r.tenant_id == tenant_filter)

    if cursor_tuple is not None:
        cursor_tid, cursor_mid = cursor_tuple
        rows = tuple(r for r in rows if (str(r.tenant_id), r.model_id) > (cursor_tid, cursor_mid))

    page = rows[: parsed_limit + 1]
    has_more = len(page) > parsed_limit
    page = page[:parsed_limit]
    next_cursor = (
        _encode_cursor(str(page[-1].tenant_id), page[-1].model_id) if has_more and page else None
    )

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.margin.view_by_tenant_model",
        target_type="margin",
        target_id=None,
        metadata={"window": window, "tenant_id": tenant_id, "limit": parsed_limit},
    )

    return ByTenantModelResponse(
        items=[_item_to_response(item) for item in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )


# ---------------------------------------------------------------------------
# GET /admin/platform/margin/trend
# ---------------------------------------------------------------------------


def _point_to_response(point: MarginTrendPoint) -> MarginTrendPointResponse:
    return MarginTrendPointResponse(
        bucket_start=point.bucket_start.isoformat(),
        provider_cost_total=str(point.provider_cost_total),
        billed_total=str(point.billed_total),
        catalog_billed_total=str(point.catalog_billed_total),
        margin=str(point.margin) if point.margin is not None else None,
        has_provider_cost_data=point.has_provider_cost_data,
    )


@margin_router.get("/trend", response_model=TrendResponse)
async def get_margin_trend(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    window: Annotated[str, Query()] = "month",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
    tenant_id: Annotated[str | None, Query()] = None,
) -> TrendResponse:
    """M5: date-bucketed trend, granularity derived from `window` (NOT a separate param) via
    `_compute_window_bounds`'s own third return value."""
    window_start, window_end, granularity = _compute_window_bounds(window, start, end)
    tenant_filter = _parse_uuid_filter(tenant_id)

    try:
        async with asyncio.timeout(_MARGIN_QUERY_TIMEOUT_SECONDS):
            points = await reconcile_trend(
                session, window_start, window_end, granularity, tenant_id=tenant_filter
            )
    except TimeoutError:
        raise MARGIN_QUERY_TIMEOUT.exc() from None

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.margin.view_trend",
        target_type="margin",
        target_id=None,
        metadata={"window": window, "tenant_id": tenant_id},
    )

    return TrendResponse(
        granularity=granularity,
        points=[_point_to_response(p) for p in points],
    )


# ---------------------------------------------------------------------------
# GET /admin/platform/margin/tie-out
# ---------------------------------------------------------------------------


@margin_router.get("/tie-out", response_model=TieOutResponse)
async def get_margin_tie_out(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
    period: Annotated[str | None, Query()] = None,
    tenant_id: Annotated[str | None, Query()] = None,
) -> TieOutResponse:
    """M6/M7: three-way tie-out (ledger vs invoiced vs provider cost) for one UTC calendar
    month, per tenant with EITHER usage or an issued invoice in that period. NEVER writes —
    a mismatch surfaces as `drift_detected` for a human to investigate, never auto-corrected
    (this router has no write path at all)."""
    period_start, period_end = _parse_period(period)
    tenant_filter = _parse_uuid_filter(tenant_id)

    try:
        async with asyncio.timeout(_MARGIN_QUERY_TIMEOUT_SECONDS):
            ledger_rows = await tie_out_ledger_by_tenant(session, period_start, period_end)
            invoices_by_tenant = await _fetch_invoices_for_period(session, period_start)
    except TimeoutError:
        raise MARGIN_QUERY_TIMEOUT.exc() from None

    ledger_by_tenant: dict[uuid.UUID, TenantLedgerPeriod] = {r.tenant_id: r for r in ledger_rows}
    all_tenant_ids = set(ledger_by_tenant) | set(invoices_by_tenant)
    if tenant_filter is not None:
        all_tenant_ids &= {tenant_filter}

    items: list[TieOutItemResponse] = []
    for tid in sorted(all_tenant_ids, key=str):
        ledger = ledger_by_tenant.get(tid)
        ledger_billed_total_usd = (
            ledger.ledger_billed_total_usd if ledger is not None else Decimal("0")
        )
        provider_cost_total_usd = (
            ledger.provider_cost_total_usd if ledger is not None else Decimal("0")
        )

        invoice = invoices_by_tenant.get(tid)
        # DECIDED at freeze (2026-07-12): draft invoices count under pending_invoice — v1
        # never actually produces a 'draft' row (invoice-generation's own auto-issue path
        # always inserts status='issued' directly), but this stays honest even if that
        # changes: only a REAL issued invoice counts as "invoiced", never a draft.
        if invoice is not None and invoice[1] == "issued":
            invoice_id, _status, total_usd, raw_total_usd = invoice
            matched = raw_total_usd == ledger_billed_total_usd
            items.append(
                TieOutItemResponse(
                    tenant_id=str(tid),
                    invoice_id=str(invoice_id),
                    invoice_status="issued",
                    invoiced_total_usd=str(total_usd),
                    invoiced_raw_total_usd=str(raw_total_usd),
                    ledger_billed_total_usd=str(ledger_billed_total_usd),
                    provider_cost_total_usd=str(provider_cost_total_usd),
                    drift_invoiced_vs_ledger=str(raw_total_usd - ledger_billed_total_usd),
                    tie_out_status="matched" if matched else "drift_detected",
                )
            )
        else:
            items.append(
                TieOutItemResponse(
                    tenant_id=str(tid),
                    invoice_id=None,
                    invoice_status="pending_invoice",
                    invoiced_total_usd=None,
                    invoiced_raw_total_usd=None,
                    ledger_billed_total_usd=str(ledger_billed_total_usd),
                    provider_cost_total_usd=str(provider_cost_total_usd),
                    drift_invoiced_vs_ledger=None,
                    tie_out_status="pending_invoice",
                )
            )

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.margin.view_tie_out",
        target_type="margin",
        target_id=None,
        metadata={"period": period, "tenant_id": tenant_id},
    )

    return TieOutResponse(
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        items=items,
    )
