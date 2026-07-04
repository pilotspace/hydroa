"""Admin API router for tenant-wide batch statistics (read-only).

Contract FROZEN @ v1 (batch-dashboard-surface TASK.md §3) + additive extension FROZEN @
v1 (batch-observability-scaffolding TASK.md §3):
  GET /admin/batches/stats — owner or admin only; member (or any other role) -> 403
    -> 200 {savings_usd, total_requests, status_counts, avg_window_wait_ms,
            avg_completion_latency_ms, completion_tpm}

savings_usd is ALWAYS "0.00" today — an explicit constant, NOT a query. list_price_usd does
not exist in usage_records yet (confirmed repo-wide, 2026-07-03) — that column belongs to
batch-billing-accuracy (a separate, not-yet-started task). Swap this constant for a real
sum(list_price_usd - cost_usd) query over usage_source="batch" the moment that task lands
the column (tracked as this task's own §7 SPEC delta, not silently forgotten).

total_requests + status_counts ARE real queries (BatchJobRepository.tenant_status_counts),
tenant-scoped via the authenticated session's tenant_id — never another tenant's data.

avg_window_wait_ms is ALSO real: BatchWindowBuffer._CLAIM_DUE_LUA additively records every
successful claim's elapsed wait time into a per-tenant Redis sum+count pair; this handler
reads it fail-open (Redis error / absent client / zero samples -> None, never a fabricated
0.0), mirroring usage/api/router.py:_read_ratelimit_counters's exact convention.

avg_completion_latency_ms and completion_tpm are ALWAYS None today (see the two
_..._PENDING_ADAPTERS constants below) — same honest-placeholder shape as
_SAVINGS_USD_PENDING_BILLING_ACCURACY: no live BatchProcessor exists yet (batch_processor
is hardcoded None in main.py), pending v58's openai-batch-adapter/anthropic-batch-adapter +
batch-billing-accuracy (none of which exist in this repo yet).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.core.db import get_session
from gateway.keys.api.deps import require_owner_or_admin

# batch_window_buffer.py's own frozen naming convention (matching _items_key/
# _started_key) mandates these stay leading-underscore; the batch-observability-
# scaffolding TASK.md §3 contract mandates THIS module reads the exact same keys
# _CLAIM_DUE_LUA writes — hence the targeted reportPrivateUsage suppression (same
# idiom as proxy/application/images_use_case.py's _fire_record_with_raw import).
from gateway.proxy.infrastructure.batch_window_buffer import (
    _wait_count_key,  # pyright: ignore[reportPrivateUsage]
    _wait_sum_key,  # pyright: ignore[reportPrivateUsage]
)
from gateway.tenants.domain.entities import Identity

batch_stats_router = APIRouter(prefix="/admin/batches", tags=["batches"])

# Application-level constant — see module docstring. NOT derived from any query.
_SAVINGS_USD_PENDING_BILLING_ACCURACY = "0.00"

# avg_completion_latency_ms / completion_tpm cannot hold real data until v58's
# openai-batch-adapter / anthropic-batch-adapter + batch-billing-accuracy land — see module
# docstring. Same honest-placeholder shape as _SAVINGS_USD_PENDING_BILLING_ACCURACY above.
_COMPLETION_LATENCY_PENDING_ADAPTERS: float | None = None
_COMPLETION_TPM_PENDING_ADAPTERS: float | None = None

# Bounds the new window-wait Redis read below — matches usage/api/router.py's
# _RATELIMIT_REDIS_TIMEOUT_SECONDS convention exactly (design-for-failure: every new IO
# path gets a bound, never an unbounded await).
_WINDOW_WAIT_REDIS_TIMEOUT_SECONDS = 5.0


class BatchStatsResponse(BaseModel):
    savings_usd: str
    total_requests: int
    status_counts: dict[str, int]
    avg_window_wait_ms: float | None = None
    avg_completion_latency_ms: float | None = None
    completion_tpm: float | None = None


async def _read_window_wait_average(redis_client: object, tenant_id: uuid.UUID) -> float | None:
    """Read the live avg_window_wait_ms aggregate for tenant_id, READ-ONLY.

    Fail-open: ANY Redis error, an absent client, or zero samples so far -> None (never a
    fabricated 0.0) — mirrors usage/api/router.py:_read_ratelimit_counters's EXACT
    convention ("never 0, never 500"). The stored wait_sum is epoch-seconds (see
    BatchWindowBuffer._CLAIM_DUE_LUA's `elapsed`); converted to milliseconds here to match
    observability/middleware.py's own duration_ms rounding convention (round(x, 3)).

    Reads both keys via a single MGET, not two sequential GETs: _CLAIM_DUE_LUA writes them
    together inside one atomic EVAL, so two separate round trips can straddle a concurrent
    write (sum read before it lands, count read after) and fabricate a 0.0 average. MGET is
    itself a single command, so it always observes both keys at the same instant.
    """
    if redis_client is None:
        return None
    try:
        async with asyncio.timeout(_WINDOW_WAIT_REDIS_TIMEOUT_SECONDS):
            raw_sum, raw_count = await redis_client.mget(  # type: ignore[attr-defined]
                _wait_sum_key(tenant_id), _wait_count_key(tenant_id)
            )
            # int()/float() stay INSIDE the try — a corrupted/non-numeric stored value
            # must fail open (None) too, never surface as an uncaught ValueError -> 500
            # (same structural placement as _read_ratelimit_counters's own casts).
            count = int(raw_count) if raw_count is not None else 0
            if count == 0:
                return None  # no samples yet (incl. "key absent") — never a fabricated 0.0
            total = float(raw_sum) if raw_sum is not None else 0.0
    except (RedisError, OSError, ValueError, TimeoutError):
        return None  # fail-open — genuinely unknown, never a fabricated 0.0
    return round((total / count) * 1000.0, 3)


@batch_stats_router.get("/stats", response_model=BatchStatsResponse)
async def get_batch_stats(
    request: Request,
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

    avg_window_wait_ms = await _read_window_wait_average(
        getattr(request.app.state, "redis_client", None), identity.tenant_id
    )

    return BatchStatsResponse(
        savings_usd=_SAVINGS_USD_PENDING_BILLING_ACCURACY,
        total_requests=total_requests,
        status_counts=status_counts,
        avg_window_wait_ms=avg_window_wait_ms,
        avg_completion_latency_ms=_COMPLETION_LATENCY_PENDING_ADAPTERS,
        completion_tpm=_COMPLETION_TPM_PENDING_ADAPTERS,
    )
