"""FastAPI router for the plan/tier catalog + superadmin cross-tenant plan assignment
surface (plan-catalog TASK.md §3 CONTRACT — FROZEN @ v1).

Endpoints:
  GET /admin/platform/plans                       — full catalog list (M4)
  GET /admin/platform/tenants/{tenant_id}/plan     — view a target tenant's plan (M5)
  PUT /admin/platform/tenants/{tenant_id}/plan     — assign/change/unassign (M6-M9)

This task is the CATALOG + ADMIN SURFACE ONLY (§3 Non-goals): no $ budget/rate/seat-cap
ENFORCEMENT lives here, no superadmin tier-DEFINITION CRUD, no self-service plan change —
those are `plan-budget-enforcement`/`plan-rate-enforcement`/`plan-seat-cap`'s own jobs.

Security (server-side, authoritative; mirrors platform_tenant_config_router.py's dual-gate
precedent exactly):
  - Every route is gated by `require_superadmin` (primary FastAPI dependency) FIRST.
  - The 2 tenant-scoped routes additionally call `authorize_tenant_scope(identity,
    tenant_id)` THEN `get_tenant_by_id` (404 TENANT_NOT_FOUND) — BEFORE any plan-specific
    check (`_require_target_tenant` below, mirrors platform_users_router.py's own helper
    of the same name, extended to hand back the row since these routes need its
    kind/plan_id/seat_cap fields, not just an existence boolean).
  - PUT additionally runs the platform-kind check (M3/R4) THEN the plan_id-resolves
    check (R5) THEN the seat_cap cross-field check (R6/R7), all BEFORE any write —
    mirrors platform_tenant_config_router.py's own "PUT checks existence before
    writing, never a silent no-op" precedent.

Audit: all 3 routes call emit_platform_audit() on their success path (M4/M5/M6) —
  "platform.plan.list" (target_tenant_id=None, the targetless/system-level case,
  mirrors platform_tenants_router.py's own bulk-list precedent) ·
  "platform.plan.view" (target_tenant_id=path tenant_id) ·
  "platform.plan.assign" (target_tenant_id=path tenant_id, metadata carries the OLD and
  NEW plan_id/seat_cap). target_type/target_id for the 2 tenant-scoped routes follow
  platform_tenant_config_router.py's own convention (a static sub-resource label, since
  target_tenant_id already scopes the tenant — NOT platform_tenants_router.py's
  get-one convention of target_id=str(tenant_id), which names the TENANT record itself
  as the audited resource, a different situation).

Write path: PUT mutates the fetched TenantRow's attributes directly and commits (plain
ORM unit-of-work) rather than a raw-SQL UPDATE + re-read — simpler and equally correct
here because the write is an unconditional 2-column set, not the partial/conditional SQL
construction platform_tenant_config_router.py's cache/guardrails routes needed. The
session's own `expire_on_commit=False` (see main.py's sessionmaker construction) means
the mutated attributes remain valid to read for the response without a re-fetch.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    PAYLOAD_INVALID,
    PLAN_NOT_FOUND,
    PLAN_TENANT_INELIGIBLE,
    TENANT_NOT_FOUND,
)
from gateway.tenants.application.plan_assignment import assign_plan
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity, Plan
from gateway.tenants.infrastructure.orm import PlanRow, TenantRow
from gateway.tenants.infrastructure.repository import get_tenant_by_id

platform_plans_router = APIRouter(prefix="/admin/platform", tags=["platform-admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PlanResponse(BaseModel):
    id: uuid.UUID
    name: str
    display_name: str
    seat_cap: int | None
    budget_usd_monthly_default: str | None
    rpm_limit_default: int | None
    tpm_limit_default: int | None


class PlansListResponse(BaseModel):
    plans: list[PlanResponse]


class TenantPlanResponse(BaseModel):
    tenant_id: uuid.UUID
    plan: PlanResponse | None
    seat_cap: int | None


class TenantPlanPutRequest(BaseModel):
    plan_id: uuid.UUID | None
    # OPTIONAL key — 3-state semantic: omitted = inherit the plan's own seat_cap at write
    # time (M8) · null = explicitly unlimited for this tenant (M9) · positive int =
    # explicit negotiated cap (M9). MUST be omitted or null when plan_id is null (R7).
    seat_cap: int | None = None


# ---------------------------------------------------------------------------
# Conversion helpers (ORM row -> domain -> API DTO; Plan is the contract-mandated
# domain shape — TASK.md §3 notes container placement is Build's discretion)
# ---------------------------------------------------------------------------


def _row_to_plan(row: PlanRow) -> Plan:
    return Plan(
        id=row.id,
        name=row.name,
        display_name=row.display_name,
        seat_cap=row.seat_cap,
        budget_usd_monthly_default=row.budget_usd_monthly_default,
        rpm_limit_default=row.rpm_limit_default,
        tpm_limit_default=row.tpm_limit_default,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _plan_to_response(plan: Plan) -> PlanResponse:
    # Decimal-as-string — mirrors BudgetGetResponse's own str(Decimal(str(...))) convention.
    budget_str = (
        str(Decimal(str(plan.budget_usd_monthly_default)))
        if plan.budget_usd_monthly_default is not None
        else None
    )
    return PlanResponse(
        id=plan.id,
        name=plan.name,
        display_name=plan.display_name,
        seat_cap=plan.seat_cap,
        budget_usd_monthly_default=budget_str,
        rpm_limit_default=plan.rpm_limit_default,
        tpm_limit_default=plan.tpm_limit_default,
    )


async def _require_target_tenant(
    identity: Identity, tenant_id: uuid.UUID, session: AsyncSession
) -> TenantRow:
    """M5/M6: authorize_tenant_scope THEN tenant-existence check — BEFORE any
    plan-specific check."""
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    return row


# ---------------------------------------------------------------------------
# GET /admin/platform/plans
# ---------------------------------------------------------------------------


@platform_plans_router.get("/plans", response_model=PlansListResponse)
async def list_platform_plans(
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> PlansListResponse:
    """GET /admin/platform/plans — the full plan catalog. SUPERADMIN only, no tenant_id
    in the path (M4)."""
    rows = (await session.execute(select(PlanRow).order_by(PlanRow.created_at))).scalars().all()
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=None,
        action="platform.plan.list",
        target_type="plan",
        target_id=None,
        metadata={},
    )
    return PlansListResponse(plans=[_plan_to_response(_row_to_plan(r)) for r in rows])


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants/{tenant_id}/plan
# ---------------------------------------------------------------------------


@platform_plans_router.get("/tenants/{tenant_id}/plan", response_model=TenantPlanResponse)
async def get_platform_tenant_plan(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> TenantPlanResponse:
    """GET .../plan — the target tenant's current plan (or null) + resolved seat_cap.
    SUPERADMIN only (M5). No platform-kind guard here — a read that truthfully reports
    "no plan" needs no guard (only the PUT below special-cases the platform tenant)."""
    tenant_row = await _require_target_tenant(identity, tenant_id, session)

    plan: Plan | None = None
    if tenant_row.plan_id is not None:
        plan_row = (
            await session.execute(select(PlanRow).where(PlanRow.id == tenant_row.plan_id))
        ).scalar_one_or_none()
        if plan_row is not None:
            plan = _row_to_plan(plan_row)

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.plan.view",
        target_type="plan",
        target_id="assignment",
        metadata={},
    )
    return TenantPlanResponse(
        tenant_id=tenant_id,
        plan=_plan_to_response(plan) if plan is not None else None,
        seat_cap=tenant_row.seat_cap,
    )


# ---------------------------------------------------------------------------
# PUT /admin/platform/tenants/{tenant_id}/plan
# ---------------------------------------------------------------------------


@platform_plans_router.put("/tenants/{tenant_id}/plan", response_model=TenantPlanResponse)
async def put_platform_tenant_plan(
    tenant_id: uuid.UUID,
    body: TenantPlanPutRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> TenantPlanResponse:
    """PUT .../plan — assign, change, or clear (plan_id: null) the target tenant's plan
    in one request. SUPERADMIN only (M6-M9). Gate order: require_superadmin ->
    authorize_tenant_scope -> get_tenant_by_id (404, R3) -> platform-kind check
    (403, M3/R4) -> plan_id-resolves check (404, R5) -> seat_cap cross-field check
    (422, R6/R7) -> write -> emit_platform_audit.
    """
    tenant_row = await _require_target_tenant(identity, tenant_id, session)

    if tenant_row.kind == "platform":
        raise PLAN_TENANT_INELIGIBLE.exc()

    fields_set = body.model_fields_set
    seat_cap_given = "seat_cap" in fields_set

    plan_row: PlanRow | None
    resolved_seat_cap: int | None

    if body.plan_id is None:
        # M7: unassign clears BOTH fields atomically — a non-null seat_cap alongside
        # plan_id: null is REJECTED (R7), never silently dropped or applied.
        if seat_cap_given and body.seat_cap is not None:
            raise PAYLOAD_INVALID.exc(
                detail="seat_cap must be omitted or null when plan_id is null"
            )
        plan_row = None
        resolved_seat_cap = None
    else:
        plan_row = (
            await session.execute(select(PlanRow).where(PlanRow.id == body.plan_id))
        ).scalar_one_or_none()
        if plan_row is None:
            raise PLAN_NOT_FOUND.exc()

        # R6: an explicitly-supplied seat_cap must be a positive integer (or null).
        if seat_cap_given and body.seat_cap is not None and body.seat_cap <= 0:
            raise PAYLOAD_INVALID.exc(detail="seat_cap must be a positive integer")

        if seat_cap_given:
            # M9: explicit override (including null = explicitly unlimited) — for THAT
            # tenant only, regardless of the plan's own default.
            resolved_seat_cap = body.seat_cap
        else:
            # M8: omitted -> one-time copy DOWN from the freshly-read plan row's own
            # seat_cap at this moment — never left at a stale prior plan's value.
            resolved_seat_cap = plan_row.seat_cap

    # I1 extraction (self-serve-checkout §3, M8): the plan/seat_cap write now goes through
    # the SHARED assign_plan use-case that self-serve checkout also calls — never a second,
    # parallel write path. Behavior is byte-identical (same two attributes set on the same
    # live TenantRow, same immediate commit, same audit values).
    assignment = assign_plan(tenant_row, plan_id=body.plan_id, seat_cap=resolved_seat_cap)
    await session.commit()

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.plan.assign",
        target_type="plan",
        target_id="assignment",
        metadata={
            "old_plan_id": (
                str(assignment.old_plan_id) if assignment.old_plan_id is not None else None
            ),
            "new_plan_id": (
                str(assignment.new_plan_id) if assignment.new_plan_id is not None else None
            ),
            "old_seat_cap": assignment.old_seat_cap,
            "new_seat_cap": assignment.new_seat_cap,
        },
    )
    return TenantPlanResponse(
        tenant_id=tenant_id,
        plan=_plan_to_response(_row_to_plan(plan_row)) if plan_row is not None else None,
        seat_cap=resolved_seat_cap,
    )
