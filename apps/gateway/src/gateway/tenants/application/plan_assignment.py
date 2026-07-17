"""Shared plan-assignment use-case (self-serve-checkout TASK.md §3, M8/A8; I1 extraction).

Before this task, the plan/seat_cap write was INLINED in the superadmin PUT
(`platform_plans_router.put_platform_tenant_plan`). The milestone invariant "the SAME
domain op, never a parallel write path" requires BOTH the superadmin path and the new
self-serve checkout path to mutate a tenant's plan through ONE shared op — so that write is
extracted here. The extraction is deliberately behavior-preserving: it sets exactly the same
two attributes on the SAME live ORM `TenantRow`, does NOT commit (the caller owns the
transaction — the superadmin router commits immediately, the checkout confirm commits inside
its row-locked transaction), and hands back the old/new values the caller needs for its
audit metadata. `test_superadmin_plan_endpoint_unchanged` proves the superadmin endpoint's
behavior + shapes stay byte-identical.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from gateway.tenants.infrastructure.orm import TenantRow


@dataclass(frozen=True, slots=True)
class PlanAssignment:
    """The before/after of a plan assignment — supplies audit metadata to the caller."""

    old_plan_id: uuid.UUID | None
    old_seat_cap: int | None
    new_plan_id: uuid.UUID | None
    new_seat_cap: int | None


def assign_plan(
    tenant_row: TenantRow, *, plan_id: uuid.UUID | None, seat_cap: int | None
) -> PlanAssignment:
    """Set ``tenant_row.plan_id`` + ``tenant_row.seat_cap``; return the before/after.

    Pure in-session mutation — the CALLER commits (byte-identical to the previously-inlined
    superadmin write). No validation here: gate order (existence / platform-kind / plan
    resolution / self-serve / audience / downgrade) is the caller's responsibility and stays
    where each caller already enforces it.
    """
    old_plan_id = tenant_row.plan_id
    old_seat_cap = tenant_row.seat_cap
    tenant_row.plan_id = plan_id
    tenant_row.seat_cap = seat_cap
    return PlanAssignment(
        old_plan_id=old_plan_id,
        old_seat_cap=old_seat_cap,
        new_plan_id=plan_id,
        new_seat_cap=seat_cap,
    )


__all__ = ["PlanAssignment", "assign_plan"]
