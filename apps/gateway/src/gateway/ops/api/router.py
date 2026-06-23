"""GET /ops/reconciliation — operator-wide (cross-tenant) reconciliation (TASK.md §3 FROZEN @ v1).

The audited exception to the tenant-scoping invariant: behind the ops-auth surface
(`require_ops` — mTLS operator cert via XFCC, never a tenant JWT), it reads drift across ALL
tenants. READ-ONLY (two SELECT-only aggregates) under a bounded timeout (IO design-for-failure;
read-only ⇒ no retry). Window params reuse the tenant endpoint's `_compute_window_bounds`
(422 ERR_PAYLOAD_INVALID on a bad window/date) — identical validation, by contract.

Auth model:   mTLS client cert -> Envoy -> x-forwarded-client-cert (XFCC) -> require_ops
Denial:        valid tenant JWT -> 403 ERR_OPS_FORBIDDEN; else byte-identical 401
Default-OFF:   empty fingerprint allow-list -> nobody authorized (fail-closed)
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.ops.api.deps import require_ops
from gateway.ops.api.schemas import OperatorReconciliationResponse, TenantReconciliationItem
from gateway.tenants.infrastructure.ops_cert_verifier import OpsIdentity

# Reuse the sibling endpoint's window-bounds validator VERBATIM — the §3 contract requires
# "reuse the existing reconciliation param validation" so /ops and /admin reconciliation
# validate windows byte-identically (422 ERR_PAYLOAD_INVALID). It is module-private to the
# usage router (refactoring its home is out of this task's scope), hence the explicit ignore.
from gateway.usage.api.router import _compute_window_bounds  # pyright: ignore[reportPrivateUsage]
from gateway.usage.api.schemas import ReconciliationSourceItem
from gateway.usage.application.reconciliation import reconcile_by_tenant, reconcile_window

ops_router = APIRouter(prefix="/ops")

# Bounded timeout for the read-only cross-tenant aggregates (IO design-for-failure).
_OPS_RECON_TIMEOUT_SECONDS = 30.0


@ops_router.get("/reconciliation", response_model=OperatorReconciliationResponse)
async def get_ops_reconciliation(
    _operator: Annotated[OpsIdentity, Depends(require_ops)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query()] = "month",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> OperatorReconciliationResponse:
    """Cross-tenant reconciliation for a window (operator-only). READ-ONLY."""
    window_start, window_end, _granularity = _compute_window_bounds(window, start, end)

    async with asyncio.timeout(_OPS_RECON_TIMEOUT_SECONDS):
        summary = await reconcile_window(session, window_start, window_end, tenant_id=None)
        tenants = await reconcile_by_tenant(session, window_start, window_end)

    return OperatorReconciliationResponse(
        window_from=summary.window_from.isoformat(),
        window_to=summary.window_to.isoformat(),
        provider_cost_total=str(summary.provider_cost_total),
        billed_total=str(summary.billed_total),
        drift=str(summary.drift),
        unbilled_upstream_cost=str(summary.unbilled_upstream_cost),
        unbilled_rows=summary.unbilled_rows,
        catalog_billed_total=str(summary.catalog_billed_total),
        by_source=[
            ReconciliationSourceItem(
                usage_source=item.usage_source,
                rows=item.rows,
                provider_cost=str(item.provider_cost),
            )
            for item in summary.by_source
        ],
        by_tenant=[
            TenantReconciliationItem(
                tenant_id=str(t.tenant_id),
                provider_cost_total=str(t.provider_cost_total),
                billed_total=str(t.billed_total),
                drift=str(t.drift),
                unbilled_upstream_cost=str(t.unbilled_upstream_cost),
                unbilled_rows=t.unbilled_rows,
            )
            for t in tenants
        ],
    )
