"""Independent VERIFY probes for GET /admin/plan (billing-ui TASK.md §6, add-verify pass).

Separate from `test_plan_router.py` (the builder's own red-first suite) on purpose —
these probes exist to check claims the builder's own suite did not, not to re-run it.
Never edits `test_plan_router.py` or any frozen contract; read-only findings, evidenced
here as reproducible tests rather than code-read inference alone.

Findings this file evidences:
  1. `test_plan_router.py::test_any_authenticated_role_passes` parametrizes 6 roles but
     OMITS Role.SUPERADMIN, even though §3 CONTRACT's own docstring names it explicitly
     ("any authenticated role (owner/admin/operator/billing_admin/viewer/member/
     superadmin)"). Closed here — a real, if minor, coverage gap in the builder's suite.
  2. Tenant isolation for GET /admin/plan is structurally guaranteed (the endpoint takes
     no tenant_id input; it always resolves `identity.tenant_id` from the JWT) — but the
     builder's suite never has two tenants in the same test, so the isolation claim was
     never actually exercised end-to-end. Probed explicitly here.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .test_plan_router import ADMIN_PLAN, PASSWORD, assign_plan, auth, mint_role_token, seed_plan, signup_owner


async def test_superadmin_role_passes(client: httpx.AsyncClient, app) -> None:
    """SUPERADMIN is named explicitly in §3 CONTRACT's own RBAC docstring but was
    OMITTED from the builder's own `test_any_authenticated_role_passes` parametrize
    list (only OWNER/ADMIN/OPERATOR/BILLING_ADMIN/VIEWER/MEMBER were covered) — closing
    that gap independently here."""
    owner = await signup_owner(client, tenant_name="SuperadminPlanCo", email="owner@superplan.io")
    token = mint_role_token(
        app, tenant_id=owner["tenant_id"], role=Role.SUPERADMIN, email="superadmin@superplan.io"
    )

    resp = await client.get(ADMIN_PLAN, headers=auth(token))

    assert resp.status_code == 200, f"SUPERADMIN expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["plan"] is None  # unplanned tenant, no error
    assert "resolved" in body


async def test_tenant_isolation_plan_data_never_crosses_tenants(
    client: httpx.AsyncClient, app, db_session: AsyncSession
) -> None:
    """Explicit, end-to-end tenant-isolation probe: two DISTINCT tenants, each assigned
    a DIFFERENT plan; tenant B's own GET /admin/plan must reflect ONLY tenant B's plan,
    never tenant A's — even though the endpoint accepts zero tenant-identifying input
    besides the caller's own JWT (structural isolation, verified here behaviorally, not
    just by code-reading `identity.tenant_id`)."""
    tenant_a = await signup_owner(client, tenant_name="IsoPlanCo-A", email="owner-a@isoplan.io")
    tenant_b = await signup_owner(client, tenant_name="IsoPlanCo-B", email="owner-b@isoplan.io")

    plan_a = await seed_plan(
        db_session, name="enterprise", display_name="Enterprise", seat_cap=500,
        budget_usd_monthly_default="5000.00", feature_flags=["sso"],
    )
    plan_b = await seed_plan(
        db_session, name="starter", display_name="Starter", seat_cap=3,
        budget_usd_monthly_default="5.00", feature_flags=[],
    )
    await assign_plan(db_session, tenant_id=tenant_a["tenant_id"], plan_id=plan_a)
    await assign_plan(db_session, tenant_id=tenant_b["tenant_id"], plan_id=plan_b)

    resp_a = await client.get(ADMIN_PLAN, headers=auth(tenant_a["jwt"]))
    resp_b = await client.get(ADMIN_PLAN, headers=auth(tenant_b["jwt"]))

    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    body_a, body_b = resp_a.json(), resp_b.json()

    assert body_a["plan"]["name"] == "enterprise"
    assert body_a["plan"]["seat_cap"] == 500
    assert body_b["plan"]["name"] == "starter"
    assert body_b["plan"]["seat_cap"] == 3
    # the actual isolation assertion: B's response contains NOTHING from A's plan
    assert body_b["plan"]["id"] != body_a["plan"]["id"]
    assert "sso" not in body_b["resolved"]["plan_feature_flags"]
    assert body_b["resolved"]["effective_budget_usd_monthly"] != body_a["resolved"]["effective_budget_usd_monthly"]
