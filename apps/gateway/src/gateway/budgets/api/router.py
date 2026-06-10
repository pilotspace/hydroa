"""Admin API router for budget management.

Contract FROZEN @ v1 (budgets TASK.md §3):
  GET  /admin/budget  — any authenticated role
  PUT  /admin/budget  — owner or admin only; member → 403
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.budgets.api.schemas import BudgetGetResponse, BudgetPutRequest, BudgetPutResponse
from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.domain.entities import Identity

budget_router = APIRouter(prefix="/admin/budget", tags=["budgets"])

_ZERO_STR = "0.00"


@budget_router.get("", response_model=BudgetGetResponse)
async def get_budget(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BudgetGetResponse:
    """GET /admin/budget — return current ceiling and ledger spend for this month.

    Accessible to any authenticated role (owner, admin, member).
    spent_usd_month is sourced from usage_records (ledger), not the Redis counter.
    """
    tenant_id = identity.tenant_id

    # Read budget_usd_monthly from tenants table
    tenant_row = (
        await session.execute(
            text("SELECT budget_usd_monthly FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    budget_str: str | None = None
    if tenant_row is not None and tenant_row[0] is not None:
        budget_str = str(Decimal(str(tenant_row[0])))

    # Sum cost_usd from usage_records for current UTC month
    spent_row = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records"
                " WHERE tenant_id = :tid"
                " AND date_trunc('month', created_at AT TIME ZONE 'UTC')"
                "   = date_trunc('month', now() AT TIME ZONE 'UTC')"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    spent_usd = (
        Decimal(str(spent_row[0])) if spent_row and spent_row[0] is not None else Decimal("0")
    )
    spent_str = f"{spent_usd:.2f}"

    return BudgetGetResponse(
        budget_usd_monthly=budget_str,
        spent_usd_month=spent_str,
    )


@budget_router.put("", response_model=BudgetPutResponse)
async def put_budget(
    body: BudgetPutRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BudgetPutResponse:
    """PUT /admin/budget — set or clear the monthly budget ceiling.

    Requires role owner or admin; member → 403 ERR_AUTH_FORBIDDEN.
    Accepts { budget_usd_monthly: "<decimal string>" | null }.
    Rejects negative values or non-decimal strings with 422 ERR_PAYLOAD_INVALID.
    """
    tenant_id = identity.tenant_id
    new_value = body.budget_usd_monthly

    persisted_str: str | None = None

    if new_value is not None:
        # Validate: must be a valid decimal string and non-negative
        try:
            parsed = Decimal(new_value)
        except InvalidOperation:
            raise ProblemError(
                422,
                "ERR_PAYLOAD_INVALID",
                "budget_usd_monthly must be a valid decimal string or null",
            ) from None

        if parsed < Decimal("0"):
            raise ProblemError(
                422,
                "ERR_PAYLOAD_INVALID",
                "budget_usd_monthly must be non-negative",
            )

        persisted_str = str(parsed)

        await session.execute(
            text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
            {"b": persisted_str, "tid": str(tenant_id)},
        )
    else:
        # null — clear the ceiling
        await session.execute(
            text("UPDATE tenants SET budget_usd_monthly = NULL WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )

    await session.commit()

    return BudgetPutResponse(budget_usd_monthly=persisted_str)
