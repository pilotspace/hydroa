"""Admin API router for per-(tenant, model) rate-card entries.

Contract FROZEN @ v1 (tiered-rate-cards TASK.md §3):
  PUT    /admin/rate-cards/{model_id}   body: {markup_pct: number}   idempotent upsert
    200 -> {model_id: string, markup_pct: string}
    403 -> problem+json ERR_AUTH_FORBIDDEN (caller lacks RATE_CARDS_MANAGE)
    422 -> problem+json (negative | non-numeric | exceeds Numeric(7,4))
  GET    /admin/rate-cards              -> {entries: [{model_id, markup_pct}]}  ([] when none)
  DELETE /admin/rate-cards/{model_id}   -> 204 always (idempotent — already-absent is success,
                                            NEVER 404)

Every route requires Permission.RATE_CARDS_MANAGE (OWNER-only — see
tenants/domain/authz.py) and acts on the CALLER'S OWN tenant
(identity.tenant_id); there is no cross-tenant surface (TASK.md §1 assumption
#2, frozen as-drafted).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.ids import uuid7
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.rate_card_orm import TenantRateCardEntry

rate_card_router = APIRouter(prefix="/admin/rate-cards", tags=["rate-cards"])


class RateCardPutRequest(BaseModel):
    """PUT body — markup_pct mirrors tenants.markup_pct's Numeric(7,4) semantics."""

    markup_pct: Decimal = Field(ge=0, max_digits=7, decimal_places=4)


class RateCardEntryResponse(BaseModel):
    """PUT response / GET list-item — markup_pct serialized as string (like other money fields)."""

    model_id: str
    markup_pct: str


class RateCardListResponse(BaseModel):
    entries: list[RateCardEntryResponse]


@rate_card_router.put("/{model_id}", response_model=RateCardEntryResponse)
async def put_rate_card_entry(
    model_id: str,
    body: RateCardPutRequest,
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RateCardEntryResponse:
    """Idempotent upsert of a per-(tenant, model) markup override.

    A duplicate (tenant, model) PUT UPDATEs the existing entry — never an
    error (TASK.md §2 "Duplicate (tenant, model) create is an idempotent
    upsert").
    """
    stmt = (
        pg_insert(TenantRateCardEntry)
        .values(
            id=uuid7(),
            tenant_id=identity.tenant_id,
            model_id=model_id,
            markup_pct=body.markup_pct,
        )
        .on_conflict_do_update(
            index_elements=[TenantRateCardEntry.tenant_id, TenantRateCardEntry.model_id],
            set_={"markup_pct": body.markup_pct, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
    await session.commit()
    return RateCardEntryResponse(model_id=model_id, markup_pct=str(body.markup_pct))


@rate_card_router.get("", response_model=RateCardListResponse)
async def list_rate_card_entries(
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RateCardListResponse:
    """Return every rate-card entry for the caller's own tenant ([] when none)."""
    rows = (
        await session.execute(
            text(
                "SELECT model_id, markup_pct FROM tenant_rate_card_entries"
                " WHERE tenant_id = :tid ORDER BY model_id"
            ),
            {"tid": str(identity.tenant_id)},
        )
    ).all()
    return RateCardListResponse(
        entries=[RateCardEntryResponse(model_id=row[0], markup_pct=str(row[1])) for row in rows]
    )


@rate_card_router.delete("/{model_id}", status_code=204)
async def delete_rate_card_entry(
    model_id: str,
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Delete a rate-card entry — idempotent: 204 even when no entry exists
    (TASK.md §2 "Deleting a model with no entry is idempotent" — never 404)."""
    await session.execute(
        text("DELETE FROM tenant_rate_card_entries WHERE tenant_id = :tid AND model_id = :mid"),
        {"tid": str(identity.tenant_id), "mid": model_id},
    )
    await session.commit()
    return Response(status_code=204)
