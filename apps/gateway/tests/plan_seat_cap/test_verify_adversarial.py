"""Independent adversarial VERIFY probes for plan-seat-cap (TASK.md §3 CONTRACT, FROZEN @
v1) — written by an INDEPENDENT verify agent, not the builder. These are NEW tests only;
nothing here edits src/ or any existing test.

Goal: refute the green — find what the builder's own 23 tests (all passing) do NOT cover:
  1. A cross-seam race where the newly-introduced SCIM transaction shape (the flagged §5
     deviation) is one of the two competitors — the builder's own concurrency test only
     raced invite-accept vs OIDC, never touching SCIM's restructured autobegin-reuse path.
  2. SAML's existing-member re-login (the builder tested this for OIDC only, R2/M7).
  3. The disclosed-but-unguarded SCIM PATCH active:true reactivation seam (TASK.md §0/§1
     "Ruled out, not silently") — confirmed here as a real, quantified over-cap bypass,
     not merely asserted from reading the code.
  4. tenant.seat_cap=0 boundary — ruled out as DB-unreachable (CheckConstraint), confirmed
     directly against Postgres rather than assumed from reading orm.py.
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.saml_sso.saml_fixtures import build_signed_response, generate_idp_keypair

from .conftest import (
    active_user_count,
    assert_problem,
    assert_scim_error,
    assign_plan,
    bearer,
    build_oidc_app,
    create_scim_token,
    deactivate_user,
    get_cookies_from_response,
    oidc_callback,
    put_saml_config,
    scim_bearer,
    seed_extra_active_users,
    seed_plan,
    signup_owner,
    start_saml_login_and_get_request_id,
    user_exists,
)

pytestmark = pytest.mark.asyncio


async def _create_pending_invite(
    client: httpx.AsyncClient, *, owner_token: str, email: str, role: str = "member"
) -> dict[str, str]:
    r = await client.post(
        "/admin/invites", json={"email": email, "role": role}, headers=bearer(owner_token)
    )
    assert r.status_code == 201, f"setup failed creating invite: {r.text}"
    result: dict[str, str] = r.json()
    return result


# ---------------------------------------------------------------------------
# Probe 1 — cross-seam race: SCIM create_user vs invite-accept, racing the LAST seat.
# The builder's own concurrency test (test_seat_cap_audit_and_concurrency.py) only ever
# races invite-accept vs OIDC — never exercises SCIM's own restructured transaction (the
# §5 "Strategy actually used" flagged deviation: create_user reuses an ALREADY-open
# autobegin transaction from get_scim_identity's bearer-token SELECT, rather than calling
# session.begin() itself). If that reused-transaction lock does not actually hold through
# create_user's own INSERT the same way it holds for the other 3 seams, this is where a
# double-admission would surface.
# ---------------------------------------------------------------------------


async def test_scim_vs_invite_accept_race_for_last_seat_exactly_one_wins(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@scimraceseatcap.example")
    plan_id = await seed_plan(db_session, name="starter-scimrace", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=3)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 4

    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="racer-invite@scimraceseatcap.example"
    )
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    invite_resp, scim_resp = await asyncio.gather(
        client.post(
            f"/invites/{invite['token']}/accept",
            json={"password": "correct horse battery staple"},
        ),
        client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "racer-scim@scimraceseatcap.example",
                "active": True,
            },
            headers=scim_bearer(scim_token),
        ),
    )

    invite_won = invite_resp.status_code == 200
    scim_won = scim_resp.status_code == 201

    assert invite_won != scim_won, (
        f"expected exactly one winner, got invite={invite_resp.status_code} "
        f"scim={scim_resp.status_code} (invite body={invite_resp.text!r} "
        f"scim body={scim_resp.text!r})"
    )
    if not invite_won:
        assert_problem(invite_resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")
    if not scim_won:
        assert_scim_error(scim_resp, 403, detail_contains="Seat cap")

    # The load-bearing assertion: never both succeeding (6 active, over cap of 5), never
    # both failing (a real seat left unfilled while both callers saw a false rejection).
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5
    assert await user_exists(db_session, email="racer-invite@scimraceseatcap.example") == invite_won
    assert await user_exists(db_session, email="racer-scim@scimraceseatcap.example") == scim_won


async def test_scim_vs_scim_race_for_last_seat_exactly_one_wins(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Same-seam SCIM-vs-SCIM race (two concurrent IdP calls, same tenant, same token) —
    the narrowest possible test of whether create_user's own reused-autobegin-transaction
    lock actually serializes two callers hitting the identical code path at once."""
    owner = await signup_owner(client, email="owner@scimscimrace.example")
    plan_id = await seed_plan(db_session, name="starter-scimscimrace", seat_cap=5)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    # ONE seat remaining (4/5) — the correct outcome is exactly one 201 and one 403.
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=3)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 4

    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])

    resp_a, resp_b = await asyncio.gather(
        client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "racer-a@scimscimrace.example",
                "active": True,
            },
            headers=scim_bearer(scim_token),
        ),
        client.post(
            "/scim/v2/Users",
            json={
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
                "userName": "racer-b@scimscimrace.example",
                "active": True,
            },
            headers=scim_bearer(scim_token),
        ),
    )

    statuses = sorted([resp_a.status_code, resp_b.status_code])
    assert statuses == [201, 403], (
        f"expected exactly one 201 and one 403, got {resp_a.status_code}/{resp_b.status_code}: "
        f"a={resp_a.text!r} b={resp_b.text!r}"
    )
    # Never both succeeding (6 active, over cap of 5), never both failing (a real seat
    # left unfilled while both callers saw a false rejection).
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 5


