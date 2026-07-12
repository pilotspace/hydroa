"""RED suite: resolve_entitlements — the pure M1 resolution core (TASK.md §3 CONTRACT,
FROZEN @ v1).

Pure-unit, zero I/O, zero DB, zero HTTP — asserts the VALUE-level precedence only.
RED until gateway.tenants.domain.entitlements does not exist / resolve_entitlements
raises ImportError.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from gateway.tenants.domain.entitlements import ResolvedEntitlements, resolve_entitlements

_PLAN_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# M1 — budget precedence (explicit tenant > plan default > unlimited)
# ---------------------------------------------------------------------------


def test_explicit_tenant_budget_beats_plan_default() -> None:
    """M1, M2 — tenant has its own explicit budget AND an assigned plan default; the
    explicit tenant value wins outright."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=Decimal("100.00"),
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=Decimal("500.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    assert result.effective_budget_usd_monthly == Decimal("100.00")


def test_plan_default_fills_gap_when_no_explicit_tenant_budget() -> None:
    """M1, M2 — no explicit tenant budget; the plan default fills the gap."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=Decimal("500.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    assert result.effective_budget_usd_monthly == Decimal("500.00")


def test_unplanned_tenant_with_no_explicit_budget_resolves_to_unlimited() -> None:
    """M1, M7 — plan_id None, no explicit tenant budget, no plan default to draw from ->
    resolves to None (unlimited), byte-identical to pre-task behavior."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=None,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    assert result.effective_budget_usd_monthly is None
    assert result.plan_id is None


def test_budget_precedence_is_null_propagation_not_plan_id_gated() -> None:
    """M2 §3 CONTRACT — RedisBudgetGuard passes plan_id=None (it never fetches the real
    plan_id) yet still needs the plan default to fill the gap when the LEFT JOIN produced
    a non-null plan_budget_usd_monthly_default. The precedence must NOT re-gate on
    plan_id — only NULL-propagation on tenant_budget_usd_monthly."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=None,
        plan_budget_usd_monthly_default=Decimal("50.00"),
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    assert result.effective_budget_usd_monthly == Decimal("50.00")


# ---------------------------------------------------------------------------
# M1 — allowlist / feature-flag dimensions are echoed independently
# ---------------------------------------------------------------------------


def test_plan_model_allowlist_is_echoed_verbatim() -> None:
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=["gpt-4o-mini"],
        plan_feature_flags=["batch"],
    )
    assert result.plan_model_allowlist == ["gpt-4o-mini"]
    assert result.plan_feature_flags == frozenset({"batch"})
    assert result.plan_id == _PLAN_ID


def test_null_plan_model_allowlist_means_no_restriction() -> None:
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=_PLAN_ID,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=[],
    )
    assert result.plan_model_allowlist is None
    assert result.plan_feature_flags == frozenset()


def test_caller_needing_only_budget_dimension_may_pass_none_for_the_rest() -> None:
    """M1 — 'A caller that only needs the budget dimension (RedisBudgetGuard) ... may pass
    None for the allowlist/feature-flag args it does not have on hand — the function
    computes each dimension independently, so an unused arg never perturbs the budget
    precedence.'"""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=Decimal("10.00"),
        plan_id=None,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    assert isinstance(result, ResolvedEntitlements)
    assert result.effective_budget_usd_monthly == Decimal("10.00")
    assert result.plan_model_allowlist is None
    assert result.plan_feature_flags == frozenset()


def test_zero_io_pure_function_is_frozen_dataclass_result() -> None:
    """Sanity: ResolvedEntitlements is immutable (frozen, slots) — a pure-value carrier,
    never a live/cached object a caller could accidentally mutate."""
    result = resolve_entitlements(
        tenant_budget_usd_monthly=None,
        plan_id=None,
        plan_budget_usd_monthly_default=None,
        plan_model_allowlist=None,
        plan_feature_flags=None,
    )
    try:
        result.plan_id = _PLAN_ID  # type: ignore[misc]
        raised = False
    except (AttributeError, TypeError):
        raised = True
    assert raised, "ResolvedEntitlements must be frozen"
