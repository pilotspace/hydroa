"""Pydantic schemas for the budgets admin API.

Contract FROZEN @ v1 (budgets TASK.md §3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BudgetPutRequest(BaseModel):
    """Body for PUT /admin/budget."""

    model_config = ConfigDict(frozen=True, strict=False)

    budget_usd_monthly: str | None


class BudgetPutResponse(BaseModel):
    """Response body for PUT /admin/budget — echo of persisted value."""

    model_config = ConfigDict(frozen=True)

    budget_usd_monthly: str | None


class BudgetGetResponse(BaseModel):
    """Response body for GET /admin/budget."""

    model_config = ConfigDict(frozen=True)

    budget_usd_monthly: str | None
    spent_usd_month: str
