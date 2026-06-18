"""Pydantic response models for GET /admin/usage and GET /admin/spend.

GET /admin/usage contract (FROZEN @ v1):
  200 -> {
    total_cost_usd: str,           # str(Decimal) — exact, no float lossiness
    total_requests: int,
    total_prompt_tokens: int,
    total_completion_tokens: int,
    records: [                     # ≤50 newest, ordered created_at DESC
      {
        id: uuid,
        model_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: str,             # str(Decimal)
        status: int,
        created_at: str            # ISO 8601 timestamptz
      }
    ]
  }

GET /admin/spend contract (FROZEN @ spend-windows — TASK.md §3):
  200 -> {
    window: "day" | "week" | "month",
    bucket_size: "day" | "week" | "month",
    totals: { bucket_start, bucket_end, requests, prompt_tokens, completion_tokens, cost_usd },
    buckets: [ { bucket_start, requests, prompt_tokens, completion_tokens, cost_usd } ],
    breakdown: [ { key_id, requests, prompt_tokens, completion_tokens, cost_usd } ] | null
  }
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class UsageRecordItem(BaseModel):
    """Single record in the /admin/usage response."""

    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str  # str(Decimal) — exact
    status: int
    created_at: str  # ISO 8601


class UsageTotalsResponse(BaseModel):
    """Response body for GET /admin/usage."""

    model_config = ConfigDict(frozen=True)

    total_cost_usd: str  # str(Decimal) — exact
    total_requests: int
    total_prompt_tokens: int
    total_completion_tokens: int
    records: list[UsageRecordItem]


# ── GET /admin/spend schemas (FROZEN @ spend-windows) ──────────────────────────


class SpendTotals(BaseModel):
    """Totals object for the spend window (sum of all buckets)."""

    model_config = ConfigDict(frozen=True)

    bucket_start: str  # ISO-8601 UTC — first bucket start
    bucket_end: str  # ISO-8601 UTC — last bucket end (now() or end param)
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str  # str(Decimal) — exact, never float


class SpendBucket(BaseModel):
    """One date_trunc bucket in the spend window."""

    model_config = ConfigDict(frozen=True)

    bucket_start: str  # ISO-8601 UTC
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str  # str(Decimal) — exact


class SpendBreakdownItem(BaseModel):
    """Per-key breakdown item (returned when group_by=key_id)."""

    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str  # str(Decimal) — exact; sorted DESC


class TeamSpendBreakdownItem(BaseModel):
    """Per-team breakdown item (returned when group_by=team_id)."""

    model_config = ConfigDict(frozen=True)

    team_id: uuid.UUID | None  # null for un-teamed keys bucket
    team_name: str | None  # null for un-teamed keys bucket
    requests: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: str  # str(Decimal) — exact; sorted cost_usd DESC (NULL bucket last)
    # team-attribution additive field: ledger column SUM (historical, at-request-time)
    # "0" when no ledger rows have team_id set yet; reconcilable against cost_usd.
    ledger_cost_usd: str  # str(Decimal) — exact; "0" when no attributed rows exist


class SpendWindowResponse(BaseModel):
    """Response body for GET /admin/spend."""

    model_config = ConfigDict(frozen=True)

    window: str  # "day" | "week" | "month"
    bucket_size: str  # granularity of buckets list
    totals: SpendTotals
    buckets: list[SpendBucket]
    # Omitted (None) when group_by is not supplied.
    breakdown: list[SpendBreakdownItem] | list[TeamSpendBreakdownItem] | None = None


# ── GET /admin/reconciliation schemas (FROZEN @ reconciliation-endpoint v1) ─────


class ReconciliationSourceItem(BaseModel):
    """Per-usage_source rollup of the unbilled-upstream rows in the window."""

    model_config = ConfigDict(frozen=True)

    usage_source: str  # frame | stream_fallback | client_disconnect
    rows: int
    provider_cost: str  # str(Decimal) — Σ provider_cost of unbilled rows with this source


class ReconciliationResponse(BaseModel):
    """Response body for GET /admin/reconciliation (the window's drift summary).

    Maps the v29 reconcile_window aggregate's ReconciliationSummary. All money fields are
    str(Decimal) — exact, never float. drift = provider_cost_total - billed_total over
    cost_basis='provider' rows (healthy < 0); catalog_billed_total is reported separately and
    is never folded into drift.
    """

    model_config = ConfigDict(frozen=True)

    window_from: str  # ISO-8601 UTC, inclusive
    window_to: str  # ISO-8601 UTC, exclusive
    provider_cost_total: str  # str(Decimal) — Σ provider_cost, cost_basis='provider'
    billed_total: str  # str(Decimal) — Σ cost_usd, cost_basis='provider'
    drift: str  # str(Decimal) — provider_cost_total - billed_total
    unbilled_upstream_cost: str  # str(Decimal) — Σ provider_cost where pcost>0 AND cost_usd=0
    unbilled_rows: int  # COUNT(*) of those rows
    catalog_billed_total: str  # str(Decimal) — Σ cost_usd, cost_basis='catalog'
    by_source: list[ReconciliationSourceItem]  # unbilled rows grouped by usage_source, sorted
