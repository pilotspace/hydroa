"""RED suite: the M1 seat-precedence extension to resolve_entitlements (TASK.md §3
CONTRACT M1, FROZEN @ v1).

Pure-unit, zero I/O, zero DB, zero HTTP — mirrors tests/plan_enforcement/
test_resolve_entitlements.py's own style exactly, extended for the NEW
`effective_seat_cap` field / `tenant_seat_cap` / `plan_seat_cap_default` kwargs.

RED until `effective_seat_cap` does not exist on ResolvedEntitlements / the two new
kwargs do not exist on resolve_entitlements (TypeError: unexpected keyword argument).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from gateway.tenants.domain.entitlements import ResolvedEntitlements, resolve_entitlements

_PLAN_ID = uuid.uuid4()


def test_explicit_tenant_seat_cap_beats_plan_default() -> None:
    """Scenario: 'Explicit tenant seat_cap override beats the plan's default' — tenant
    assigned plan 'team' (seat_cap=50) with its own explicit tenant seat_cap=10 -> the
    resolved effective_seat_cap is 10, not 50."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_seat_cap=10,
        plan_seat_cap_default=50,
    )
    assert result.effective_seat_cap == 10


def test_plan_seat_cap_default_fills_gap_when_no_tenant_override() -> None:
    """Scenario: 'Plan's seat_cap default fills the gap when no tenant override is set' —
    plan 'starter' (seat_cap=5), tenant seat_cap=NULL -> resolved effective_seat_cap is 5."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_seat_cap=None,
        plan_seat_cap_default=5,
    )
    assert result.effective_seat_cap == 5


def test_existing_caller_unaffected_by_the_new_kwargs() -> None:
    """Scenario: 'An existing resolve_entitlements caller is unaffected by the new
    kwargs' — RedisBudgetGuard's own existing call never supplies tenant_seat_cap or
    plan_seat_cap_default; effective_budget_usd_monthly resolves identically to pre-task
    behavior AND effective_seat_cap resolves to None (never perturbs the budget
    dimension)."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=Decimal("10.00"),
        plan_id=None,
        plan_budget_usd_monthly_default=Decimal("50.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
        # tenant_seat_cap / plan_seat_cap_default deliberately OMITTED — byte-identical
        # to every pre-task call site.
    )
    assert isinstance(result, ResolvedEntitlements)
    assert result.effective_budget_usd_monthly == Decimal("10.00")
    assert result.effective_seat_cap is None
