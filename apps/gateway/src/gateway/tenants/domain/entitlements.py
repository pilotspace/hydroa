"""Entitlement resolution — pure, zero-I/O core (plan-enforcement TASK.md §3, FROZEN @ v1).

Turns a tenant's raw budget/allowlist/feature-flag columns plus its (optional) assigned
plan's own defaults into the VALUES actually enforced. This module NEVER decides whether
to enforce anything — every caller (RedisBudgetGuard, the governance model-allowlist step,
check_plan_feature) makes its own enforcement decision from the resolved values.

Zero framework imports (domain layer — CONVENTIONS.md layering discipline).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ResolvedEntitlements:
    """Resolved (VALUE-level, not enforcement-level) entitlements for one tenant.

    plan_id is an echo of the input arg — None means "no plan was supplied to this
    resolution call" (either genuinely unplanned, or the caller only had budget-dimension
    inputs on hand, e.g. RedisBudgetGuard). Callers gate M6/M7 feature/allowlist
    enforcement on `plan_id is not None` themselves.
    """

    effective_budget_usd_monthly: Decimal | None
    plan_model_allowlist: list[str] | None  # None = no plan-level restriction
    plan_feature_flags: frozenset[str]  # empty set if unplanned or plan grants none
    plan_id: uuid.UUID | None  # None = unplanned (callers gate M6/M7 on this)
    # plan-seat-cap TASK.md §3 (FROZEN @ v1) — additive (M1). Same precedence SHAPE as
    # the budget dimension, computed independently: never perturbs any other field.
    effective_seat_cap: int | None = None


def resolve_entitlements(
    *,
    tenant_budget_usd_monthly: Decimal | None,
    plan_id: uuid.UUID | None,
    plan_budget_usd_monthly_default: Decimal | None,
    plan_model_allowlist: list[str] | None,
    plan_feature_flags: list[str] | None,  # DB NOT NULL DEFAULT '[]'; None only if plan_id is None
    # plan-seat-cap TASK.md §3 (FROZEN @ v1) — additive (M1), both OPTIONAL so every
    # EXISTING call site (RedisBudgetGuard, SqlAlchemyPlanEntitlementResolver's 2 call
    # sites) stays byte-identical, untouched.
    tenant_seat_cap: int | None = None,
    plan_seat_cap_default: int | None = None,
) -> ResolvedEntitlements:
    """Pure, zero I/O. Precedence (M1): explicit tenant setting > plan default > unlimited.

    Budget precedence is NULL-propagation only — it does NOT branch on `plan_id`. This is
    deliberate: a caller that resolves `plan_budget_usd_monthly_default` via a
    `LEFT JOIN plans` (e.g. RedisBudgetGuard) already gets `None` there whenever the
    tenant has no plan (the JOIN simply fails to match), so the "plan default only when
    plan_id is set" rule holds by construction without this function re-checking plan_id.
    That is also why RedisBudgetGuard is free to pass `plan_id=None` (M2 §3) — it never
    perturbs the budget dimension.

    A caller that only needs the budget dimension (RedisBudgetGuard) reads
    `.effective_budget_usd_monthly` off the result and may pass `None` for the
    allowlist/feature-flag args it does not have on hand — each dimension is computed
    independently, so an unused arg never perturbs the budget precedence.

    effective_seat_cap precedence mirrors budget exactly (plan-seat-cap TASK.md §3 M1):
    tenant_seat_cap if not None, else plan_seat_cap_default, else None (unlimited) — the
    SAME NULL-propagation-only shape, computed independently of every other dimension.
    """
    effective_budget = (
        tenant_budget_usd_monthly
        if tenant_budget_usd_monthly is not None
        else plan_budget_usd_monthly_default
    )
    effective_seat_cap = tenant_seat_cap if tenant_seat_cap is not None else plan_seat_cap_default
    flags = frozenset(plan_feature_flags) if plan_feature_flags else frozenset()
    return ResolvedEntitlements(
        effective_budget_usd_monthly=effective_budget,
        plan_model_allowlist=plan_model_allowlist,
        plan_feature_flags=flags,
        plan_id=plan_id,
        effective_seat_cap=effective_seat_cap,
    )


__all__ = ["ResolvedEntitlements", "resolve_entitlements"]
