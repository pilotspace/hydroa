"""Admin API router for tenant-self resolved plan entitlements.

Contract FROZEN @ v1 (billing-ui TASK.md §3, M8):
  GET /admin/plan — any authenticated role (mirrors gateway/budgets/api/router.py:get_budget's
  RBAC exactly: only `Depends(get_identity)`, no require_permission/role-check dependency).

A THIN read composed from two already-frozen pieces, zero new query shape invented:
  1. `PlanEntitlementResolver.resolve(tenant_id)` (plan-enforcement TASK.md §3, FROZEN @ v1) —
     the resolved budget/allowlist/features. Consumed via its concrete adapter
     `SqlAlchemyPlanEntitlementResolver`, never re-implemented here.
  2. A `plans` row lookup (plan-catalog's own schema, unchanged) for display metadata
     (display_name/seat_cap/rpm_limit_default/tpm_limit_default) when a plan is assigned.

This router owns the endpoint; `plan-enforcement`'s frozen files (ports.py, entitlements.py,
plan_entitlement_resolver.py) are imported, never edited.

self-serve-plans-catalog TASK.md §3 (FROZEN @ v1) adds a SIBLING `GET /admin/plans` (plural) —
the tenant's self-serve upgrade catalog, same `Depends(get_identity)` RBAC idiom. A thin read
over `list_self_serve_plans` (gateway.tenants.application.self_serve_plans); the tenant's
account_type/plan_id are resolved from `identity.tenant_id` server-side, never a query/body
field (self-serve-checkout's own I1/I3 discipline, reused verbatim).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.api.deps import get_identity
from gateway.tenants.application.self_serve_plans import list_self_serve_plans
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.ports import PlanEntitlementResolver
from gateway.tenants.infrastructure.orm import TenantRow
from gateway.tenants.infrastructure.plan_entitlement_resolver import (
    SqlAlchemyPlanEntitlementResolver,
)

plan_router = APIRouter(prefix="/admin/plan", tags=["plans"])


class PlanSummary(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    seat_cap: int | None
    rpm_limit_default: int | None
    tpm_limit_default: int | None


class ResolvedEntitlementsResponse(BaseModel):
    effective_budget_usd_monthly: str | None
    plan_model_allowlist: list[str] | None
    plan_feature_flags: list[str]


class PlanGetResponse(BaseModel):
    plan: PlanSummary | None
    resolved: ResolvedEntitlementsResponse


class SelfServePlanItem(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    base_price_usd_monthly: str | None


class SelfServePlansResponse(BaseModel):
    plans: list[SelfServePlanItem]


def _money2(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def get_plan_entitlement_resolver(request: Request) -> PlanEntitlementResolver:
    """DI seam over the frozen plan-enforcement port — a fresh adapter bound to the app's
    own sessionmaker (mirrors `record_audit(request.app.state.sessionmaker, ...)` in
    `budgets/api/router.py:put_budget`, the codebase's own precedent for reaching
    `app.state.sessionmaker` from inside a route handler)."""
    return SqlAlchemyPlanEntitlementResolver(request.app.state.sessionmaker)


@plan_router.get("", response_model=PlanGetResponse)
async def get_plan(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
    resolver: Annotated[PlanEntitlementResolver, Depends(get_plan_entitlement_resolver)],
) -> PlanGetResponse:
    """GET /admin/plan — tenant-self resolved entitlements + plan display metadata.

    Accessible to any authenticated role (owner, admin, operator, billing_admin, viewer,
    member, superadmin) — no require_permission call, only get_identity, mirroring
    GET /admin/budget's own RBAC exactly.

    An unplanned tenant (resolved.plan_id is None) returns `plan: null` with a 200 — never
    an error (R6): the tenant-level resolved budget still renders, mirroring
    plan-enforcement's own M7 grandfathered-unlimited semantics.
    """
    resolved = await resolver.resolve(identity.tenant_id)

    plan: PlanSummary | None = None
    if resolved.plan_id is not None:
        row = (
            await session.execute(
                text(
                    "SELECT id, name, display_name, seat_cap, rpm_limit_default,"
                    " tpm_limit_default FROM plans WHERE id = :pid"
                ),
                {"pid": str(resolved.plan_id)},
            )
        ).fetchone()
        if row is not None:
            plan = PlanSummary(
                id=row[0],
                name=row[1],
                display_name=row[2],
                seat_cap=row[3],
                rpm_limit_default=row[4],
                tpm_limit_default=row[5],
            )

    budget_str = (
        str(resolved.effective_budget_usd_monthly)
        if resolved.effective_budget_usd_monthly is not None
        else None
    )

    return PlanGetResponse(
        plan=plan,
        resolved=ResolvedEntitlementsResponse(
            effective_budget_usd_monthly=budget_str,
            plan_model_allowlist=resolved.plan_model_allowlist,
            plan_feature_flags=sorted(resolved.plan_feature_flags),
        ),
    )


@plan_router.get("s", response_model=SelfServePlansResponse)
async def get_self_serve_plans(
    request: Request,
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SelfServePlansResponse:
    """GET /admin/plans — the tenant's self-serve upgrade catalog.

    Accessible to any authenticated role (mirrors `get_plan` above): only `Depends(get_identity)`,
    no `require_permission` — the catalog is non-sensitive pricing already public on /pricing.

    The tenant's account_type/plan_id are resolved from `identity.tenant_id` by loading its
    OWN `TenantRow` here — never from a query/body field (I1/I3 discipline, mirrors
    self-serve-checkout's `_require_tenant`).
    """
    tenant = (
        await session.execute(select(TenantRow).where(TenantRow.id == identity.tenant_id))
    ).scalar_one_or_none()
    account_type = tenant.account_type if tenant is not None else None
    current_plan_id = tenant.plan_id if tenant is not None else None

    plans = await list_self_serve_plans(
        request.app.state.sessionmaker,
        account_type=account_type,
        current_plan_id=current_plan_id,
    )

    return SelfServePlansResponse(
        plans=[
            SelfServePlanItem(
                id=p.id,
                name=p.name,
                display_name=p.display_name,
                base_price_usd_monthly=_money2(p.base_price),
            )
            for p in plans
        ]
    )


__all__ = ["plan_router"]
