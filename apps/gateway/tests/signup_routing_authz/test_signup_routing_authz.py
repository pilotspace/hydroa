"""Red->green suite for signup-and-routing-authz (S1 TASK.md §3/§4 — FROZEN).

One test per §2 SCENARIOS row (16 total). Asserts observable behavior only: HTTP status,
`code` field, DB row effects (tenants/users/routing_config/audit_events counts and content)
— never internal implementation details.

Two sub-surfaces:
  1. Public signup is now gated behind `Settings.public_signup_enabled` (default OFF; the
     top-level `tests/conftest.py` `settings` fixture flips it ON so every OTHER suite that
     bootstraps a tenant via signup keeps working — see that file's docstring). Tests 1/2/8
     explicitly build a flag-OFF app (`second_app_client`) to exercise the disabled path.
  2. GET/PUT /admin/routing now requires `require_superadmin` (previously any owner/admin via
     `Permission.ROUTING_MANAGE`) — routing-admin TASK.md decision superseded, see
     routing_admin_router.py's module docstring. Tests 9-16 exercise the real dependency
     against SUPERADMIN/OWNER/ADMIN/OPERATOR JWTs issued directly via
     `app.state.token_service.issue()` (no DB lookup needed for a role-only check).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._polling import poll_until

from gateway.tenants.domain.entities import Role

from .conftest import ADA, ADMIN_INVITES, ADMIN_ROUTING, LOGIN, SIGNUP, bearer, issue_token

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _row_counts(db: AsyncSession) -> tuple[int, int]:
    tenants = (await db.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    users = (await db.execute(text("SELECT count(*) FROM users"))).scalar_one()
    return int(tenants), int(users)


def _assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status} got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code={code}: {body}"
    assert body.get("status") == status
    return body


async def _routing_config_row(db: AsyncSession) -> dict[str, Any] | None:
    row = (await db.execute(text("SELECT config FROM routing_config WHERE id IS TRUE"))).fetchone()
    return dict(row[0]) if row is not None and row[0] is not None else None


async def _routing_update_audit_count(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                text("SELECT count(*) FROM audit_events WHERE action = 'routing.update'")
            )
        ).scalar_one()
    )


VALID_ROUTING_BODY = {"model_groups": {"chat": ["vendor/gpt"]}}
INVALID_ROUTING_BODY = {"model_groups": {"chat": []}}  # EMPTY_CANDIDATE_LIST


# ===========================================================================
# 1. Signup rejected invite-only (flag OFF)
# ===========================================================================


async def test_signup_rejected_invite_only(
    second_app_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    """Flag OFF, valid signup body -> 403 ERR_SIGNUP_INVITE_ONLY, zero rows created."""
    _app, off_client = second_app_client
    before = await _row_counts(db_session)

    resp = await off_client.post(SIGNUP, json=ADA)

    _assert_problem(resp, 403, "ERR_SIGNUP_INVITE_ONLY")
    after = await _row_counts(db_session)
    assert after == before, f"signup must create ZERO rows when disabled: {before} -> {after}"


# ===========================================================================
# 2. Invite-only gate checked BEFORE any body validation
# ===========================================================================


async def test_signup_invite_only_checked_before_validation(
    client: httpx.AsyncClient,
    second_app_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    """Flag OFF + an ALREADY-registered email + a weak password -> still 403
    ERR_SIGNUP_INVITE_ONLY, never 400 (weak password) or 409 (email taken) — the gate is
    checked first, before the use case (and its DB IO) ever runs."""
    # Register the email for real via the flag-ON primary client.
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201, signup.text
    before = await _row_counts(db_session)

    _app, off_client = second_app_client
    resp = await off_client.post(
        SIGNUP,
        json={"tenant_name": "Other Org", "email": ADA["email"], "password": "short"},
    )

    _assert_problem(resp, 403, "ERR_SIGNUP_INVITE_ONLY")
    after = await _row_counts(db_session)
    assert after == before, "no new rows from the rejected second attempt"


# ===========================================================================
# 3. Signup succeeds when enabled
# ===========================================================================


async def test_signup_succeeds_when_enabled(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Flag ON (the primary client's default), valid body -> 201 + one new tenant + one new
    user row."""
    before = await _row_counts(db_session)

    resp = await client.post(SIGNUP, json=ADA)

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["tenant_id"] and body["user_id"]
    after = await _row_counts(db_session)
    assert after == (before[0] + 1, before[1] + 1)


# ===========================================================================
# 4. Weak password still rejected when enabled
# ===========================================================================


