"""Reconciliation aggregate — Σ(provider_cost) vs Σ(billed) over a [from,to] window (§3).

A pure, READ-ONLY measurement over the append-only `usage_records` ledger. For a half-open
`[window_from, window_to)` window it compares the upstream-reported provider cost against what
we billed the tenant, and surfaces the UNBILLED-UPSTREAM rows (`provider_cost > 0 ∧ cost_usd = 0`
— we paid the upstream but billed the user $0) grouped by `usage_source`. The reconciliation
endpoint and the drift-alert both consume this single primitive (the milestone's shared metric).

Drift reconciles ONLY `cost_basis='provider'` rows — only they carry an authoritative upstream
cost. `cost_basis='catalog'` rows have no provider truth and are reported separately as
`catalog_billed_total`, never folded into drift. Money stays `Decimal` end-to-end (asyncpg
returns Numeric as Decimal); two SELECT-only queries keep that precision (a JSON-agg single
statement would round provider_cost through a float).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class SourceBreakdown:
    """Per-`usage_source` rollup of the unbilled-upstream rows."""

    usage_source: str
    rows: int
    provider_cost: Decimal  # Σ provider_cost of unbilled rows with this source


@dataclass(frozen=True)
class ReconciliationSummary:
    """The window's reconciliation result (all money fields are Decimal)."""

    window_from: datetime  # inclusive
    window_to: datetime  # exclusive
    provider_cost_total: Decimal  # Σ provider_cost over cost_basis='provider'
    billed_total: Decimal  # Σ cost_usd over cost_basis='provider'
    drift: Decimal  # provider_cost_total - billed_total
    unbilled_upstream_cost: Decimal  # Σ provider_cost (>0, cost_usd=0, cost_basis='provider')
    unbilled_rows: int  # COUNT(*) of those rows
    catalog_billed_total: Decimal  # Σ cost_usd over cost_basis='catalog'
    by_source: tuple[SourceBreakdown, ...]  # unbilled rows grouped by usage_source, sorted


def _money(value: object) -> Decimal:
    """Coerce a DB-returned numeric to Decimal without going through float."""
    return Decimal(str(value))


def _as_naive_utc(dt: datetime) -> datetime:
    """Normalize a window bound to naive UTC for the `created_at` comparison.

    asyncpg expects NAIVE UTC datetimes when binding the `usage_records.created_at` parameter —
    the existing spend query strips tz the same way (`window_start.replace(tzinfo=None)  # asyncpg
    expects naive UTC`, usage/api/router.py). The reconciliation endpoint passes UTC-AWARE bounds,
    so convert aware → UTC then drop tzinfo; a naive bound is assumed UTC and passes through.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


async def reconcile_window(
    session: AsyncSession,
    window_from: datetime,
    window_to: datetime,
    tenant_id: uuid.UUID | None = None,
) -> ReconciliationSummary:
    """Reconcile provider cost vs billed over `created_at ∈ [window_from, window_to)`.

    Tenant-scoped when `tenant_id` is given, operator-wide (all tenants) when None. READ-ONLY:
    two SELECT-only aggregate queries, the ledger is never written. Raises ``ValueError`` on an
    inverted window (`window_to < window_from`); an empty window (`==`) returns explicit zeros.
    """
    window_from = _as_naive_utc(window_from)
    window_to = _as_naive_utc(window_to)
    if window_to < window_from:
        raise ValueError(
            f"inverted reconciliation window: window_to {window_to!r} < window_from {window_from!r}"
        )

    tenant_clause = " AND tenant_id = :tid" if tenant_id is not None else ""
    params: dict[str, object] = {"from": window_from, "to": window_to}
    if tenant_id is not None:
        params["tid"] = str(tenant_id)

    # Query 1 — the scalar totals (one row; COALESCE(...,0) so an empty window → explicit zeros).
    totals_row = (
        await session.execute(
            text(
                "SELECT"
                "  COALESCE(SUM(provider_cost) FILTER (WHERE cost_basis = 'provider'), 0)"
                "    AS provider_cost_total,"
                "  COALESCE(SUM(cost_usd) FILTER (WHERE cost_basis = 'provider'), 0)"
                "    AS billed_total,"
                "  COALESCE(SUM(cost_usd) FILTER (WHERE cost_basis = 'catalog'), 0)"
                "    AS catalog_billed_total,"
                "  COALESCE(SUM(provider_cost) FILTER"
                "    (WHERE provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider'), 0)"
                "    AS unbilled_upstream_cost,"
                "  COUNT(*) FILTER"
                "    (WHERE provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider')"
                "    AS unbilled_rows"
                " FROM usage_records"
                " WHERE created_at >= :from AND created_at < :to" + tenant_clause
            ),
            params,
        )
    ).fetchone()

    provider_cost_total = _money(totals_row[0]) if totals_row else Decimal("0")
    billed_total = _money(totals_row[1]) if totals_row else Decimal("0")
    catalog_billed_total = _money(totals_row[2]) if totals_row else Decimal("0")
    unbilled_upstream_cost = _money(totals_row[3]) if totals_row else Decimal("0")
    unbilled_rows = int(totals_row[4]) if totals_row else 0

    # Query 2 — the unbilled-upstream rows grouped by usage_source (sorted for determinism).
    source_rows = (
        await session.execute(
            text(
                "SELECT usage_source,"  # noqa: S608 (static clause, bound params)
                "  COUNT(*) AS rows,"
                "  COALESCE(SUM(provider_cost), 0) AS provider_cost"
                " FROM usage_records"
                " WHERE provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider'"
                "   AND created_at >= :from AND created_at < :to"
                + tenant_clause
                + " GROUP BY usage_source ORDER BY usage_source"
            ),
            params,
        )
    ).fetchall()

    by_source = tuple(
        SourceBreakdown(usage_source=str(row[0]), rows=int(row[1]), provider_cost=_money(row[2]))
        for row in source_rows
    )

    return ReconciliationSummary(
        window_from=window_from,
        window_to=window_to,
        provider_cost_total=provider_cost_total,
        billed_total=billed_total,
        drift=provider_cost_total - billed_total,
        unbilled_upstream_cost=unbilled_upstream_cost,
        unbilled_rows=unbilled_rows,
        catalog_billed_total=catalog_billed_total,
        by_source=by_source,
    )
