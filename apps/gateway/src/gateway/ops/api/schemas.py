"""Response schemas for the operator-wide reconciliation endpoint (TASK.md §3 FROZEN @ v1).

The operator view is the v29 tenant reconciliation shape aggregated across ALL tenants, PLUS
a per-tenant breakdown (`by_tenant`). All money fields are str(Decimal) — exact, never float.
The unbilled-by-source rollup reuses the tenant endpoint's ReconciliationSourceItem.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from gateway.usage.api.schemas import ReconciliationSourceItem


class TenantReconciliationItem(BaseModel):
    """One tenant's provider drift within the window (operator breakdown row)."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str  # UUID as str
    provider_cost_total: str  # str(Decimal) — Σ provider_cost, cost_basis='provider'
    billed_total: str  # str(Decimal) — Σ cost_usd, cost_basis='provider'
    drift: str  # str(Decimal) — provider_cost_total - billed_total
    unbilled_upstream_cost: str  # str(Decimal) — Σ provider_cost where pcost>0 AND cost_usd=0
    unbilled_rows: int  # COUNT(*) of those rows


class OperatorReconciliationResponse(BaseModel):
    """Cross-tenant reconciliation for a window: global aggregate + per-tenant breakdown.

    Global fields mirror GET /admin/reconciliation but span EVERY tenant (the audited
    tenant-scope exception). by_tenant lists provider drift per tenant, sorted by tenant_id.
    """

    model_config = ConfigDict(frozen=True)

    window_from: str  # ISO-8601 UTC, inclusive
    window_to: str  # ISO-8601 UTC, exclusive
    provider_cost_total: str  # str(Decimal) — Σ provider_cost, cost_basis='provider', all tenants
    billed_total: str  # str(Decimal) — Σ cost_usd, cost_basis='provider', all tenants
    drift: str  # str(Decimal) — provider_cost_total - billed_total
    unbilled_upstream_cost: str  # str(Decimal) — Σ provider_cost where pcost>0 AND cost_usd=0
    unbilled_rows: int  # COUNT(*) of those rows, all tenants
    catalog_billed_total: str  # str(Decimal) — Σ cost_usd, cost_basis='catalog', all tenants
    by_source: list[ReconciliationSourceItem]  # unbilled rows grouped by usage_source, sorted
    by_tenant: list[TenantReconciliationItem]  # per-tenant provider drift, sorted by tenant_id
