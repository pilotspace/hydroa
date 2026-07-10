"""Pydantic response models for GET /admin/guardrails/analytics.

Contract (FROZEN @ v1 — guardrail-analytics TASK.md §3):
  200 -> {
    window: "day" | "week" | "month",
    bucket_size: "day" | "week" | "month",
    totals: { bucket_start, bucket_end, evaluations, hits, blocked, masked, audited,
              passed, error, unchecked, budget_exceeded },
    buckets: [ { bucket_start, evaluations, hits, blocked, masked, audited, passed,
                 error, unchecked, budget_exceeded }, ... ],
    breakdown: [
      -- group_by="guardrail":     { guardrail, <same count fields> }
      -- group_by="policy_source": { policy_source, <same count fields> }
      -- group_by="key_id":        { key_id, <same count fields> }
    ] | null   -- null when group_by is omitted
  }
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict


class GuardrailAnalyticsTotals(BaseModel):
    """Totals object for the guardrail analytics window (sum of all buckets)."""

    model_config = ConfigDict(frozen=True)

    bucket_start: str  # ISO-8601 UTC — first bucket start
    bucket_end: str  # ISO-8601 UTC — last bucket end (now() or end param)
    evaluations: int  # total recorded verdicts in window
    hits: int  # evaluations - passed (any non-"passed" action)
    blocked: int
    masked: int
    audited: int
    passed: int
    error: int
    unchecked: int
    budget_exceeded: int


class GuardrailAnalyticsBucket(BaseModel):
    """One date_trunc bucket in the guardrail analytics window."""

    model_config = ConfigDict(frozen=True)

    bucket_start: str  # ISO-8601 UTC
    evaluations: int
    hits: int
    blocked: int
    masked: int
    audited: int
    passed: int
    error: int
    unchecked: int
    budget_exceeded: int


class GuardrailBreakdownItem(BaseModel):
    """Breakdown item for group_by="guardrail" (the "pattern" dimension)."""

    model_config = ConfigDict(frozen=True)

    guardrail: str  # "prompt_injection" | "pii_mask" | "ml_moderation" | "error"
    evaluations: int
    hits: int
    blocked: int
    masked: int
    audited: int
    passed: int
    error: int
    unchecked: int
    budget_exceeded: int


class PolicySourceBreakdownItem(BaseModel):
    """Breakdown item for group_by="policy_source" (the "policy" dimension)."""

    model_config = ConfigDict(frozen=True)

    policy_source: str  # "key" | "tenant" | "none"
    evaluations: int
    hits: int
    blocked: int
    masked: int
    audited: int
    passed: int
    error: int
    unchecked: int
    budget_exceeded: int


class KeyBreakdownItem(BaseModel):
    """Breakdown item for group_by="key_id" (the "key" dimension)."""

    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    evaluations: int
    hits: int
    blocked: int
    masked: int
    audited: int
    passed: int
    error: int
    unchecked: int
    budget_exceeded: int


class GuardrailAnalyticsResponse(BaseModel):
    """Response body for GET /admin/guardrails/analytics."""

    model_config = ConfigDict(frozen=True)

    window: str  # "day" | "week" | "month"
    bucket_size: str  # granularity of buckets list
    totals: GuardrailAnalyticsTotals
    buckets: list[GuardrailAnalyticsBucket]
    breakdown: (
        list[GuardrailBreakdownItem]
        | list[PolicySourceBreakdownItem]
        | list[KeyBreakdownItem]
        | None
    )
