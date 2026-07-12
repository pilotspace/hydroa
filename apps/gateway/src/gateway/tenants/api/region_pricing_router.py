"""Admin API router for per-(tenant, region) pricing-multiplier overrides.

Contract FROZEN @ v1 (region-pricing TASK.md §3):
  PUT    /admin/region-pricing/{region}   body: {multiplier: number}   idempotent upsert
    200 -> {region: string, multiplier: string}
    403 -> problem+json ERR_AUTH_FORBIDDEN (caller lacks RATE_CARDS_MANAGE)
    422 -> problem+json (negative | non-numeric | exceeds Numeric(6,4) | unknown region)
  GET    /admin/region-pricing            -> {entries: [{region, multiplier}]}  ([] when none)
  DELETE /admin/region-pricing/{region}   -> 204 always (idempotent — already-absent
                                            is success, NEVER 404)

Every route requires Permission.RATE_CARDS_MANAGE (OWNER-only, reused — region
multipliers ARE rate-card entries per TASK.md DECIDED #1) and acts on the
CALLER'S OWN tenant (identity.tenant_id); no cross-tenant surface (mirrors
rate_card_router.py exactly, TASK.md §1 assumption #2, frozen as-drafted).

DECIDED at freeze (deliberate deviation from rate_card_router's no-FK-style
precedent): PUT validates `region` against the frozen four-value Literal
(us|eu|ap|global) — FastAPI rejects any other path value with a 422
(via the app's shared RequestValidationError -> problem+json handler,
core/errors.py) before the handler body even runs. A silent no-op on a money
knob is a foot-gun (TASK.md DECIDED #3). GET/DELETE accept any string —
mirrors rate_card_router's model_id precedent: listing/deleting an
unrecognized region is always safe (there is never a row to touch for a
region nothing was ever PUT against), never a rejection.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import Region
from gateway.core.db import get_session
from gateway.core.ids import uuid7
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.region_pricing_orm import TenantRegionMultiplierOverride

region_pricing_router = APIRouter(prefix="/admin/region-pricing", tags=["region-pricing"])


class RegionPricingPutRequest(BaseModel):
    """PUT body — raw multiplier (Numeric(6,4)), CHECK > 0 (TASK.md §3 DDL)."""

    multiplier: Decimal = Field(gt=0, max_digits=6, decimal_places=4)


class RegionPricingEntryResponse(BaseModel):
    """PUT response / GET list-item — multiplier serialized as string (like markup_pct)."""

    region: str
    multiplier: str


class RegionPricingListResponse(BaseModel):
    entries: list[RegionPricingEntryResponse]


@region_pricing_router.put("/{region}", response_model=RegionPricingEntryResponse)
async def put_region_pricing_entry(
    region: Region,
    body: RegionPricingPutRequest,
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegionPricingEntryResponse:
    """Idempotent upsert of a per-(tenant, region) multiplier override.

    A duplicate (tenant, region) PUT UPDATEs the existing entry — never an
    error (TASK.md §2 "Duplicate region override PUT is an idempotent upsert").
    `region` is already validated against the frozen us|eu|ap|global Literal
    by FastAPI's path-param typing before this body runs.
    """
    stmt = (
        pg_insert(TenantRegionMultiplierOverride)
        .values(
            id=uuid7(),
            tenant_id=identity.tenant_id,
            region=region,
            multiplier=body.multiplier,
        )
        .on_conflict_do_update(
            index_elements=[
                TenantRegionMultiplierOverride.tenant_id,
                TenantRegionMultiplierOverride.region,
            ],
            set_={"multiplier": body.multiplier, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
    await session.commit()
    return RegionPricingEntryResponse(region=region, multiplier=str(body.multiplier))


@region_pricing_router.get("", response_model=RegionPricingListResponse)
async def list_region_pricing_entries(
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RegionPricingListResponse:
    """Return every region-pricing override for the caller's own tenant ([] when none)."""
    rows = (
        await session.execute(
            text(
                "SELECT region, multiplier FROM tenant_region_multiplier_overrides"
                " WHERE tenant_id = :tid ORDER BY region"
            ),
            {"tid": str(identity.tenant_id)},
        )
    ).all()
    return RegionPricingListResponse(
        entries=[RegionPricingEntryResponse(region=row[0], multiplier=str(row[1])) for row in rows]
    )


@region_pricing_router.delete("/{region}", status_code=204)
async def delete_region_pricing_entry(
    region: str,
    identity: Annotated[Identity, require_permission(Permission.RATE_CARDS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Delete a region-pricing override — idempotent: 204 even when no entry
    exists (TASK.md §2 "Deleting an absent region override is a no-op success"
    — never 404)."""
    await session.execute(
        text(
            "DELETE FROM tenant_region_multiplier_overrides"
            " WHERE tenant_id = :tid AND region = :region"
        ),
        {"tid": str(identity.tenant_id), "region": region},
    )
    await session.commit()
    return Response(status_code=204)