# ---------------------------------------------------------------------------
# Probe 2 — SAML existing-member re-login at a full tenant (M7). The builder's own
# `test_existing_member_oidc_relogin_never_gated_by_seat_cap` covers OIDC only; SAML
# shares the SAME `_get_or_provision_sso_user` helper but goes through a DIFFERENT
# router (saml_router.py) with its OWN except-clause — an independent wiring point that
# could regress without the OIDC test catching it.
# ---------------------------------------------------------------------------


async def test_existing_member_saml_relogin_never_gated_by_seat_cap(
    saml_client: httpx.AsyncClient, saml_db_session: AsyncSession
) -> None:
    owner = await signup_owner(saml_client, email="owner@samlrelogin.example")
    plan_id = await seed_plan(saml_db_session, name="team-saml-relogin", seat_cap=20)
    await assign_plan(saml_db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    existing_emails = await seed_extra_active_users(
        saml_db_session, tenant_id=owner["tenant_id"], count=19, email_prefix="samlrelogin"
    )
    assert await active_user_count(saml_db_session, tenant_id=owner["tenant_id"]) == 20
    existing_email = existing_emails[0]

    keypair = generate_idp_keypair()
    # SANCTIONED EDIT (domain-routing-unification CR-v2, 2026-07-18): write-gate now
    # requires a verified claim first — precondition added, assertion intent unchanged.
    config_resp = await put_saml_config(
        saml_client,
        owner_token=owner["owner_token"],
        idp_x509_cert=keypair.cert_pem,
        idp_entity_id="https://fake-idp.test/entity-samlrelogin",
        idp_sso_url="https://fake-idp.test/sso-samlrelogin",
        email_domains=[existing_email.split("@", 1)[-1]],
        db_session=saml_db_session,
    )
    assert config_resp.status_code == 200, config_resp.text
    config = config_resp.json()

    request_id = await start_saml_login_and_get_request_id(
        saml_client, domain=existing_email.split("@", 1)[-1]
    )
    saml_response_b64 = build_signed_response(
        idp_entity_id="https://fake-idp.test/entity-samlrelogin",
        sp_entity_id=config["sp_entity_id"],
        acs_url=config["acs_url"],
        request_id=request_id,
        keypair=keypair,
        subject_email=existing_email,
    )

    resp = await saml_client.post(
        "/auth/saml/acs", data={"SAMLResponse": saml_response_b64}, follow_redirects=False
    )

    assert resp.status_code == 302, (
        f"an EXISTING member's SAML re-login must NEVER be gated by the seat cap, got "
        f"{resp.status_code}: {resp.text}"
    )
    assert "ai_proxy_session" in get_cookies_from_response(resp)
    assert await active_user_count(saml_db_session, tenant_id=owner["tenant_id"]) == 20


# ---------------------------------------------------------------------------
# Probe 3 — SCIM PATCH active:true reactivation (TASK.md §0/§1, "Ruled out, not
# silently" — disclosed as a real gap, not gated by this task's Must list). Confirm the
# exposure is REAL and quantify it: an at-cap tenant with churned (deactivated) members
# can be pushed OVER its effective cap via reactivation, with zero rejection.
# ---------------------------------------------------------------------------


async def test_scim_reactivation_bypasses_seat_cap_pushing_tenant_over_cap(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@scimreactivatebypass.example")
    plan_id = await seed_plan(db_session, name="starter-reactivate-bypass", seat_cap=3)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    # Fill to cap: owner (1) + 2 more active = 3/3.
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=2)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 3

    # Simulate 2 CHURNED (deactivated) former members in the SAME tenant.
    churned_ids: list[str] = []
    for i in range(2):
        uid = uuid.uuid4()
        email = f"churned-{i}-{uuid.uuid4().hex[:8]}@scimreactivatebypass.example"
        await db_session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method, "
                "deactivated_at) VALUES (:id, :tid, :email, 'x', 'member', 'password', now())"
            ),
            {"id": uid, "tid": owner["tenant_id"], "email": email},
        )
        churned_ids.append(str(uid))
    await db_session.commit()
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 3  # unchanged

    # Confirm the GATED path correctly rejects a brand-new admission at this same cap —
    # proves the tenant genuinely IS at cap and the gate is live (control check).
    invite = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="gated-new@scimreactivatebypass.example"
    )
    gated_resp = await client.post(
        f"/invites/{invite['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert_problem(gated_resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")

    # Now reactivate BOTH churned members via SCIM PATCH active:true — the UNGATED path.
    scim_token = await create_scim_token(client, owner_token=owner["owner_token"])
    for uid in churned_ids:
        patch_resp = await client.patch(
            f"/scim/v2/Users/{uid}",
            json={
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [{"op": "replace", "path": "active", "value": True}],
            },
            headers=scim_bearer(scim_token),
        )
        assert patch_resp.status_code == 200, (
            f"SCIM reactivation of a churned member at an at-cap tenant returned "
            f"{patch_resp.status_code} — if this ever starts being rejected, the §7 "
            f"OBSERVE spec-delta this probe backs has been resolved: {patch_resp.text}"
        )

    final_count = await active_user_count(db_session, tenant_id=owner["tenant_id"])
    assert final_count == 5, (
        f"expected reactivation to bypass the cap entirely (3 -> 5, cap=3), got "
        f"{final_count} active — if this is now <= 3, the gap has been closed and this "
        f"probe (plus the TASK.md §1 disclosed gap) should be retired"
    )
    assert final_count > 3, (
        "CONFIRMED: SCIM PATCH active:true reactivation is NOT gated by "
        "assert_seat_available — a tenant can be pushed arbitrarily over its seat cap "
        "by reactivating churned members, with zero 403s. Disclosed in TASK.md §0/§1 as "
        "a deliberate scope boundary ('Ruled out, not silently'); this probe confirms "
        "the exposure is real and quantifies it (2 reactivations = 2 seats over cap, "
        "unbounded by the number of churned rows available)."
    )


