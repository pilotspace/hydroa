"""VERIFY-phase adversarial repros for plan-enforcement (TASK.md, phase=verify).

NOT part of the frozen §4 test_plan — these are throwaway repros written during the
verify gate to confirm/refute specific adversarial hypotheses. Left uncommitted per
verify instructions. Do not fold into the frozen suite.

Findings probed:
  1. put_guardrails's ml_moderation gate is narrower than the §3 CONTRACT pseudocode
     literally states ("ONLY inside `if "ml_moderation" in fields_set:` branch") — the
     shipped code adds `and body.ml_moderation is not None`, so an explicit
     `{"ml_moderation": null}` clear is NEVER gated, even for a plan lacking the
     feature. Confirmed here to be a NO-OP-safe narrowing (clearing never grants a
     capability), not a privilege escalation — but it IS an undisclosed deviation from
     the frozen contract text, and untested by the frozen §4 suite.
  2. Mid-flight plan unassignment (plan_id -> NULL via the existing
     PUT .../plan {plan_id: null} admin endpoint) takes effect on the VERY NEXT
     request/call — no caching layer anywhere in the enforcement chain (AuthzResult,
     check_plan_feature) staled the grandfathered-unlimited transition.
"""

from __future__ import annotations

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import assert_problem, assign_plan, auth, seed_plan, signup_owner

GUARDRAILS = "/admin/guardrails"
BATCH_POLICY = "/admin/batch-policy"


async def test_verify_ml_moderation_explicit_null_clear_bypasses_feature_gate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Repro for finding #1: a plan LACKING "ml_moderation" still allows an explicit
    `{"ml_moderation": null}` PUT to succeed (200), because guardrail_router.py's gate
    condition is `if "ml_moderation" in fields_set and body.ml_moderation is not None`,
    not the contract's literal `if "ml_moderation" in fields_set`.

    Judgment: SAFE narrowing (clearing can only turn a feature OFF, never ON — no
    capability is granted), but undisclosed vs the frozen §3 pseudocode and uncovered
    by the frozen §4 suite. Flagged as a concern, not a security HARD-STOP.
    """
    owner = await signup_owner(
        client, tenant_name="VerifyMlModClearCo", email="owner@verifymlmodclear.io"
    )
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["logs_explorer"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    # First prove the FORWARD direction is still correctly gated (sanity, matches
    # the frozen suite's own test_configuring_ml_moderation_refused_for_plan_lacking_feature).
    forward = await client.put(
        GUARDRAILS,
        json={"ml_moderation": {"enabled": True, "mode": "block"}},
        headers=auth(owner["jwt"]),
    )
    assert_problem(forward, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")

    # Now the explicit-null clear — contract's literal pseudocode says this should ALSO
    # be gated (any touch of the "ml_moderation" key while fields_set contains it); the
    # shipped code instead lets it through.
    clear = await client.put(
        GUARDRAILS,
        json={"ml_moderation": None},
        headers=auth(owner["jwt"]),
    )
    # This assertion documents the CURRENT (deviating) behavior — 200, not 403.
    assert clear.status_code == 200, (
        f"expected the shipped code's narrowed gate to let an explicit null-clear "
        f"through even for a plan lacking the feature; got {clear.status_code}: {clear.text}"
    )


async def test_verify_plan_unassignment_takes_effect_immediately_no_staleness(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Repro for finding #2 (grandfathering honesty): after a tenant is assigned a plan
    lacking "batch" (enable refused), directly clearing tenants.plan_id back to NULL
    (mirrors what PUT .../plan {plan_id: null} does at the row level) makes the VERY
    NEXT batch-enable request succeed — no cached AuthzResult/entitlement value keeps
    gating the tenant after unassignment.
    """
    from sqlalchemy import text as sa_text

    owner = await signup_owner(
        client, tenant_name="VerifyUnassignCo", email="owner@verifyunassign.io"
    )
    plan_id = await seed_plan(db_session, name="starter", feature_flags=[])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    gated = await client.put(BATCH_POLICY, json={"enabled": True}, headers=auth(owner["jwt"]))
    assert_problem(gated, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")

    # Unassign the plan (byte-identical row-level effect to the admin
    # PUT .../plan {plan_id: null} endpoint).
    await db_session.execute(
        sa_text("UPDATE tenants SET plan_id = NULL WHERE id = :tid"),
        {"tid": owner["tenant_id"]},
    )
    await db_session.commit()

    freed = await client.put(BATCH_POLICY, json={"enabled": True}, headers=auth(owner["jwt"]))
    assert freed.status_code == 200, (
        f"expected immediate grandfathered-unlimited on the next request after "
        f"unassignment, got {freed.status_code}: {freed.text}"
    )
