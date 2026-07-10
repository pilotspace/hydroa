"""GET /admin/guardrails/analytics — windowed guardrail-verdict aggregation.

Contract (FROZEN @ v1 — guardrail-analytics TASK.md §3):
  - OPS_READ-gated: owner/admin/operator/billing_admin/viewer pass; member -> 403
    (reuses gateway.usage.api.router._require_ops_read verbatim — the SAME gate
    /admin/alerts, /admin/health/upstreams, /admin/ratelimits, /admin/slo already use).
  - window="day"|"week"|"month" (default "month"), optional start/end ISO-date override —
    reuses gateway.usage.api.router._compute_window_bounds verbatim (imported, not
    duplicated), so a bad window/date maps to the SAME 422 codes /admin/spend uses.
  - Optional group_by="guardrail"|"policy_source"|"key_id" — exactly one breakdown at a
    time; omitted -> breakdown=null (never 422). An unknown value -> 422
    ERR_PAYLOAD_GROUP_BY_INVALID (reuses PAYLOAD_GROUP_BY_INVALID verbatim).
  - Optional key_id filter narrows every query to that key; a key_id not belonging to the
    caller's tenant (or not existing) -> 404 ERR_KEY_NOT_FOUND (reuses
    KEY_NOT_FOUND_IN_TENANT verbatim, no existence leak).
  - Tenant isolation: every query includes WHERE tenant_id = :tenant_id.
  - Empty window -> 200 with explicit zero totals + empty buckets/breakdown (never 404).
  - hits = evaluations - passed (any non-"passed" action counts as a hit).
  - READ-ONLY — never writes a row.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    KEY_NOT_FOUND_IN_TENANT,
    PAYLOAD_GROUP_BY_INVALID,
    PAYLOAD_KEY_ID_UUID_INVALID,
)
from gateway.guardrail_analytics.api.schemas import (
    GuardrailAnalyticsBucket,
    GuardrailAnalyticsResponse,
    GuardrailAnalyticsTotals,
    GuardrailBreakdownItem,
    KeyBreakdownItem,
    PolicySourceBreakdownItem,
)
from gateway.tenants.domain.entities import Identity

# Deliberate reuse of usage/api/router.py's private helpers, verbatim, per §3 CONTRACT
# ("reuses _compute_window_bounds verbatim (imported, not duplicated)" / "reuses
# gateway.usage.api.router._require_ops_read verbatim"): the SAME window-bounds
# parsing + OPS_READ gate every other admin-analytics endpoint (/admin/spend,
# /admin/slo, /admin/alerts) already uses. reportPrivateUsage is a mechanical
# pyright friction for an intentional cross-module reuse, not a design smell.
from gateway.usage.api.router import (
    _compute_window_bounds,  # pyright: ignore[reportPrivateUsage]
    _require_ops_read,  # pyright: ignore[reportPrivateUsage]
)

guardrail_analytics_router = APIRouter(prefix="/admin/guardrails/analytics", tags=["guardrails"])

_VALID_GROUP_BY = {"guardrail", "policy_source", "key_id"}

# Column list shared by the bucket query and every breakdown query (conditional-SUM
# idiom, mirrors usage/api/router.py:get_slo's SUM(CASE WHEN ... THEN 1 ELSE 0 END)).
_COUNT_COLUMNS = (
    "  COUNT(*) AS evaluations,"
    "  SUM(CASE WHEN action != 'passed' THEN 1 ELSE 0 END) AS hits,"
    "  SUM(CASE WHEN action = 'blocked' THEN 1 ELSE 0 END) AS blocked,"
    "  SUM(CASE WHEN action = 'masked' THEN 1 ELSE 0 END) AS masked,"
    "  SUM(CASE WHEN action = 'audited' THEN 1 ELSE 0 END) AS audited,"
    "  SUM(CASE WHEN action = 'passed' THEN 1 ELSE 0 END) AS passed,"
    "  SUM(CASE WHEN action = 'error' THEN 1 ELSE 0 END) AS error,"
    "  SUM(CASE WHEN action = 'unchecked' THEN 1 ELSE 0 END) AS unchecked,"
    "  SUM(CASE WHEN action = 'budget_exceeded' THEN 1 ELSE 0 END) AS budget_exceeded"
)


def _counts_from_row(row: object, offset: int) -> dict[str, int]:
    """Extract the 9 conditional-SUM count fields starting at `offset` in a fetched row."""
    return {
        "evaluations": int(row[offset]),  # type: ignore[index]
        "hits": int(row[offset + 1]),  # type: ignore[index]
        "blocked": int(row[offset + 2]),  # type: ignore[index]
        "masked": int(row[offset + 3]),  # type: ignore[index]
        "audited": int(row[offset + 4]),  # type: ignore[index]
        "passed": int(row[offset + 5]),  # type: ignore[index]
        "error": int(row[offset + 6]),  # type: ignore[index]
        "unchecked": int(row[offset + 7]),  # type: ignore[index]
        "budget_exceeded": int(row[offset + 8]),  # type: ignore[index]
    }


_ZERO_COUNTS: dict[str, int] = {
    "evaluations": 0,
    "hits": 0,
    "blocked": 0,
    "masked": 0,
    "audited": 0,
    "passed": 0,
    "error": 0,
    "unchecked": 0,
    "budget_exceeded": 0,
}


@guardrail_analytics_router.get("", response_model=GuardrailAnalyticsResponse)
async def get_guardrail_analytics(
    request: Request,
    identity: Annotated[Identity, Depends(_require_ops_read)],
    session: Annotated[AsyncSession, Depends(get_session)],
    window: Annotated[str, Query()] = "month",
    key_id: Annotated[str | None, Query()] = None,
    group_by: Annotated[str | None, Query()] = None,
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> GuardrailAnalyticsResponse:
    """Return windowed guardrail-verdict aggregates from guardrail_verdict_events."""
    _ = request  # unused — identity/session are the only deps this handler needs
    tenant_id: uuid.UUID = identity.tenant_id

    # Compute window boundaries FIRST (422 on bad window/date before any query executes).
    window_start, window_end, granularity = _compute_window_bounds(window, start, end)

    # Validate optional key_id filter — must belong to caller's tenant.
    key_uuid: uuid.UUID | None = None
    if key_id is not None:
        try:
            key_uuid = uuid.UUID(key_id)
        except ValueError:
            raise PAYLOAD_KEY_ID_UUID_INVALID.exc(key_id=key_id) from None
        key_row = (
            await session.execute(
                text("SELECT id FROM api_keys WHERE id = :kid AND tenant_id = :tid"),
                {"kid": str(key_uuid), "tid": str(tenant_id)},
            )
        ).fetchone()
        if key_row is None:
            raise KEY_NOT_FOUND_IN_TENANT.exc()

    # Validate group_by whitelist (422 for unknown values) BEFORE any breakdown query.
    if group_by is not None and group_by not in _VALID_GROUP_BY:
        raise PAYLOAD_GROUP_BY_INVALID.exc(
            valid=", ".join(sorted(_VALID_GROUP_BY)), group_by=group_by
        )

    params: dict[str, object] = {
        "tenant_id": str(tenant_id),
        "window_start": window_start.replace(tzinfo=None),  # asyncpg expects naive UTC
        "window_end": window_end.replace(tzinfo=None),
    }
    key_filter = ""
    if key_uuid is not None:
        key_filter = " AND key_id = :key_id"
        params["key_id"] = str(key_uuid)

    # ── Bucket aggregation query (per §3 contract SQL) ──────────────────────────
    # granularity is validated against the day/week/month whitelist by
    # _compute_window_bounds above; key_filter is a hardcoded literal string
    # " AND key_id = :key_id" or "". S608 is a false positive — no user-supplied
    # values are interpolated into the SQL text.
    _bucket_sql = text(
        "SELECT"
        f"  date_trunc('{granularity}', created_at AT TIME ZONE 'UTC') AS bucket_start,"
        f"{_COUNT_COLUMNS}"
        " FROM guardrail_verdict_events"
        " WHERE tenant_id = :tenant_id"
        "   AND created_at >= :window_start"
        "   AND created_at <  :window_end"
        f"{key_filter}"
        " GROUP BY bucket_start"
        " ORDER BY bucket_start ASC"
    )
    bucket_rows = (await session.execute(_bucket_sql, params)).fetchall()

    buckets: list[GuardrailAnalyticsBucket] = []
    totals_acc = dict(_ZERO_COUNTS)
    for row in bucket_rows:
        bs = row[0]
        bucket_start_str = bs.isoformat() if hasattr(bs, "isoformat") else str(bs)
        counts = _counts_from_row(row, 1)
        buckets.append(GuardrailAnalyticsBucket(bucket_start=bucket_start_str, **counts))
        for k, v in counts.items():
            totals_acc[k] += v

    # ── Totals ────────────────────────────────────────────────────────────────
    # Computed as the sum of bucket rows (same WHERE clause -> exact reconciliation),
    # mirrors usage/api/router.py:get_spend's own totals-from-buckets approach.
    if buckets:
        totals_bucket_start = buckets[0].bucket_start
    else:
        totals_bucket_start = window_start.replace(tzinfo=None).isoformat()
    effective_end = window_end - datetime.timedelta(microseconds=1)
    totals_bucket_end = effective_end.replace(tzinfo=None).isoformat()

    totals = GuardrailAnalyticsTotals(
        bucket_start=totals_bucket_start,
        bucket_end=totals_bucket_end,
        **totals_acc,
    )

    # ── Breakdown (group_by=guardrail|policy_source|key_id) ─────────────────────
    breakdown: (
        list[GuardrailBreakdownItem]
        | list[PolicySourceBreakdownItem]
        | list[KeyBreakdownItem]
        | None
    ) = None
    if group_by is not None:
        _breakdown_sql = text(
            "SELECT"
            f"  {group_by},"
            f"{_COUNT_COLUMNS}"
            " FROM guardrail_verdict_events"
            " WHERE tenant_id = :tenant_id"
            "   AND created_at >= :window_start"
            "   AND created_at <  :window_end"
            f"{key_filter}"
            f" GROUP BY {group_by}"
            " ORDER BY evaluations DESC"
        )
        breakdown_rows = (await session.execute(_breakdown_sql, params)).fetchall()
        if group_by == "guardrail":
            breakdown = [
                GuardrailBreakdownItem(guardrail=str(row[0]), **_counts_from_row(row, 1))
                for row in breakdown_rows
            ]
        elif group_by == "policy_source":
            breakdown = [
                PolicySourceBreakdownItem(policy_source=str(row[0]), **_counts_from_row(row, 1))
                for row in breakdown_rows
            ]
        else:  # group_by == "key_id"
            breakdown = [
                KeyBreakdownItem(key_id=uuid.UUID(str(row[0])), **_counts_from_row(row, 1))
                for row in breakdown_rows
            ]

    return GuardrailAnalyticsResponse(
        window=window,
        bucket_size=granularity,
        totals=totals,
        buckets=buckets,
        breakdown=breakdown,
    )