async def test_signup_weak_password_still_rejected_when_enabled(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    before = await _row_counts(db_session)

    resp = await client.post(
        SIGNUP, json={"tenant_name": "Acme", "email": "weak@acme.io", "password": "short"}
    )

    _assert_problem(resp, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert await _row_counts(db_session) == before


# ===========================================================================
# 5. Duplicate email still rejected when enabled (ERR_TENANT_EMAIL_TAKEN — the REAL code)
# ===========================================================================


async def test_signup_duplicate_email_still_rejected_when_enabled(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.post(SIGNUP, json=ADA)).status_code == 201
    before = await _row_counts(db_session)

    again = {**ADA, "tenant_name": "Other Org"}
    resp = await client.post(SIGNUP, json=again)

    # NOTE: the scenario prose loosely says ERR_AUTH_EMAIL_TAKEN; the ACTUAL, unchanged
    # current value is ERR_TENANT_EMAIL_TAKEN (error_catalog.py AUTH_EMAIL_TAKEN spec).
    _assert_problem(resp, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await _row_counts(db_session) == before


# ===========================================================================
# 6. Invite issuance unaffected by the signup flag
# ===========================================================================


async def test_invite_issuance_unaffected_by_signup_flag(
    client: httpx.AsyncClient,
    second_app_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """An existing tenant/owner (bootstrapped while the flag was ON) can still issue invites
    through an app where the signup flag is OFF — invite issuance has zero coupling to
    `public_signup_enabled`."""
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201, signup.text
    login = await client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]

    _app, off_client = second_app_client
    resp = await off_client.post(
        ADMIN_INVITES,
        json={"email": "newmember@acme.io", "role": "member"},
        headers=bearer(owner_token),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "newmember@acme.io"
    assert body["status"] == "pending"


# ===========================================================================
# 7. Invite acceptance unaffected by the signup flag
# ===========================================================================


async def test_invite_acceptance_unaffected_by_signup_flag(
    client: httpx.AsyncClient,
    second_app_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    """A pending invite (issued while the flag was ON) can still be accepted through an app
    where the signup flag is OFF — invite acceptance has zero coupling to
    `public_signup_enabled`."""
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201, signup.text
    login = await client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})
    owner_token = login.json()["access_token"]

    invite = await client.post(
        ADMIN_INVITES,
        json={"email": "acceptme@acme.io", "role": "member"},
        headers=bearer(owner_token),
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]

    _app, off_client = second_app_client
    resp = await off_client.post(
        f"/invites/{token}/accept", json={"password": "accept horse battery staple"}
    )

    assert resp.status_code == 200, resp.text
    users = (
        await db_session.execute(
            text("SELECT count(*) FROM users WHERE email = 'acceptme@acme.io'")
        )
    ).scalar_one()
    assert users == 1


# ===========================================================================
# 8. Bootstrap flip ON -> OFF: first owner survives the flip
# ===========================================================================


async def test_bootstrap_flip_on_then_off(
    client: httpx.AsyncClient,
    second_app_client: tuple[Any, httpx.AsyncClient],
    db_session: AsyncSession,
) -> None:
    """Flag-ON app bootstraps an owner (201). A flag-OFF app on the SAME database then
    refuses a second signup (403) — but the FIRST owner can still log in (200) through the
    flag-OFF app too, proving the flip only closes NEW signups, never existing identities."""
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201, signup.text
    before = await _row_counts(db_session)

    _app, off_client = second_app_client
    second_attempt = await off_client.post(
        SIGNUP,
        json={
            "tenant_name": "Second Org",
            "email": "second@acme.io",
            "password": "correct horse battery",
        },
    )
    _assert_problem(second_attempt, 403, "ERR_SIGNUP_INVITE_ONLY")
    assert await _row_counts(db_session) == before, "the refused second signup created no rows"

    login = await off_client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})
    assert login.status_code == 200, login.text
    assert login.json()["token_type"] == "bearer"


# ===========================================================================
# 9. SUPERADMIN reads routing unchanged
# ===========================================================================


async def test_superadmin_reads_routing_unchanged(app: Any, client: httpx.AsyncClient) -> None:
    token = issue_token(app, role=Role.SUPERADMIN)

    resp = await client.get(ADMIN_ROUTING, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected_top = {
        "retry_policy",
        "cooldown",
        "model_groups",
        "candidates",
        "routing_strategy",
        "deployments",
    }
    assert set(body.keys()) == expected_top, f"top-level shape changed: {body.keys()}"


# ===========================================================================
# 10. SUPERADMIN writes routing unchanged (persisted + audited)
# ===========================================================================


async def test_superadmin_writes_routing_unchanged(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = issue_token(app, role=Role.SUPERADMIN)

    resp = await client.put(ADMIN_ROUTING, json=VALID_ROUTING_BODY, headers=bearer(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["model_groups"] == VALID_ROUTING_BODY["model_groups"]

    persisted = await _routing_config_row(db_session)
    assert persisted is not None, "PUT must persist a routing_config row"
    assert persisted["model_groups"] == VALID_ROUTING_BODY["model_groups"]

    # MIXED wait: the audit event must APPEAR (positive — fire-and-forget, so poll)
    # and must appear EXACTLY ONCE (negative — a duplicate audit row for one write
    # would be a real defect, and polling alone would never give it time to show).
    await poll_until(lambda: _routing_update_audit_count(db_session), lambda n: n >= 1)
    # NEGATIVE WAIT: the exactly-once half.
    await asyncio.sleep(0.05)
    assert await _routing_update_audit_count(db_session) == 1, (
        "routing.update audit event must fire on a successful write"
    )


# ===========================================================================
# 11. Tenant OWNER cannot read routing
# ===========================================================================


async def test_tenant_owner_cannot_read_routing(app: Any, client: httpx.AsyncClient) -> None:
    token = issue_token(app, role=Role.OWNER)

    resp = await client.get(ADMIN_ROUTING, headers=bearer(token))

    _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# 12. Tenant OWNER cannot write routing (no persist, no audit)
# ===========================================================================


async def test_tenant_owner_cannot_write_routing(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = issue_token(app, role=Role.OWNER)
    before = await _routing_config_row(db_session)

    resp = await client.put(ADMIN_ROUTING, json=VALID_ROUTING_BODY, headers=bearer(token))

    _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    after = await _routing_config_row(db_session)
    assert after == before, f"routing_config row must be UNCHANGED on a 403: {before} -> {after}"

    # NEGATIVE WAIT: proves a 403 writes NO audit event. Only elapsed time can show
    # absence — polling a zero count returns on iteration one and asserts nothing.
    await asyncio.sleep(0.05)
    assert await _routing_update_audit_count(db_session) == 0, (
        "no routing.update audit event may fire on a rejected write"
    )


# ===========================================================================
# 13. ADMIN and OPERATOR cannot access routing (GET+PUT x 2 roles)
# ===========================================================================


@pytest.mark.parametrize("role", [Role.ADMIN, Role.OPERATOR])
async def test_admin_and_operator_cannot_access_routing(
    app: Any, client: httpx.AsyncClient, role: Role
) -> None:
    token = issue_token(app, role=role)

    get_resp = await client.get(ADMIN_ROUTING, headers=bearer(token))
    put_resp = await client.put(ADMIN_ROUTING, json=VALID_ROUTING_BODY, headers=bearer(token))

    _assert_problem(get_resp, 403, "ERR_AUTH_FORBIDDEN")
    _assert_problem(put_resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# 14. Missing bearer token still 401 (GET + PUT)
# ===========================================================================


async def test_routing_missing_token_still_401(client: httpx.AsyncClient) -> None:
    get_resp = await client.get(ADMIN_ROUTING)
    put_resp = await client.put(ADMIN_ROUTING, json=VALID_ROUTING_BODY)

    # authz._resolve_identity raises AUTH_TOKEN_MISSING for a missing/malformed bearer
    # header, which shares the SAME wire code as AUTH_TOKEN_INVALID: ERR_AUTH_INVALID_TOKEN.
    _assert_problem(get_resp, 401, "ERR_AUTH_INVALID_TOKEN")
    _assert_problem(put_resp, 401, "ERR_AUTH_INVALID_TOKEN")


# ===========================================================================
# 15. SUPERADMIN invalid routing body still 422 (nothing persisted)
# ===========================================================================


async def test_superadmin_invalid_routing_body_still_422(
    app: Any, client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = issue_token(app, role=Role.SUPERADMIN)
    before = await _routing_config_row(db_session)

    resp = await client.put(ADMIN_ROUTING, json=INVALID_ROUTING_BODY, headers=bearer(token))

    assert resp.status_code == 422, f"expected 422 got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == "ERR_ROUTING_CONFIG_INVALID", body
    assert body.get("detail") == "EMPTY_CANDIDATE_LIST", body

    after = await _routing_config_row(db_session)
    assert after == before, "an invalid PUT body must persist nothing"


# ===========================================================================
# 16. End-to-end chain: a freshly signed-up OWNER cannot reach routing admin
# ===========================================================================


async def test_s1_chain_closed_end_to_end(client: httpx.AsyncClient) -> None:
    """Flag ON: signup a brand-new tenant, log in as its OWNER, then PUT /admin/routing with
    that OWNER's real JWT -> 403 ERR_AUTH_FORBIDDEN. Proves the two S1 halves compose: a
    public signup can never itself yield routing-admin access."""
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201, signup.text
    login = await client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]

    resp = await client.put(ADMIN_ROUTING, json=VALID_ROUTING_BODY, headers=bearer(owner_token))

    _assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
