"""Admin API router for per-tenant service-tier configuration.

Contract FROZEN @ v1 (service-tiers TASK.md §3), mirrors region_pricing_router.py:

  PUT    /admin/service-tiers/default-tier    body: {default_tier: "priority"|"standard"}
    200 -> {default_tier: string}
    403 -> problem+json "ERR_AUTH_FORBIDDEN"
    422 -> problem+json   (default_tier not in {priority, standard})

  PUT    /admin/service-tiers/priority-markup   body: {markup_pct: number}
    200 -> {markup_pct: string}
    403 -> problem+json "ERR_AUTH_FORBIDDEN"
    422 -> problem+json   (negative | non-numeric | exceeds Numeric(7,4))

  GET    /admin/service-tiers   -> {default_tier: string, priority_markup_pct: string}
    (effective: override-or-seed — reads the SAME resolution rule resolve_tier_multiplier
    uses at billing time, so this display never drifts from what a request actually bills)

Every route requires Permission.KEYS_MANAGE (owner-or-admin, reused) and acts on the
CALLER'S OWN tenant only (identity.tenant_id) — no cross-tenant surface, mirrors
region_pricing_router.py exactly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.ids import uuid7
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.tier_markup_orm import TenantPriorityMarkupOverride

service_tier_router = APIRouter(prefix="/admin/service-tiers", tags=["service-tiers"])

# service-tiers TASK.md §3 M11: the DECIDED seed (+25%), the SAME literal
# rate_card_resolver.resolve_tier_multiplier falls back to — the GET route below reads
# this constant so its "effective" display never drifts from what a request actually
# bills (single source of truth, no second hardcoded seed).
_PRIORITY_MARKUP_SEED_PCT = Decimal("25")


class DefaultTierPutRequest(BaseModel):
    default_tier: Literal["priority", "standard"]


class DefaultTierResponse(BaseModel):
    default_tier: str


class PriorityMarkupPutRequest(BaseModel):
    """PUT body — markup_pct (Numeric(7,4)), CHECK >= 0 (TASK.md §3 DDL)."""

    markup_pct: Decimal = Field(ge=0, max_digits=7, decimal_places=4)


class PriorityMarkupResponse(BaseModel):
    markup_pct: str


class ServiceTiersEffectiveResponse(BaseModel):
    default_tier: str
    priority_markup_pct: str


@service_tier_router.put("/default-tier", response_model=DefaultTierResponse)
async def put_default_tier(
    request: Request,
    body: DefaultTierPutRequest,
    identity: Annotated[Identity, require_permission(Permission.KEYS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DefaultTierResponse:
    """Set the caller's own tenant's default_tier (inherited by any key without a
    key-level tier override — three-state PATCH convention on /admin/keys/*)."""
    await session.execute(
        text("UPDATE tenants SET default_tier = :tier WHERE id = :tid"),
        {"tier": body.default_tier, "tid": str(identity.tenant_id)},
    )
    await session.commit()

    # Audit emit — fail-open fire-and-forget (mirrors teams/api/router.py add_member's own
    # idiom exactly).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="service_tier.default_tier_update",
                target_type="service_tier",
                target_id="default_tier",
                result="success",
                metadata={"default_tier": body.default_tier},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return DefaultTierResponse(default_tier=body.default_tier)


@service_tier_router.put("/priority-markup", response_model=PriorityMarkupResponse)
async def put_priority_markup(
    request: Request,
    body: PriorityMarkupPutRequest,
    identity: Annotated[Identity, require_permission(Permission.KEYS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PriorityMarkupResponse:
    """Idempotent upsert of the caller's own tenant's priority-tier markup override.

    A duplicate PUT UPDATEs the existing row — never an error (TASK.md §2 "Duplicate
    priority-markup PUT is an idempotent upsert").
    """
    stmt = (
        pg_insert(TenantPriorityMarkupOverride)
        .values(
            id=uuid7(),
            tenant_id=identity.tenant_id,
            markup_pct=body.markup_pct,
        )
        .on_conflict_do_update(
            index_elements=[TenantPriorityMarkupOverride.tenant_id],
            set_={"markup_pct": body.markup_pct, "updated_at": func.now()},
        )
    )
    await session.execute(stmt)
    await session.commit()

    # Audit emit — fail-open fire-and-forget (mirrors teams/api/router.py add_member's own
    # idiom exactly).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="service_tier.priority_markup_update",
                target_type="service_tier",
                target_id="priority_markup",
                result="success",
                metadata={"markup_pct": str(body.markup_pct)},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return PriorityMarkupResponse(markup_pct=str(body.markup_pct))


@service_tier_router.get("", response_model=ServiceTiersEffectiveResponse)
async def get_service_tiers(
    identity: Annotated[Identity, require_permission(Permission.KEYS_MANAGE)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ServiceTiersEffectiveResponse:
    """Return the caller's own tenant's EFFECTIVE service-tier configuration
    (override-or-seed — the same resolution rule resolve_tier_multiplier uses)."""
    tenant_row = (
        await session.execute(
            text("SELECT default_tier FROM tenants WHERE id = :tid"),
            {"tid": str(identity.tenant_id)},
        )
    ).fetchone()
    default_tier = str(tenant_row[0]) if tenant_row is not None else "standard"

    markup_row = (
        await session.execute(
            text("SELECT markup_pct FROM tenant_priority_markup_overrides WHERE tenant_id = :tid"),
            {"tid": str(identity.tenant_id)},
        )
    ).fetchone()
    priority_markup_pct = (
        Decimal(str(markup_row[0])) if markup_row is not None else _PRIORITY_MARKUP_SEED_PCT
    )
    return ServiceTiersEffectiveResponse(
        default_tier=default_tier,
        priority_markup_pct=str(priority_markup_pct),
    )


__all__ = ["service_tier_router"]
