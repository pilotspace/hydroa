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
class TenantReconciliation:
    """One tenant's reconciliation drift within a window (operator-wide breakdown).

    Provider-only, mirroring the global drift semantics: tenants with no `cost_basis='provider'`
    rows in the window do not appear (catalog usage carries no upstream truth, hence no drift).
    """

    tenant_id: uuid.UUID
    provider_cost_total: Decimal
    billed_total: Decimal
    drift: Decimal  # provider_cost_total - billed_total
    unbilled_upstream_cost: Decimal
    unbilled_rows: int


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


@dataclass(frozen=True)
class CostBasisBreach:
    """One recorder-invariant breach: a `cost_basis='catalog'` row carrying a provider_cost.

    Catalog rows have no upstream truth, so they must never carry a provider_cost. A row that
    does is a recorder bug (the cost basis was misclassified) — it is silently excluded from
    drift and the unbilled-upstream leak filter, so it would otherwise leak unnoticed. The audit
    surfaces these rows so the operator can detect and fix the misclassification.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    provider_cost: Decimal
    created_at: datetime


@dataclass(frozen=True)
class UnrecoveredDisconnect:
    """A residual client-disconnect row that is billed $0 with no recovery (v33).

    A `client_disconnect` row with `provider_cost IS NULL` and `cost_usd = 0` and no
    `openrouter_recovered` sibling: the disconnect cost upstream money but we recorded a silent
    $0 with no estimate (zero-token / no-frame) and no recovery path. The audit enumerates these
    so an operator can investigate — the partial-token case is instead stamped at record time.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    model_id: str
    created_at: datetime


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


async def reconcile_by_tenant(
    session: AsyncSession,
    window_from: datetime,
    window_to: datetime,
) -> tuple[TenantReconciliation, ...]:
    """Per-tenant provider drift over `created_at ∈ [window_from, window_to)` (operator-wide).

    READ-ONLY: one SELECT-only `GROUP BY tenant_id` aggregate over the cost_basis='provider'
    rows (the rows that carry upstream truth). Cross-tenant by design — the caller MUST be the
    platform operator (the audited tenant-scope exception). Rows are sorted by tenant_id for
    determinism. Raises ``ValueError`` on an inverted window; an empty window → ``()``.
    """
    window_from = _as_naive_utc(window_from)
    window_to = _as_naive_utc(window_to)
    if window_to < window_from:
        raise ValueError(
            f"inverted reconciliation window: window_to {window_to!r} < window_from {window_from!r}"
        )

    rows = (
        await session.execute(
            text(
                "SELECT tenant_id,"
                "  COALESCE(SUM(provider_cost), 0) AS provider_cost_total,"
                "  COALESCE(SUM(cost_usd), 0) AS billed_total,"
                "  COALESCE(SUM(provider_cost) FILTER"
                "    (WHERE provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider'), 0)"
                "    AS unbilled_upstream_cost,"
                "  COUNT(*) FILTER"
                "    (WHERE provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider')"
                "    AS unbilled_rows"
                " FROM usage_records"
                " WHERE created_at >= :from AND created_at < :to AND cost_basis = 'provider'"
                " GROUP BY tenant_id ORDER BY tenant_id"
            ),
            {"from": window_from, "to": window_to},
        )
    ).fetchall()

    return tuple(
        TenantReconciliation(
            tenant_id=uuid.UUID(str(row[0])),
            provider_cost_total=_money(row[1]),
            billed_total=_money(row[2]),
            drift=_money(row[1]) - _money(row[2]),
            unbilled_upstream_cost=_money(row[3]),
            unbilled_rows=int(row[4]),
        )
        for row in rows
    )


