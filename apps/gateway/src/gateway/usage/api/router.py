"""GET /admin/usage — authenticated tenant usage totals + 50 newest records.

Contract (FROZEN @ v1 — TASK.md §3):
  - Authorization: Bearer <JWT>
  - 200: UsageTotalsResponse
  - 401: problem+json ERR_AUTH_INVALID_TOKEN
  - Totals and records come from Postgres ledger (NOT Redis counter).
  - Tenant isolation: only the authenticated tenant's rows are returned.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.errors import ProblemError
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import InvalidTokenError
from gateway.tenants.domain.ports import TokenService
from gateway.usage.api.schemas import UsageRecordItem, UsageTotalsResponse

usage_router = APIRouter(prefix="/admin", tags=["usage"])


def _extract_identity(request: Request) -> Identity:
    """Extract and validate Bearer JWT; raise 401 on any failure."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(401, "ERR_AUTH_INVALID_TOKEN", "Missing or malformed bearer token")
    token_service = cast(TokenService, request.app.state.token_service)
    try:
        return token_service.decode(token)
    except InvalidTokenError:
        raise ProblemError(401, "ERR_AUTH_INVALID_TOKEN", "Invalid or expired token") from None


@usage_router.get("/usage", response_model=UsageTotalsResponse)
async def get_usage(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UsageTotalsResponse:
    """Return usage totals and 50 newest records for the authenticated tenant."""
    identity = _extract_identity(request)
    tenant_id: uuid.UUID = identity.tenant_id

    # Totals from the ledger (NOT the Redis advisory counter)
    totals_row = (
        await session.execute(
            text(
                "SELECT"
                "  COALESCE(SUM(cost_usd), 0) AS total_cost_usd,"
                "  COUNT(*) AS total_requests,"
                "  COALESCE(SUM(prompt_tokens), 0) AS total_prompt_tokens,"
                "  COALESCE(SUM(completion_tokens), 0) AS total_completion_tokens"
                " FROM usage_records"
                " WHERE tenant_id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchone()

    total_cost_usd = Decimal(str(totals_row[0])) if totals_row else Decimal("0")
    total_requests = int(totals_row[1]) if totals_row else 0
    total_prompt_tokens = int(totals_row[2]) if totals_row else 0
    total_completion_tokens = int(totals_row[3]) if totals_row else 0

    # ≤50 newest records ordered by created_at DESC
    rows = (
        await session.execute(
            text(
                "SELECT id, model_id, prompt_tokens, completion_tokens,"
                "  cost_usd, status, created_at"
                " FROM usage_records"
                " WHERE tenant_id = :tid"
                " ORDER BY created_at DESC"
                " LIMIT 50"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchall()

    records = [
        UsageRecordItem(
            id=row[0],
            model_id=str(row[1]),
            prompt_tokens=int(row[2]),
            completion_tokens=int(row[3]),
            cost_usd=str(Decimal(str(row[4]))),
            status=int(row[5]),
            created_at=row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
        )
        for row in rows
    ]

    return UsageTotalsResponse(
        total_cost_usd=str(total_cost_usd),
        total_requests=total_requests,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        records=records,
    )
