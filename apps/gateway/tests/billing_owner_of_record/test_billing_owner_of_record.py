"""RED->GREEN suite for billing-owner-of-record (TASK.md §2 SCENARIOS + §3 CONTRACT,
FROZEN @ v1). One test per scenario (M2-M7 application-level + all R1-R9 adversarial
rejects); the M1/M8 migration-backfill scenarios live in
tests/migrations/test_billing_owner_of_record_backfill.py.

SECURITY PROPERTIES PROVEN SERVER-SIDE (appsec-engineer persona, risk: high, sensitivity:
security):
  - Never-zero-billing-owner: HOOK 1 (role-change, both self-service AND superadmin
    cross-tenant routers) and HOOK 2 (SCIM deactivation, PATCH + DELETE-alias) both
    reject a write that would leave the tenant's designated billing owner
    non-billing-capable or inactive — every reject asserts BOTH the error status AND that
    the underlying DB row did NOT change (no silent partial write).
  - Confused-deputy safety: reassigning to a user in a DIFFERENT tenant 404s
    byte-identically to an unknown user_id (R5) — no existence oracle.
  - Race safety (R9): concurrent reassign-to-X and demote-X never both succeed — proven
    with a forced `asyncio.Barrier` rendezvous at the shared FOR-UPDATE lock acquisition
    point (a plain `asyncio.gather` alone would not reliably force genuine interleaving).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

from .conftest import (
    add_user_to_team,
    bearer,
    create_scim_token,
    create_team,
    get_billing_owner_user_id,
    get_user_state,
    insert_user,
    issue_jwt,
    login,
    set_billing_owner,
    signup_tenant,
    team_members_for_user,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixture: a tenant with an OWNER (A) already designated as billing owner
# ---------------------------------------------------------------------------


@pytest.fixture
async def tenant_a(client: httpx.AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    signup = await signup_tenant(client, tenant_name="BillCo", email="owner-a@billingownertest.io")
    tenant_id = uuid.UUID(str(signup["tenant_id"]))
    owner_user_id = uuid.UUID(str(signup["user_id"]))
    owner_token = await login(client, email="owner-a@billingownertest.io")
    await set_billing_owner(db_session, tenant_id=tenant_id, user_id=owner_user_id)
    return {
        "tenant_id": tenant_id,
        "owner_user_id": owner_user_id,
        "owner_token": owner_token,
    }


# ===========================================================================
# M2 — role change on an ordinary member is unaffected (byte-identical baseline)
# ===========================================================================


async def test_role_change_ordinary_member_unaffected(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]
    b_id = await insert_user(
        db_session, tenant_id=tenant_id, email="b@billingownertest.io", role="member"
    )

    resp = await client.put(
        f"/admin/users/{b_id}/role", json={"role": "operator"}, headers=bearer(owner_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "operator"

    role, _ = await get_user_state(db_session, b_id)
    assert role == "operator"
    # B's tenant billing owner is unaffected.
    assert await get_billing_owner_user_id(db_session, tenant_id) == tenant_a["owner_user_id"]


# ===========================================================================
# M3 — SCIM deactivates a non-owner user normally (byte-identical baseline)
# ===========================================================================


async def test_scim_deactivates_non_owner_normally(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]
    scim_token = await create_scim_token(client, owner_token=owner_token)

    c_id = await insert_user(
        db_session, tenant_id=tenant_id, email="c@billingownertest.io", role="member"
    )
    team_id = await create_team(db_session, tenant_id=tenant_id, name="Core")
    await add_user_to_team(db_session, team_id=team_id, user_id=c_id)
    assert await team_members_for_user(db_session, c_id) == 1

    resp = await client.patch(
        f"/scim/v2/Users/{c_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=bearer(scim_token),
    )
    assert resp.status_code == 200, resp.text

    _role, deactivated_at = await get_user_state(db_session, c_id)
    assert deactivated_at is not None
    assert await team_members_for_user(db_session, c_id) == 0
    # Tenant A's billing owner is unaffected.
    assert await get_billing_owner_user_id(db_session, tenant_id) == tenant_a["owner_user_id"]


# ===========================================================================
# M4/M5 — reassign THEN demote succeeds (happy path)
# ===========================================================================


async def test_reassign_then_demote_succeeds(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]
    a_id = tenant_a["owner_user_id"]

    b_id = await insert_user(
        db_session, tenant_id=tenant_id, email="b@billingownertest.io", role="billing_admin"
    )
    # A second OWNER-capable caller — the pre-existing self-guard makes A demoting
    # itself structurally unreachable regardless of billing-owner state (R7).
    second_owner_token = issue_jwt(app, role=Role.OWNER, tenant_id=tenant_id)

    reassign = await client.put(
        "/admin/billing-owner", json={"user_id": str(b_id)}, headers=bearer(owner_token)
    )
    assert reassign.status_code == 200, reassign.text
    assert reassign.json()["user_id"] == str(b_id)
    assert await get_billing_owner_user_id(db_session, tenant_id) == b_id

    demote = await client.put(
        f"/admin/users/{a_id}/role",
        json={"role": "member"},
        headers=bearer(second_owner_token),
    )
    assert demote.status_code == 200, demote.text
    assert demote.json()["role"] == "member"
    role, _ = await get_user_state(db_session, a_id)
    assert role == "member"


# ===========================================================================
# M5 — reassigning to the current billing owner is an idempotent no-op
# ===========================================================================


async def test_reassign_idempotent_noop(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    owner_token = tenant_a["owner_token"]

    resp = await client.put(
        "/admin/billing-owner", json={"user_id": str(a_id)}, headers=bearer(owner_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user_id"] == str(a_id)
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# M6 — GET returns the current designation
# ===========================================================================


async def test_get_billing_owner_returns_designation(
    client: httpx.AsyncClient, app: Any, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    # Any authenticated tenant member, not just the owner.
    member_token = issue_jwt(app, role=Role.MEMBER, tenant_id=tenant_id)

    resp = await client.get("/admin/billing-owner", headers=bearer(member_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user_id"] == str(a_id)
    assert body["email"] == "owner-a@billingownertest.io"
    assert body["role"] == "owner"


async def test_get_billing_owner_null_when_unset(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    """The GET's nullable shape (platform tenant / unresolved backfill edge, M6).

    billing-owner-signup-population reconciliation: signup now stamps the founding OWNER as
    billing-owner-of-record, so the unset state must be produced explicitly — NULL the pointer
    directly (the exact 'unresolved backfill edge / platform tenant' state this test asserts,
    which GetBillingOwnerUseCase maps to {null,null,null}). Intent unchanged: GET's nullable
    shape when billing_owner_user_id IS NULL."""
    signup = await signup_tenant(
        client, tenant_name="NullCo", email="owner-null@billingownertest.io"
    )
    await db_session.execute(
        text("UPDATE tenants SET billing_owner_user_id = NULL WHERE id = :tid"),
        {"tid": signup["tenant_id"]},
    )
    await db_session.commit()
    owner_token = await login(client, email="owner-null@billingownertest.io")

    resp = await client.get("/admin/billing-owner", headers=bearer(owner_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": None, "email": None, "role": None}


# ===========================================================================
# M7 — invoice attribution resolves via the tenant join, no new column
# ===========================================================================


async def test_invoice_attribution_resolves_via_join(
    db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]

    invoice_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO invoices (id, tenant_id, period_start, period_end)
            VALUES (:id, :tid, now(), now() + interval '1 month')
            """
        ),
        {"id": invoice_id, "tid": tenant_id},
    )
    await db_session.commit()

    row = await db_session.execute(
        text(
            "SELECT t.billing_owner_user_id FROM invoices i "
            "JOIN tenants t ON t.id = i.tenant_id WHERE i.id = :iid"
        ),
        {"iid": invoice_id},
    )
    assert row.scalar_one() == a_id

    cols = await db_session.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = 'invoices'")
    )
    col_names = {r[0] for r in cols.fetchall()}
    assert "billing_owner_user_id" not in col_names, (
        "M7: invoices must carry NO new column — attribution is a read-side join only"
    )