async def audit_cost_basis_breaches(
    session: AsyncSession,
    window_from: datetime | None = None,
    window_to: datetime | None = None,
) -> tuple[CostBasisBreach, ...]:
    """Surface recorder-invariant breaches: `cost_basis='catalog'` rows carrying a provider_cost.

    A catalog row has no upstream truth and must never carry `provider_cost`; one that does was
    misclassified and is silently excluded from drift and the unbilled-upstream leak filter — so
    it would otherwise leak unnoticed. READ-ONLY: one SELECT-only scan, sorted by `created_at` for
    determinism. The window is OPTIONAL: with both bounds omitted the audit scans the whole ledger
    (cheap, the breach is rare); when given it is half-open `[from, to)`. Operator-wide (all
    tenants) — the caller MUST be the platform operator. Raises ``ValueError`` on an inverted
    window.
    """
    clause = ""
    params: dict[str, object] = {}
    if window_from is not None and window_to is not None:
        wf = _as_naive_utc(window_from)
        wt = _as_naive_utc(window_to)
        if wt < wf:
            raise ValueError(
                f"inverted reconciliation window: window_to {wt!r} < window_from {wf!r}"
            )
        clause = " AND created_at >= :from AND created_at < :to"
        params = {"from": wf, "to": wt}
    elif window_from is not None or window_to is not None:
        raise ValueError(
            "audit_cost_basis_breaches: pass both window_from and window_to, or neither"
        )

    rows = (
        await session.execute(
            text(
                "SELECT id, tenant_id, provider_cost, created_at"  # noqa: S608 (static clause, bound params)
                " FROM usage_records"
                " WHERE cost_basis = 'catalog'"
                "   AND provider_cost IS NOT NULL AND provider_cost > 0" + clause
                + " ORDER BY created_at"
            ),
            params,
        )
    ).fetchall()

    return tuple(
        CostBasisBreach(
            id=uuid.UUID(str(row[0])),
            tenant_id=uuid.UUID(str(row[1])),
            provider_cost=_money(row[2]),
            created_at=row[3],
        )
        for row in rows
    )


async def audit_unrecovered_disconnects(
    session: AsyncSession,
    window_from: datetime | None = None,
    window_to: datetime | None = None,
) -> tuple[UnrecoveredDisconnect, ...]:
    """Surface residual silent-$0 client-disconnect rows: no estimate, no recovery (v33).

    A `usage_source='client_disconnect'` row with `provider_cost IS NULL` and `cost_usd = 0` and
    NO `openrouter_recovered` sibling on its generation id — the disconnect cost upstream money
    but was recorded as a silent $0 with nothing to estimate (zero-token / no-frame) and no
    out-of-band recovery. The partial-token case is stamped at record time, so what remains here
    is the genuinely unrecoverable residue an operator should investigate. READ-ONLY: one
    SELECT-only scan, ordered by `created_at`. The window is OPTIONAL (both bounds or neither);
    half-open `[from, to)` when given. Raises ``ValueError`` on an inverted window.
    """
    clause = ""
    params: dict[str, object] = {}
    if window_from is not None and window_to is not None:
        wf = _as_naive_utc(window_from)
        wt = _as_naive_utc(window_to)
        if wt < wf:
            raise ValueError(
                f"inverted reconciliation window: window_to {wt!r} < window_from {wf!r}"
            )
        clause = " AND u.created_at >= :from AND u.created_at < :to"
        params = {"from": wf, "to": wt}
    elif window_from is not None or window_to is not None:
        raise ValueError(
            "audit_unrecovered_disconnects: pass both window_from and window_to, or neither"
        )

    rows = (
        await session.execute(
            text(
                "SELECT u.id, u.tenant_id, u.model_id, u.created_at"  # noqa: S608 (static clause, bound params)
                " FROM usage_records u"
                " WHERE u.usage_source = 'client_disconnect'"
                "   AND u.provider_cost IS NULL AND u.cost_usd = 0"
                "   AND NOT EXISTS ("
                "         SELECT 1 FROM usage_records r"
                "          WHERE r.provider_generation_id = u.provider_generation_id"
                "            AND r.usage_source = 'openrouter_recovered')" + clause
                + " ORDER BY u.created_at"
            ),
            params,
        )
    ).fetchall()

    return tuple(
        UnrecoveredDisconnect(
            id=uuid.UUID(str(row[0])),
            tenant_id=uuid.UUID(str(row[1])),
            model_id=str(row[2]),
            created_at=row[3],
        )
        for row in rows
    )
