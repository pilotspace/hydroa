"""Pydantic response models for GET /admin/usage.

Contract (FROZEN @ v1):
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
