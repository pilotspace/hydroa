"""Pydantic schemas for the credits admin/tenant API.

Contract FROZEN @ v1 (credits-ledger TASK.md §3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CreditsTopupRequest(BaseModel):
    """Body for POST /admin/platform/tenants/{tenant_id}/credits/topup."""

    model_config = ConfigDict(frozen=True, strict=False)

    amount_usd: str
    note: str | None = None


class CreditsTopupResponse(BaseModel):
    """Response body for a top-up (201 new, 200 idempotent replay)."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    entry_type: str
    amount_usd: str
    balance_after_usd: str
    idempotency_key: str
    created_at: str


class CreditsBalanceResponse(BaseModel):
    """Response body for GET /admin/credits/balance."""

    model_config = ConfigDict(frozen=True)

    tenant_id: str
    balance_usd: str
    grace_usd: str
    updated_at: str


class CreditsHistoryEntry(BaseModel):
    """One row in the paginated credit ledger history."""

    model_config = ConfigDict(frozen=True)

    id: str
    entry_type: str
    amount_usd: str
    balance_after_usd: str
    reference_type: str | None
    reference_id: str | None
    request_id: str | None
    created_at: str


class CreditsHistoryResponse(BaseModel):
    """Response body for GET /admin/credits/history."""

    model_config = ConfigDict(frozen=True)

    entries: list[CreditsHistoryEntry]
    next_cursor: str | None