# ===========================================================================
# R1 — demote-last-billing-owner is rejected
# ===========================================================================


async def test_demote_last_billing_owner_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    # A different OWNER caller — R1 is distinct from R7's self-demote case.
    caller_token = issue_jwt(app, role=Role.OWNER, tenant_id=tenant_id)

    resp = await client.put(
        f"/admin/users/{a_id}/role", json={"role": "member"}, headers=bearer(caller_token)
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "ERR_LAST_BILLING_OWNER", resp.text

    role, _ = await get_user_state(db_session, a_id)
    assert role == "owner", "role must be UNCHANGED on reject"
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# R2 — deactivate-last-billing-owner is rejected
# ===========================================================================


async def test_deactivate_last_billing_owner_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    owner_token = tenant_a["owner_token"]
    scim_token = await create_scim_token(client, owner_token=owner_token)

    team_id = await create_team(db_session, tenant_id=tenant_id, name="Core")
    await add_user_to_team(db_session, team_id=team_id, user_id=a_id)
    assert await team_members_for_user(db_session, a_id) == 1

    resp = await client.patch(
        f"/scim/v2/Users/{a_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=bearer(scim_token),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "ERR_LAST_BILLING_OWNER", resp.text

    _role, deactivated_at = await get_user_state(db_session, a_id)
    assert deactivated_at is None, "deactivated_at must stay NULL on reject"
    assert await team_members_for_user(db_session, a_id) == 1, "team_members must be untouched"
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


async def test_deactivate_last_billing_owner_rejected_via_delete_alias(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    """R2, DELETE-alias variant (M6 of scim-provisioning: DELETE == PATCH active:false)."""
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    owner_token = tenant_a["owner_token"]
    scim_token = await create_scim_token(client, owner_token=owner_token)

    resp = await client.delete(f"/scim/v2/Users/{a_id}", headers=bearer(scim_token))
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "ERR_LAST_BILLING_OWNER", resp.text

    _role, deactivated_at = await get_user_state(db_session, a_id)
    assert deactivated_at is None
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# R3 — reassign to a non-billing-capable user is rejected
# ===========================================================================


async def test_reassign_to_non_billing_capable_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]
    d_id = await insert_user(
        db_session, tenant_id=tenant_id, email="d@billingownertest.io", role="viewer"
    )

    resp = await client.put(
        "/admin/billing-owner", json={"user_id": str(d_id)}, headers=bearer(owner_token)
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_BILLING_OWNER_INELIGIBLE", resp.text
    assert await get_billing_owner_user_id(db_session, tenant_id) == tenant_a["owner_user_id"]


# ===========================================================================
# R4 — reassign to a deactivated user is rejected
# ===========================================================================


async def test_reassign_to_deactivated_user_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]
    e_id = await insert_user(
        db_session,
        tenant_id=tenant_id,
        email="e@billingownertest.io",
        role="owner",
        deactivated=True,
    )

    resp = await client.put(
        "/admin/billing-owner", json={"user_id": str(e_id)}, headers=bearer(owner_token)
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_BILLING_OWNER_INELIGIBLE", resp.text
    assert await get_billing_owner_user_id(db_session, tenant_id) == tenant_a["owner_user_id"]


# ===========================================================================
# R5 — reassign to another tenant's user is rejected (confused deputy)
# ===========================================================================


async def test_reassign_to_another_tenant_user_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    owner_token = tenant_a["owner_token"]

    other_signup = await signup_tenant(
        client, tenant_name="OtherCo", email="owner-f@billingownertest.io"
    )
    f_id = uuid.UUID(str(other_signup["user_id"]))

    resp = await client.put(
        "/admin/billing-owner", json={"user_id": str(f_id)}, headers=bearer(owner_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_USER_NOT_FOUND", resp.text

    # Byte-identical to an unknown user_id.
    unknown_resp = await client.put(
        "/admin/billing-owner", json={"user_id": str(uuid.uuid4())}, headers=bearer(owner_token)
    )
    assert unknown_resp.status_code == resp.status_code
    assert unknown_resp.json() == resp.json()

    assert await get_billing_owner_user_id(db_session, tenant_id) == tenant_a["owner_user_id"]


# ===========================================================================
# R6 — reassignment by a non-OWNER caller is rejected
# ===========================================================================


async def test_reassign_by_non_owner_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]

    for role_str in ["admin", "operator", "billing_admin", "viewer", "member"]:
        token = issue_jwt(app, role=Role(role_str), tenant_id=tenant_id)
        resp = await client.put(
            "/admin/billing-owner", json={"user_id": str(a_id)}, headers=bearer(token)
        )
        assert resp.status_code == 403, f"role={role_str}: {resp.text}"
        assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text

    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# R7 — self-demote by the sole billing owner is rejected by the PRE-EXISTING self-guard
# ===========================================================================


async def test_self_demote_sole_billing_owner_preexisting_guard(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    owner_token = tenant_a["owner_token"]

    resp = await client.put(
        f"/admin/users/{a_id}/role", json={"role": "member"}, headers=bearer(owner_token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text
    # NOT 409 — the self-guard fires before the new M2 guard is ever reached.
    assert resp.json().get("code") != "ERR_LAST_BILLING_OWNER"

    role, _ = await get_user_state(db_session, a_id)
    assert role == "owner"
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# R8 — superadmin cross-tenant demote-last-billing-owner is rejected identically
# ===========================================================================


async def test_superadmin_cross_tenant_demote_last_billing_owner_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    tenant_a: dict[str, Any],
    platform_tenant_id: uuid.UUID,
) -> None:
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    superadmin_token = issue_jwt(app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id)

    resp = await client.put(
        f"/admin/platform/tenants/{tenant_id}/users/{a_id}/role",
        json={"role": "member"},
        headers=bearer(superadmin_token),
    )
    assert resp.status_code == 409, resp.text
    assert resp.json().get("code") == "ERR_LAST_BILLING_OWNER", resp.text

    role, _ = await get_user_state(db_session, a_id)
    assert role == "owner"
    assert await get_billing_owner_user_id(db_session, tenant_id) == a_id


# ===========================================================================
# R9 — concurrent reassign-to-X and demote-X never both succeed
# ===========================================================================


async def test_concurrent_reassign_and_demote_race(
    client: httpx.AsyncClient, db_session: AsyncSession, tenant_a: dict[str, Any]
) -> None:
    """Forces genuine interleaving (an asyncio.Barrier) at the shared M4 FOR-UPDATE lock
    acquisition point so both transactions attempt the SAME row lock at once — a plain
    `asyncio.gather` alone is not sufficient to reliably force this race (mirrors
    tests/credits_ledger/test_verify_adversarial.py's own barrier-forcing precedent)."""
    tenant_id = tenant_a["tenant_id"]
    a_id = tenant_a["owner_user_id"]
    owner_token = tenant_a["owner_token"]

    x_id = await insert_user(
        db_session, tenant_id=tenant_id, email="x@billingownertest.io", role="billing_admin"
    )

    barrier = asyncio.Barrier(2)
    original = UserRoleRepository.lock_and_get_billing_owner_user_id

    async def _rendezvous_then_lock(
        self: UserRoleRepository, *, tenant_id: uuid.UUID
    ) -> uuid.UUID | None:
        await barrier.wait()
        return await original(self, tenant_id=tenant_id)

    UserRoleRepository.lock_and_get_billing_owner_user_id = _rendezvous_then_lock  # type: ignore[method-assign]
    try:
        reassign_resp, demote_resp = await asyncio.gather(
            client.put(
                "/admin/billing-owner", json={"user_id": str(x_id)}, headers=bearer(owner_token)
            ),
            client.put(
                f"/admin/users/{x_id}/role", json={"role": "member"}, headers=bearer(owner_token)
            ),
        )
    finally:
        UserRoleRepository.lock_and_get_billing_owner_user_id = original  # type: ignore[method-assign]

    final_owner = await get_billing_owner_user_id(db_session, tenant_id)
    final_role, _ = await get_user_state(db_session, x_id)

    # Never both succeed, and the invariant (billing_owner_user_id always points to an
    # ACTIVE billing-capable user) holds in EITHER legitimate outcome.
    if final_owner == x_id:
        # Outcome (a): reassign committed first -> billing_owner_user_id became X ->
        # the demotion then correctly re-reads the post-commit owner and 409s.
        assert reassign_resp.status_code == 200, reassign_resp.text
        assert demote_resp.status_code == 409, demote_resp.text
        assert demote_resp.json().get("code") == "ERR_LAST_BILLING_OWNER"
        assert final_role == "billing_admin", "rejected demotion must leave X's role unchanged"
    else:
        # Outcome (b): demote committed first (X was never the owner yet) -> it
        # succeeds -> the reassignment then re-validates X against the post-commit role
        # and correctly 422s.
        assert final_owner == a_id, "billing_owner_user_id must be unchanged on this branch"
        assert demote_resp.status_code == 200, demote_resp.text
        assert reassign_resp.status_code == 422, reassign_resp.text
        assert reassign_resp.json().get("code") == "ERR_BILLING_OWNER_INELIGIBLE"
        assert final_role == "member"
