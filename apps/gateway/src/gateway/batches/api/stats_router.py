"""Admin API router for tenant-wide batch statistics (read-only).

Contract FROZEN @ v1 (batch-dashboard-surface TASK.md §3):
  GET /admin/batches/stats — owner or admin only; member (or any other role) -> 403
    -> 200 {savings_usd, total_requests, status_counts}

savings_usd is ALWAYS "0.00" today — an explicit constant, NOT a query. list_price_usd does
not exist in usage_records yet (confirmed repo-wide, 2026-07-03) — that column belongs to
batch-billing-accuracy (a separate, not-yet-started task). Swap this constant for a real
sum(list_price_usd - cost_usd) query over usage_source="batch" the moment that task lands
the column (tracked as this task's own §7 SPEC delta, not silently forgotten).

total_requests + status_counts ARE real queries (BatchJobRepository.tenant_status_counts),
tenant-scoped via the authenticated session's tenant_id — never another tenant's data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.core.db import get_session
from gateway.keys.api.deps import require_owner_or_admin
from gateway.tenants.domain.entities import Identity

batch_stats_router = APIRouter(prefix="/admin/batches", tags=["batches"])

# Application-level constant — see module docstring. NOT derived from any query.
_SAVINGS_USD_PENDING_BILLING_ACCURACY = "0.00"


class BatchStatsResponse(BaseModel):
    savings_usd: str
    total_requests: int
    status_counts: dict[str, int]


@batch_stats_router.get("/stats", response_model=BatchStatsResponse)
async def get_batch_stats(
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchStatsResponse:
    """GET /admin/batches/stats — read-only tenant-wide batch statistics.

    Requires role owner or admin; every other role -> 403 ERR_AUTH_FORBIDDEN (matches
    /admin/cache's PUT dependency — this whole endpoint is admin-gated, not just a write).
    """
    repo = BatchJobRepository(session)
    status_counts = await repo.tenant_status_counts(tenant_id=identity.tenant_id)
    total_requests = sum(status_counts.values())

    return BatchStatsResponse(
        savings_usd=_SAVINGS_USD_PENDING_BILLING_ACCURACY,
        total_requests=total_requests,
        status_counts=status_counts,
    )