# ---------------------------------------------------------------------------
# Probe 4 — seat_cap=0 boundary: confirmed DB-unreachable (CheckConstraint), not merely
# assumed from reading orm.py. A defense-in-depth note, not a defect: even if application
# code ever computed effective_seat_cap=0 some other way, `current_seats >= 0` is always
# true for a tenant with >= 1 active member (impossible to have 0 active members and grant
# admission), so 0 would behave as "always rejected" — never as "unlimited" — which is
# the SAFE failure direction (fails closed, not open).
# ---------------------------------------------------------------------------


async def test_tenant_seat_cap_zero_is_rejected_by_db_check_constraint(
    db_session: AsyncSession, client: httpx.AsyncClient
) -> None:
    owner = await signup_owner(client, email="owner@seatcapzero.example")
    with pytest.raises(IntegrityError, match="ck_tenants_seat_cap_positive"):
        await db_session.execute(
            text("UPDATE tenants SET seat_cap = 0 WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
        await db_session.commit()
    await db_session.rollback()


async def test_plan_seat_cap_zero_is_rejected_by_db_check_constraint(
    db_session: AsyncSession,
) -> None:
    from gateway.tenants.infrastructure.orm import PlanRow

    row = PlanRow(
        id=uuid.uuid4(),
        name="zero-seat-plan",
        display_name="Zero Seat Plan",
        seat_cap=0,
        budget_usd_monthly_default=None,
        rpm_limit_default=None,
        tpm_limit_default=None,
        model_allowlist=None,
        feature_flags=[],
    )
    db_session.add(row)
    with pytest.raises(IntegrityError, match="ck_plans_seat_cap_positive"):
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# Probe 5 — explicit tenant.seat_cap override precedence confirmed THROUGH the real
# assert_seat_available I/O path (not just the pure resolve_entitlements unit test the
# builder already has) — a tenant with a LOWER explicit override than its plan default,
# at the override's own boundary.
# ---------------------------------------------------------------------------


async def test_tenant_override_below_plan_default_enforced_through_assert_seat_available(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    from gateway.tenants.application.entitlements import assert_seat_available
    from gateway.tenants.domain.errors import SeatCapExceededError
    from .conftest import set_tenant_seat_cap

    owner = await signup_owner(client, email="owner@overrideprecedence.example")
    plan_id = await seed_plan(db_session, name="team-override-precedence", seat_cap=50)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    await set_tenant_seat_cap(db_session, tenant_id=owner["tenant_id"], seat_cap=2)
    await seed_extra_active_users(db_session, tenant_id=owner["tenant_id"], count=1)
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 2

    # The PLAN default (50) would allow this tenant far more headroom — only the
    # explicit tenant.seat_cap=2 override, enforced end-to-end through the real locked
    # I/O helper (not the pure precedence function alone), must govern here.
    with pytest.raises(SeatCapExceededError) as excinfo:
        await assert_seat_available(db_session, uuid.UUID(owner["tenant_id"]))
    assert excinfo.value.seat_cap == 2, (
        f"expected the tenant override (2) to win over the plan default (50) through the "
        f"real assert_seat_available I/O path, got seat_cap={excinfo.value.seat_cap}"
    )


# ---------------------------------------------------------------------------
# Probe 6 — the mirror image of Probe 3: deactivating a member FREES a seat for a real,
# GATED admission (not just SCIM's own unguarded reactivation path). `deactivate_user`
# exists in conftest.py but is never called by any of the builder's own 23 tests — the
# COUNT query's own `deactivated_at IS NULL` filter has never been proven, end-to-end,
# to actually let a NEW admission through once a seat is freed by deactivation.
# ---------------------------------------------------------------------------


async def test_deactivating_a_member_frees_a_seat_for_the_next_gated_admission(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await signup_owner(client, email="owner@deactivatefreesseat.example")
    plan_id = await seed_plan(db_session, name="starter-deactivate-frees", seat_cap=3)
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)
    filler_emails = await seed_extra_active_users(
        db_session, tenant_id=owner["tenant_id"], count=2, email_prefix="deactivatefrees"
    )
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 3

    # Control: at cap, a new admission is rejected.
    invite_at_cap = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="blocked@deactivatefreesseat.example"
    )
    blocked_resp = await client.post(
        f"/invites/{invite_at_cap['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert_problem(blocked_resp, 403, "ERR_PLAN_SEAT_CAP_EXCEEDED")

    # Deactivate one filler member — frees exactly one seat.
    await deactivate_user(db_session, email=filler_emails[0])
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 2

    # The SAME cap, now with headroom — the next admission through a REAL gated seam
    # (invite-accept) must succeed.
    invite_after_free = await _create_pending_invite(
        client, owner_token=owner["owner_token"], email="unblocked@deactivatefreesseat.example"
    )
    unblocked_resp = await client.post(
        f"/invites/{invite_after_free['token']}/accept",
        json={"password": "correct horse battery staple"},
    )
    assert unblocked_resp.status_code == 200, (
        f"expected deactivating a member to free a seat for the NEXT gated admission, got "
        f"{unblocked_resp.status_code}: {unblocked_resp.text}"
    )
    assert await active_user_count(db_session, tenant_id=owner["tenant_id"]) == 3
