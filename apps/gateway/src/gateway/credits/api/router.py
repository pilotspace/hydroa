"""FastAPI routers for the credits module.

Contract FROZEN @ v1 (credits-ledger TASK.md §3):
  POST /admin/platform/tenants/{tenant_id}/credits/topup — PLATFORM-OPERATOR only
    (require_superadmin — NOT _require_budgets_manage; a tenant admin must never
    mint its own credits, §0 Issue 5).
  GET  /admin/credits/balance  — any authenticated tenant role, own tenant only.
  GET  /admin/credits/history  — any authenticated tenant role, own tenant only.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    CREDITS_IDEMPOTENCY_KEY_REQUIRED,
    CREDITS_TOPUP_INVALID,
    PAYLOAD_INVALID,
    TENANT_NOT_FOUND,
)
from gateway.credits.api.schemas import (
    CreditsBalanceResponse,
    CreditsHistoryEntry,
    CreditsHistoryResponse,
    CreditsTopupRequest,
    CreditsTopupResponse,
)
from gateway.credits.application.topup_service import topup as topup_service
from gateway.keys.api.deps import get_identity
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.repository import get_tenant_by_id

credits_platform_router = APIRouter(
    prefix="/admin/platform/tenants/{tenant_id}/credits", tags=["credits", "platform-admin"]
)
credits_router = APIRouter(prefix="/admin/credits", tags=["credits"])

_HISTORY_DEFAULT_LIMIT = 50
_HISTORY_MAX_LIMIT = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_topup_amount(raw: str) -> Decimal:
    """R2: amount_usd must be a positive, finite decimal string."""
    try:
        parsed = Decimal(raw)
    except InvalidOperation:
        raise CREDITS_TOPUP_INVALID.exc() from None
    if not parsed.is_finite() or parsed <= Decimal("0"):
        raise CREDITS_TOPUP_INVALID.exc()
    return parsed


def _encode_history_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    payload = json.dumps({"created_at": created_at.isoformat(), "id": str(row_id)}).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_history_cursor(raw: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = raw + "=" * (-len(raw) % 4)
        payload = base64.urlsafe_b64decode(padded.encode("ascii"))
        obj = json.loads(payload)
        created_at = datetime.fromisoformat(obj["created_at"])
        row_id = uuid.UUID(obj["id"])
    except Exception:
        raise PAYLOAD_INVALID.exc() from None
    return created_at, row_id


def _parse_history_limit(raw: str | None) -> int:
    if raw is None:
        return _HISTORY_DEFAULT_LIMIT
    try:
        parsed = int(raw)
    except ValueError:
        raise PAYLOAD_INVALID.exc() from None
    if parsed < 1 or parsed > _HISTORY_MAX_LIMIT:
        raise PAYLOAD_INVALID.exc()
    return parsed


# ---------------------------------------------------------------------------
# POST /admin/platform/tenants/{tenant_id}/credits/topup — platform-operator only
# ---------------------------------------------------------------------------


@credits_platform_router.post("/topup", response_model=CreditsTopupResponse)
async def post_credits_topup(
    tenant_id: uuid.UUID,
    body: CreditsTopupRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreditsTopupResponse:
    """POST .../credits/topup — credit a tenant's prepaid balance. SUPERADMIN only.

    Order (M1 in §0 Issue 5's terms): superadmin role (R5) -> Idempotency-Key present
    (R3) -> amount valid (R2) -> tenant exists (R6) -> idempotent write (M9/R4).
    Response status: 201 for a NEW topup, 200 for an idempotent replay.
    """
    if not idempotency_key:
        raise CREDITS_IDEMPOTENCY_KEY_REQUIRED.exc()

    amount = _parse_topup_amount(body.amount_usd)

    authorize_tenant_scope(identity, tenant_id)
    tenant_row = await get_tenant_by_id(session, tenant_id)
    if tenant_row is None:
        raise TENANT_NOT_FOUND.exc()

    result = await topup_service(
        request.app.state.sessionmaker,
        tenant_id=tenant_id,
        amount_usd=amount,
        idempotency_key=idempotency_key,
        note=body.note,
        actor_user_id=identity.user_id,
    )

    # Audit — fire-and-forget, separate session (mirrors platform_audit_router's
    # emit_platform_audit / budget_router's record_audit; never rolls back the caller).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="credits.topup",
                target_type="credit_ledger",
                target_id=str(result.id),
                result="success",
                metadata={
                    "amount_usd": str(result.amount_usd),
                    "idempotency_key": idempotency_key,
                    "created": result.created,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    response = CreditsTopupResponse(
        id=str(result.id),
        tenant_id=str(tenant_id),
        entry_type="topup",
        amount_usd=str(result.amount_usd),
        balance_after_usd=str(result.balance_after_usd),
        idempotency_key=result.idempotency_key,
        created_at=datetime.now(UTC).isoformat(),
    )
    # FastAPI response_model doesn't control status by itself here — the route always
    # declares 200 by default; the 201-vs-200 distinction is expressed via JSONResponse.
    return JSONResponse(  # type: ignore[return-value]
        status_code=201 if result.created else 200,
        content=response.model_dump(),
    )


# ---------------------------------------------------------------------------
# GET /admin/credits/balance — any authenticated tenant role, own tenant only
# ---------------------------------------------------------------------------


@credits_router.get("/balance", response_model=CreditsBalanceResponse)
async def get_credits_balance(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreditsBalanceResponse:
    """GET /admin/credits/balance — current balance + grace floor for the caller's tenant."""
    tenant_id = identity.tenant_id
    row = (
        await session.execute(
            text(
                "SELECT balance_usd, grace_usd, updated_at FROM tenant_credit_balances"
                " WHERE tenant_id = :t"
            ),
            {"t": str(tenant_id)},
        )
    ).fetchone()

    if row is None:
        # No ledger activity yet for this tenant — the zero-value default a fresh
        # tenant_credit_balances row would carry (lazily created on first hold/topup).
        return CreditsBalanceResponse(
            tenant_id=str(tenant_id),
            balance_usd="0.00000000",
            grace_usd="0.00000000",
            updated_at=datetime.now(UTC).isoformat(),
        )

    return CreditsBalanceResponse(
        tenant_id=str(tenant_id),
        balance_usd=str(Decimal(str(row[0]))),
        grace_usd=str(Decimal(str(row[1]))),
        updated_at=row[2].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /admin/credits/history — any authenticated tenant role, own tenant only
# ---------------------------------------------------------------------------


@credits_router.get("/history", response_model=CreditsHistoryResponse)
async def get_credits_history(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[str | None, Query()] = None,
) -> CreditsHistoryResponse:
    """GET /admin/credits/history — paginated, newest-first, scoped to the caller's own
    tenant (M10) — tenant_id is ALWAYS identity.tenant_id, never a query param."""
    tenant_id = identity.tenant_id
    parsed_limit = _parse_history_limit(limit)

    params: dict[str, object] = {"t": str(tenant_id), "lim": parsed_limit + 1}
    where_cursor = ""
    if cursor is not None:
        cursor_created_at, cursor_id = _decode_history_cursor(cursor)
        where_cursor = " AND (created_at, id) < (:cc, :ci)"
        params["cc"] = cursor_created_at
        params["ci"] = str(cursor_id)

    rows = (
        await session.execute(
            text(
                "SELECT id, entry_type, amount_usd, balance_after_usd, reference_type,"
                " reference_id, request_id, created_at"
                " FROM credit_ledger WHERE tenant_id = :t" + where_cursor
                + " ORDER BY created_at DESC, id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()

    has_more = len(rows) > parsed_limit
    page = rows[:parsed_limit]
    next_cursor = (
        _encode_history_cursor(page[-1][7], uuid.UUID(str(page[-1][0])))
        if has_more and page
        else None
    )

    return CreditsHistoryResponse(
        entries=[
            CreditsHistoryEntry(
                id=str(r[0]),
                entry_type=r[1],
                amount_usd=str(Decimal(str(r[2]))),
                balance_after_usd=str(Decimal(str(r[3]))),
                reference_type=r[4],
                reference_id=str(r[5]) if r[5] is not None else None,
                request_id=str(r[6]) if r[6] is not None else None,
                created_at=r[7].isoformat(),
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )


__all__ = ["credits_platform_router", "credits_router"]
