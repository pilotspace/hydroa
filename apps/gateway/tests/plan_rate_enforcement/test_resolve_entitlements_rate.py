"""RED suite: the M1 rpm/tpm-precedence extension to resolve_entitlements
(plan-rate-enforcement TASK.md §3 CONTRACT M1, FROZEN @ v1).

Pure-unit, zero I/O, zero DB, zero HTTP — mirrors tests/plan_seat_cap/
test_resolve_entitlements_seat.py's own style exactly, extended for the NEW
`effective_rpm_limit`/`effective_tpm_limit` fields and the four new kwargs.

RED until `effective_rpm_limit`/`effective_tpm_limit` don't exist on ResolvedEntitlements
/ the four new kwargs don't exist on resolve_entitlements (TypeError: unexpected keyword
argument).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from gateway.tenants.domain.entitlements import ResolvedEntitlements, resolve_entitlements

_PLAN_ID = uuid.uuid4()


def test_resolve_adds_rate_dimensions() -> None:
    """§2 Scenario 'resolve adds rpm/tpm dimensions' — a plan with rpm_limit_default=60,
    tpm_limit_default=100000 and NO tenant override resolves effective_rpm_limit=60 /
    effective_tpm_limit=100000, and every pre-existing dimension (budget, seat_cap) is
    unaffected by supplying the new kwargs. Covers: M1.
    """
    result = resolve_entitlements(
        tenant_budget_usd_monthly=Decimal("10.00"),
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=Decimal("50.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_seat_cap=None,
        plan_seat_cap_default=5,
        tenant_rpm_limit=None,
        plan_rpm_limit_default=60,
        tenant_tpm_limit=None,
        plan_tpm_limit_default=100000,
    )
    assert isinstance(result, ResolvedEntitlements)
    assert result.effective_rpm_limit == 60
    assert result.effective_tpm_limit == 100000
    # Every pre-existing dimension stays computed exactly as it was before this task.
    assert result.effective_budget_usd_monthly == Decimal("10.00")
    assert result.effective_seat_cap == 5


def test_resolve_tenant_override_wins() -> None:
    """§2 Scenario (implied by M0/M1) — an explicit tenant rpm/tpm override beats the
    plan's own default, computed independently per dimension (mirrors budget/seat_cap's
    own precedence exactly). Covers: M0, M1.
    """
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_rpm_limit=10,
        plan_rpm_limit_default=600,
        tenant_tpm_limit=5000,
        plan_tpm_limit_default=400000,
    )
    assert result.effective_rpm_limit == 10
    assert result.effective_tpm_limit == 5000

    # Independence check: overriding ONE dimension leaves the OTHER resolving purely off
    # the plan default when its own tenant override is None.
    partial = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
        tenant_rpm_limit=10,
        plan_rpm_limit_default=600,
        tenant_tpm_limit=None,
        plan_tpm_limit_default=400000,
    )
    assert partial.effective_rpm_limit == 10
    assert partial.effective_tpm_limit == 400000


def test_existing_caller_unaffected_by_the_new_rate_kwargs() -> None:
    """An existing resolve_entitlements caller (e.g. RedisBudgetGuard's own call, which
    never supplies the four new rate kwargs) is byte-identical: effective_budget_usd_monthly
    resolves as before AND effective_rpm_limit/effective_tpm_limit both resolve to None
    (never perturbs the budget dimension). Covers: M1 (byte-identical existing callers).
    """
    result = resolve_entitlements(
        tenant_budget_usd_monthly=Decimal("10.00"),
        plan_id=None,
        plan_budget_usd_monthly_default=Decimal("50.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
        # tenant_rpm_limit / plan_rpm_limit_default / tenant_tpm_limit /
        # plan_tpm_limit_default deliberately OMITTED — byte-identical to every
        # pre-task call site.
    )
    assert result.effective_budget_usd_monthly == Decimal("10.00")
    assert result.effective_rpm_limit is None
    assert result.effective_tpm_limit is None
