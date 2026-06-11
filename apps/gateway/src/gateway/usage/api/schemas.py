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


class SpendWindowResponse(BaseModel):
    """Response body for GET /admin/spend."""

    model_config = ConfigDict(frozen=True)

    window: str  # "day" | "week" | "month"
    bucket_size: str  # granularity of buckets list
    totals: SpendTotals
    buckets: list[SpendBucket]
    # Omitted (None) when group_by is not supplied.
    breakdown: list[SpendBreakdownItem] | list[TeamSpendBreakdownItem] | None = None
