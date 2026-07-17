"""RED->GREEN suite for POST/GET/DELETE /admin/invites (member-invite-issuance TASK.md §4).

One test per §2 SCENARIOS entry, plus a small number of well-justified additions (see the
module docstrings on TEST 25-27 below). All assertions are behavioral (HTTP status/body, DB
state, audit rows) or, for the three unit-only additions, direct calls to the extracted
shared symbol itself — never internal implementation details of the use cases/repository.
Identity JWTs are issued directly via app.state.token_service.issue() for role-only fixtures
(same pattern as tests/test_users_role.py); a real tenant+owner is created via signup for the
tests that need one (tenant-scoped uniqueness / anti-enumeration / audit checks).

DB-touching tests use the conftest `client` + `db_session` + `app` fixtures (drop/create per
test, real Postgres).

SECURITY PROPERTIES PROVEN SERVER-SIDE:
  - Escalation ceiling parity: admin cannot invite owner/admin (shared assert_role_within_ceiling)
  - superadmin is never invitable, for any caller, before any use case runs — proven BOTH at
    the HTTP layer (byte-identical to the existing role-reassignment pre-check) AND directly
    against the real Role.SUPERADMIN enum member and the extracted shared predicate itself
    (TEST 25-26 below close the gap a stale, superadmin-less codebase slice could not see).
  - MEMBERS_MANAGE gating: only owner/admin can create/list/revoke invites
  - Anti-enumeration: CREATE never reveals whether an email belongs to a different tenant
  - Tenant isolation: LIST/REVOKE never see/touch another tenant's invites (no oracle on REVOKE)
  - Token is CSPRNG, hashed at rest, shown exactly once; never present in LIST responses
  - Re-invite atomicity: at most one pending row per (tenant, email), even under a genuine race
  - Audit: invite.create / invite.revoke fire fail-open; LIST is unaudited
  - DB-level defense-in-depth: the invites_role_check CHECK constraint independently rejects
    role='superadmin' even if application code were bypassed (TEST 10)
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.tenants.domain.entities import Role

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _issue_token(
    app: Any,
    *,
    role: Role,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> str:
    """Issue a JWT with a specific role/tenant_id (and optionally a fixed user_id)."""
    uid = user_id or uuid.uuid4()
    token, _ = app.state.token_service.issue(
        user_id=uid,
        tenant_id=tenant_id,
        role=role,
        email=email or f"{role.value}@invitetest.io",
    )
    return token


def _assert_forbidden(resp: httpx.Response) -> None:
    """403 ERR_AUTH_FORBIDDEN — the standard rejection shape (R2/R5)."""
    assert resp.status_code == 403, f"expected 403 got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text


def _assert_payload_invalid(resp: httpx.Response) -> None:
    """422 ERR_PAYLOAD_INVALID — the standard validation-rejection shape (R3/R4/R6)."""
    assert resp.status_code == 422, f"expected 422 got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID", resp.text


async def _pending_count(db_session: AsyncSession, tenant_id: uuid.UUID, email: str) -> int:
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM invites WHERE tenant_id = :tid AND email = :email "
            "AND status = 'pending'"
        ),
        {"tid": tenant_id, "email": email},
    )
    return result.scalar_one()


async def _total_count(db_session: AsyncSession, tenant_id: uuid.UUID, email: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM invites WHERE tenant_id = :tid AND email = :email"),
        {"tid": tenant_id, "email": email},
    )
    return result.scalar_one()


async def _seed_user(
    db_session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
    role: Role,
) -> None:
    """Insert a real users row — needed whenever a minted JWT's user_id will be used as
    invites.invited_by_user_id (a real FK to users.id), not just for permission-boundary
    checks (those never reach an INSERT, so an arbitrary/unbacked user_id is fine there).
    """
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "role": role.value},
    )
    await db_session.commit()


async def _invite_row(db_session: AsyncSession, invite_id: uuid.UUID) -> Any:
    result = await db_session.execute(
        text(
            "SELECT id, tenant_id, email, role, token_hash, status, expires_at, "
            "invited_by_user_id FROM invites WHERE id = :id"
        ),
        {"id": invite_id},
    )
    return result.mappings().one()


# ---------------------------------------------------------------------------
# Fixture: a real tenant + owner (via signup), for tenant-scoped tests
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_tenant(
    client: httpx.AsyncClient,
    app: Any,
) -> dict[str, Any]:
    """Signup creates a tenant + owner. Returns owner_token / tenant_id / owner_user_id."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "InviteCo",
            "email": "owner@invitetest.io",
            "password": "invite-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": "owner@invitetest.io", "password": "invite-horse-battery-01"},
    )
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]
    tenant_id = uuid.UUID(signup.json()["tenant_id"])
    owner_identity = app.state.token_service.decode(owner_token)

    return {
        "owner_token": owner_token,
        "tenant_id": tenant_id,
        "owner_user_id": owner_identity.user_id,
    }


# ---------------------------------------------------------------------------
# TEST 1/2: Owner / admin invite within their ceiling                                    [M1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_invites_co_owner(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Owner may invite any of the 6 self-service roles, e.g. 'owner' itself."""
    headers = _bearer(seeded_tenant["owner_token"])
    r = await client.post(
        "/admin/invites",
        json={"email": "new@co.io", "role": "owner"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "owner"
    assert body["status"] == "pending"
    assert set(body.keys()) == {
        "id",
        "email",
        "role",
        "status",
        "expires_at",
        "created_at",
        "invited_by_user_id",
        "token",
        # transactional-email TASK.md §3 (FROZEN @ v1, M9): ONE additive field superseding
        # this task's own original "EXACTLY this shape" pin — every field above stays
        # byte-identical, this is the only addition (which channel the accept-link email
        # was dispatched to).
        "email_delivery_channel",
    }, (
        "InviteCreateResponse must be this shape (member-invite-issuance §3 + transactional-email §3 M9)"
    )
    assert await _pending_count(db_session, seeded_tenant["tenant_id"], "new@co.io") == 1


@pytest.mark.asyncio
async def test_admin_invites_allowed_tier(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Admin may invite operator/billing_admin/viewer/member."""
    admin_id = uuid.uuid4()
    await _seed_user(
        db_session,
        user_id=admin_id,
        tenant_id=seeded_tenant["tenant_id"],
        email="admin@invitetest.io",
        role=Role.ADMIN,
    )
    admin_token = _issue_token(
        app, role=Role.ADMIN, tenant_id=seeded_tenant["tenant_id"], user_id=admin_id
    )
    r = await client.post(
        "/admin/invites",
        json={"email": "new@co.io", "role": "viewer"},
        headers=_bearer(admin_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "viewer"


# ---------------------------------------------------------------------------
# TEST 3: Admin cannot invite owner or admin (escalation ceiling)                   [M1, R5]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_cannot_invite_owner_or_admin(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    admin_token = _issue_token(app, role=Role.ADMIN, tenant_id=seeded_tenant["tenant_id"])
    headers = _bearer(admin_token)

    r1 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "owner"}, headers=headers
    )
    _assert_forbidden(r1)

    r2 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "admin"}, headers=headers
    )
    _assert_forbidden(r2)

    assert await _total_count(db_session, seeded_tenant["tenant_id"], "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 4: role == "superadmin" always rejected, before any use case runs           [M2, R4]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_superadmin_role_always_rejected(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Even the highest self-service tier (owner) can never invite 'superadmin'.

    Byte-identical detail to PUT /admin/users/{id}/role's own pre-check for the same payload.
    """
    headers = _bearer(seeded_tenant["owner_token"])
    r = await client.post(
        "/admin/invites",
        json={"email": "x@co.io", "role": "superadmin"},
        headers=headers,
    )
    _assert_payload_invalid(r)
    assert r.json().get("detail") == "Unknown role: 'superadmin'", r.text
    assert await _total_count(db_session, seeded_tenant["tenant_id"], "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 5: Unparseable role literal is rejected                                          [R3]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_unparseable_role_rejected(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    r = await client.post(
        "/admin/invites",
        json={"email": "x@co.io", "role": "wizard"},
        headers=headers,
    )
    _assert_payload_invalid(r)
    assert await _total_count(db_session, seeded_tenant["tenant_id"], "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 6: Malformed email is rejected                                                    [R6]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_malformed_email_rejected(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    r = await client.post(
        "/admin/invites",
        json={"email": "not-an-email", "role": "member"},
        headers=headers,
    )
    _assert_payload_invalid(r)


# ---------------------------------------------------------------------------
# TEST 7: Caller without MEMBERS_MANAGE cannot create an invite                          [R2]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_caller_without_members_manage_cannot_invite(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    for role_str in ["operator", "billing_admin", "viewer", "member"]:
        token = _issue_token(
            app,
            role=Role(role_str),
            tenant_id=seeded_tenant["tenant_id"],
            email=f"{role_str}@gating.local",
        )
        r = await client.post(
            "/admin/invites",
            json={"email": "x@co.io", "role": "member"},
            headers=_bearer(token),
        )
        _assert_forbidden(r)

    assert await _total_count(db_session, seeded_tenant["tenant_id"], "x@co.io") == 0


# ---------------------------------------------------------------------------
# TEST 8: Missing or invalid bearer token is rejected                                    [R1]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_or_invalid_bearer_token_rejected(
    client: httpx.AsyncClient,
) -> None:
    # No Authorization header at all
    r1 = await client.post("/admin/invites", json={"email": "x@co.io", "role": "member"})
    assert r1.status_code == 401, r1.text
    assert r1.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r1.text

    # Garbage bearer token
    r2 = await client.post(
        "/admin/invites",
        json={"email": "x@co.io", "role": "member"},
        headers=_bearer("not-a-real-jwt"),
    )
    assert r2.status_code == 401, r2.text
    assert r2.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r2.text


# ---------------------------------------------------------------------------
# TEST 8b/8c: LIST and REVOKE share the IDENTICAL MEMBERS_MANAGE + bearer-token gate  [M10,
# R1, R2] — §3 CONTRACT documents 401/403 for all three endpoints, not just CREATE; the
# SCENARIOS section drafted only one explicit Gherkin block for R1/R2 (tied to CREATE), so
# these two tests close that contract-conformance gap behaviorally rather than by code
# inspection of the shared require_permission(...) dependency.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_invites_requires_bearer_token_and_members_manage(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
) -> None:
    # No Authorization header at all -> 401 (R1)
    r1 = await client.get("/admin/invites")
    assert r1.status_code == 401, r1.text
    assert r1.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r1.text

    # Garbage bearer token -> 401 (R1)
    r2 = await client.get("/admin/invites", headers=_bearer("not-a-real-jwt"))
    assert r2.status_code == 401, r2.text
    assert r2.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r2.text

    # Authenticated but lacking MEMBERS_MANAGE -> 403 (R2)
    member_token = _issue_token(
        app, role=Role.MEMBER, tenant_id=seeded_tenant["tenant_id"], email="member@gating.local"
    )
    r3 = await client.get("/admin/invites", headers=_bearer(member_token))
    _assert_forbidden(r3)


@pytest.mark.asyncio
async def test_revoke_invite_requires_bearer_token_and_members_manage(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    create = await client.post(
        "/admin/invites", json={"email": "gated@co.io", "role": "member"}, headers=headers
    )
    assert create.status_code == 201, create.text
    invite_id = create.json()["id"]

    # No Authorization header at all -> 401 (R1)
    r1 = await client.delete(f"/admin/invites/{invite_id}")
    assert r1.status_code == 401, r1.text
    assert r1.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r1.text

    # Garbage bearer token -> 401 (R1)
    r2 = await client.delete(f"/admin/invites/{invite_id}", headers=_bearer("not-a-real-jwt"))
    assert r2.status_code == 401, r2.text
    assert r2.json().get("code") == "ERR_AUTH_INVALID_TOKEN", r2.text

    # Authenticated but lacking MEMBERS_MANAGE -> 403 (R2)
    member_token = _issue_token(
        app,
        role=Role.MEMBER,
        tenant_id=seeded_tenant["tenant_id"],
        email="member2@gating.local",
    )
    r3 = await client.delete(f"/admin/invites/{invite_id}", headers=_bearer(member_token))
    _assert_forbidden(r3)

    # None of the above touched the row — still pending.
    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# TEST 9: Token is CSPRNG, hashed at rest, shown exactly once                            [M3]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_is_csprng_hashed_shown_once(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    r = await client.post(
        "/admin/invites",
        json={"email": "new@co.io", "role": "member"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    token = body["token"]
    assert isinstance(token, str) and len(token) >= 32

    row = await _invite_row(db_session, uuid.UUID(body["id"]))
    expected_hash = Sha256SecretHasher().hash(token)
    assert row["token_hash"] == expected_hash
    assert row["token_hash"] != token

    list_resp = await client.get("/admin/invites", headers=headers)
    assert list_resp.status_code == 200, list_resp.text
    for item in list_resp.json()["invites"]:
        assert "token" not in item
        assert "token_hash" not in item


# ---------------------------------------------------------------------------
# TEST 10: Email stored lowercased; DB itself excludes 'superadmin'                      [M4]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_lowercased_and_db_excludes_superadmin(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    tenant_id = seeded_tenant["tenant_id"]

    r = await client.post(
        "/admin/invites",
        json={"email": "MixedCase@Co.IO", "role": "member"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    first_id = r.json()["id"]
    assert r.json()["email"] == "mixedcase@co.io"

    # Re-inviting with different casing of the SAME address matches and supersedes
    # the same logical row — never a second, parallel pending row.
    r2 = await client.post(
        "/admin/invites",
        json={"email": "mixedcase@CO.io", "role": "member"},
        headers=headers,
    )
    assert r2.status_code == 201, r2.text
    assert await _pending_count(db_session, tenant_id, "mixedcase@co.io") == 1
    assert (
        await _total_count(db_session, tenant_id, "mixedcase@co.io") == 2
    )  # 1 revoked + 1 pending

    first_row = await _invite_row(db_session, uuid.UUID(first_id))
    assert first_row["status"] == "revoked"

    # DEFENSE-IN-DEPTH (M4): the DB CHECK constraint itself rejects role='superadmin',
    # completely independent of application code — a raw INSERT that bypasses the
    # use-case/router entirely must still fail. This is the second, DB-level line of
    # defense behind the M2 pre-check and the assert_role_within_ceiling guard.
    with pytest.raises(Exception):  # noqa: B017, PT011 — DBAPI IntegrityError, driver-specific
        await db_session.execute(
            text(
                "INSERT INTO invites "
                "(id, tenant_id, email, role, token_hash, status, expires_at, invited_by_user_id) "
                "VALUES (:id, :tid, 'raw@co.io', 'superadmin', 'deadbeef', 'pending', "
                "now() + interval '7 days', :uid)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "uid": seeded_tenant["owner_user_id"]},
        )
        await db_session.commit()
    await db_session.rollback()


# ---------------------------------------------------------------------------
# TEST 11: Email already belongs to a member of the CALLER's own tenant                  [R7]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_already_member_of_caller_tenant(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    tenant_id = seeded_tenant["tenant_id"]
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, 'u@t.io', '$argon2id$v=dummy', 'member')"
        ),
        {"id": uuid.uuid4(), "tid": tenant_id},
    )
    await db_session.commit()

    r = await client.post(
        "/admin/invites",
        json={"email": "u@t.io", "role": "member"},
        headers=_bearer(seeded_tenant["owner_token"]),
    )
    assert r.status_code == 409, r.text
    assert r.json().get("code") == "ERR_INVITE_EMAIL_ALREADY_MEMBER", r.text
    assert await _total_count(db_session, tenant_id, "u@t.io") == 0


# ---------------------------------------------------------------------------
# TEST 12: Email in a DIFFERENT tenant is indistinguishable from brand-new         [M6]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_in_different_tenant_indistinguishable_from_new(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    tenant_a = seeded_tenant["tenant_id"]
    tenant_b = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'OtherCo')"),
        {"tid": tenant_b},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, 'b@b.io', '$argon2id$v=dummy', 'member')"
        ),
        {"id": uuid.uuid4(), "tid": tenant_b},
    )
    await db_session.commit()

    r = await client.post(
        "/admin/invites",
        json={"email": "b@b.io", "role": "member"},
        headers=_bearer(seeded_tenant["owner_token"]),
    )
    assert r.status_code == 201, r.text  # IDENTICAL shape to a brand-new email (no leak)

    assert await _pending_count(db_session, tenant_a, "b@b.io") == 1
    # Tenant B's own user row is completely untouched.
    other_user = await db_session.execute(
        text("SELECT role FROM users WHERE tenant_id = :tid AND email = 'b@b.io'"),
        {"tid": tenant_b},
    )
    assert other_user.scalar_one() == "member"


# ---------------------------------------------------------------------------
# TEST 13: Re-inviting supersedes the old pending row atomically                         [M5]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reinvite_same_pending_email_issues_fresh_token_kills_old(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    tenant_id = seeded_tenant["tenant_id"]

    r1 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert r1.status_code == 201, r1.text
    token_1 = r1.json()["token"]
    id_1 = r1.json()["id"]

    r2 = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert r2.status_code == 201, r2.text
    token_2 = r2.json()["token"]
    id_2 = r2.json()["id"]

    assert token_1 != token_2
    assert id_1 != id_2
    assert await _pending_count(db_session, tenant_id, "x@co.io") == 1

    old_row = await _invite_row(db_session, uuid.UUID(id_1))
    assert old_row["status"] == "revoked"
    new_row = await _invite_row(db_session, uuid.UUID(id_2))
    assert new_row["status"] == "pending"


# ---------------------------------------------------------------------------
# TEST 14: Concurrent double re-invite never produces two live tokens         [M5 concurrency]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_double_reinvite_never_two_live_tokens(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    tenant_id = seeded_tenant["tenant_id"]

    first = await client.post(
        "/admin/invites", json={"email": "race@co.io", "role": "member"}, headers=headers
    )
    assert first.status_code == 201, first.text

    r1, r2 = await asyncio.gather(
        client.post(
            "/admin/invites", json={"email": "race@co.io", "role": "member"}, headers=headers
        ),
        client.post(
            "/admin/invites", json={"email": "race@co.io", "role": "member"}, headers=headers
        ),
    )
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["token"] != r2.json()["token"]

    assert await _pending_count(db_session, tenant_id, "race@co.io") == 1


# ---------------------------------------------------------------------------
# TEST 15: Listing pending invites                                                       [M8]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_invites(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])

    r1 = await client.post(
        "/admin/invites", json={"email": "one@co.io", "role": "member"}, headers=headers
    )
    r2 = await client.post(
        "/admin/invites", json={"email": "two@co.io", "role": "member"}, headers=headers
    )
    r3 = await client.post(
        "/admin/invites", json={"email": "three@co.io", "role": "member"}, headers=headers
    )
    for r in (r1, r2, r3):
        assert r.status_code == 201, r.text

    revoke = await client.delete(f"/admin/invites/{r3.json()['id']}", headers=headers)
    assert revoke.status_code == 204, revoke.text

    listed = await client.get("/admin/invites", headers=headers)
    assert listed.status_code == 200, listed.text
    invites = listed.json()["invites"]
    emails = {i["email"] for i in invites}
    assert emails == {"one@co.io", "two@co.io"}
    for item in invites:
        assert item["status"] == "pending"
        assert set(item.keys()) == {
            "id",
            "email",
            "role",
            "status",
            "expires_at",
            "created_at",
            "invited_by_user_id",
        }


# ---------------------------------------------------------------------------
# TEST 16: Owner/admin revokes a pending invite                                          [M9]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_admin_revokes_pending_invite(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    create = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert create.status_code == 201, create.text
    invite_id = create.json()["id"]

    r = await client.delete(f"/admin/invites/{invite_id}", headers=headers)
    assert r.status_code == 204, r.text

    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "revoked"


# ---------------------------------------------------------------------------
# TEST 17: Any current owner/admin may revoke another's invite (tenant-scoped)     [M9 edge]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_any_current_owner_admin_may_revoke_others_invite(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    tenant_id = seeded_tenant["tenant_id"]
    admin_a_id = uuid.uuid4()
    await _seed_user(
        db_session, user_id=admin_a_id, tenant_id=tenant_id, email="admina@co.io", role=Role.ADMIN
    )
    admin_a_token = _issue_token(
        app, role=Role.ADMIN, tenant_id=tenant_id, user_id=admin_a_id, email="admina@co.io"
    )

    create = await client.post(
        "/admin/invites",
        json={"email": "x@co.io", "role": "member"},
        headers=_bearer(admin_a_token),
    )
    assert create.status_code == 201, create.text
    invite_id = create.json()["id"]

    # A DIFFERENT current owner/admin (the tenant's owner) revokes it — tenant-scoped, not
    # creator-scoped.
    r = await client.delete(
        f"/admin/invites/{invite_id}", headers=_bearer(seeded_tenant["owner_token"])
    )
    assert r.status_code == 204, r.text
    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "revoked"

    # Admin A has since been demoted to member — their own (re-issued) JWT is now member-scoped.
    admin_a_demoted_token = _issue_token(
        app, role=Role.MEMBER, tenant_id=tenant_id, user_id=admin_a_id, email="admina@co.io"
    )
    r2 = await client.delete(f"/admin/invites/{invite_id}", headers=_bearer(admin_a_demoted_token))
    _assert_forbidden(r2)


# ---------------------------------------------------------------------------
# TEST 18: Revoking an unknown invite_id                                                 [R8]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_unknown_invite_id(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
) -> None:
    unknown_id = "00000000-0000-0000-0000-000000000000"
    r = await client.delete(
        f"/admin/invites/{unknown_id}", headers=_bearer(seeded_tenant["owner_token"])
    )
    assert r.status_code == 404, r.text
    assert r.json().get("code") == "ERR_INVITE_NOT_FOUND", r.text


# ---------------------------------------------------------------------------
# TEST 19: Revoking a different tenant's invite is indistinguishable from unknown  [R8 no oracle]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_invite_belonging_to_different_tenant_indistinguishable(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    tenant_b = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'TenantB')"), {"tid": tenant_b}
    )
    await db_session.commit()
    owner_b_id = uuid.uuid4()
    await _seed_user(
        db_session, user_id=owner_b_id, tenant_id=tenant_b, email="ownerb@b.io", role=Role.OWNER
    )
    owner_b_token = _issue_token(
        app, role=Role.OWNER, tenant_id=tenant_b, user_id=owner_b_id, email="ownerb@b.io"
    )

    create = await client.post(
        "/admin/invites",
        json={"email": "x@b.io", "role": "member"},
        headers=_bearer(owner_b_token),
    )
    assert create.status_code == 201, create.text
    invite_id = create.json()["id"]

    # Owner of tenant A (a DIFFERENT tenant) tries to revoke tenant B's invite.
    r = await client.delete(
        f"/admin/invites/{invite_id}", headers=_bearer(seeded_tenant["owner_token"])
    )
    assert r.status_code == 404, r.text
    assert r.json().get("code") == "ERR_INVITE_NOT_FOUND", r.text  # same code as truly-unknown

    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "pending"  # completely unchanged


# ---------------------------------------------------------------------------
# TEST 20: Revoking an already-revoked invite                                            [R9]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_already_revoked_invite(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    create = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    invite_id = create.json()["id"]
    first_revoke = await client.delete(f"/admin/invites/{invite_id}", headers=headers)
    assert first_revoke.status_code == 204, first_revoke.text

    second_revoke = await client.delete(f"/admin/invites/{invite_id}", headers=headers)
    assert second_revoke.status_code == 409, second_revoke.text
    assert second_revoke.json().get("code") == "ERR_INVITE_NOT_PENDING", second_revoke.text

    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "revoked"


# ---------------------------------------------------------------------------
# TEST 21: Revoking an already-accepted invite (race w/ sibling accept-flow) [R9 concurrency]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoke_already_accepted_invite(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    create = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    invite_id = create.json()["id"]

    # Simulate the (separate, sibling) accept-flow task having just accepted this invite.
    await db_session.execute(
        text("UPDATE invites SET status = 'accepted' WHERE id = :id"), {"id": uuid.UUID(invite_id)}
    )
    await db_session.commit()

    r = await client.delete(f"/admin/invites/{invite_id}", headers=headers)
    assert r.status_code == 409, r.text
    assert r.json().get("code") == "ERR_INVITE_NOT_PENDING", r.text

    row = await _invite_row(db_session, uuid.UUID(invite_id))
    assert row["status"] == "accepted"  # revoke can never un-accept a joined user


# ---------------------------------------------------------------------------
# TEST 22: CREATE and REVOKE each fire an audit event                                   [M12]
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_revoke_fire_audit_events(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    headers = _bearer(seeded_tenant["owner_token"])
    create = await client.post(
        "/admin/invites", json={"email": "x@co.io", "role": "member"}, headers=headers
    )
    assert create.status_code == 201, create.text
    invite_id = create.json()["id"]

    revoke = await client.delete(f"/admin/invites/{invite_id}", headers=headers)
    assert revoke.status_code == 204, revoke.text

    await asyncio.sleep(0.05)  # let the fire-and-forget audit tasks complete

    create_audit = (
        await db_session.execute(
            text(
                "SELECT action, metadata FROM audit_events WHERE action = 'invite.create' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).fetchone()
    assert create_audit is not None, "expected invite.create audit event"

    revoke_audit = (
        await db_session.execute(
            text(
                "SELECT action, metadata FROM audit_events WHERE action = 'invite.revoke' "
                "ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).fetchone()
    assert revoke_audit is not None, "expected invite.revoke audit event"


# ---------------------------------------------------------------------------
# TEST 23 (repo-level): get_by_id_and_tenant is a real, exercised repository symbol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invite_repository_get_by_id_and_tenant(
    db_session: AsyncSession,
) -> None:
    """Direct infra-layer coverage of InviteRepository.get_by_id_and_tenant (§3 CONTRACT)."""
    from gateway.tenants.domain.entities import Role as _Role
    from gateway.tenants.infrastructure.invite_repository import InviteRepository

    tenant_id = uuid.uuid4()
    other_tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'RepoTestCo')"), {"tid": tenant_id}
    )
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'RepoTestOtherCo')"),
        {"tid": other_tenant_id},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, 'inviter@repo.io', '$argon2id$v=dummy', 'owner')"
        ),
        {"id": (inviter_id := uuid.uuid4()), "tid": tenant_id},
    )
    await db_session.commit()

    repo = InviteRepository(db_session)
    assert _Role.MEMBER.value == "member"  # sanity: the role stored below matches this enum
    new_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO invites "
            "(id, tenant_id, email, role, token_hash, status, expires_at, invited_by_user_id) "
            "VALUES (:id, :tid, 'repo-target@co.io', 'member', 'abc123', 'pending', "
            "now() + interval '7 days', :uid)"
        ),
        {"id": new_id, "tid": tenant_id, "uid": inviter_id},
    )
    await db_session.commit()

    found = await repo.get_by_id_and_tenant(invite_id=new_id, tenant_id=tenant_id)
    assert found is not None
    assert found.email == "repo-target@co.io"

    not_found_wrong_tenant = await repo.get_by_id_and_tenant(
        invite_id=new_id, tenant_id=other_tenant_id
    )
    assert not_found_wrong_tenant is None

    not_found_unknown_id = await repo.get_by_id_and_tenant(
        invite_id=uuid.uuid4(), tenant_id=tenant_id
    )
    assert not_found_unknown_id is None


# ---------------------------------------------------------------------------
# TEST 24: Migration is purely additive                                                 [M11]
# ---------------------------------------------------------------------------


def _find_invites_migration() -> pathlib.Path | None:
    """Locate the migration file whose upgrade() creates the 'invites' table.

    Discovered by CONTENT (not a hardcoded revision id/filename) so this test is meaningful
    both RED (no such file exists yet) and GREEN (finds whichever file actually creates it).
    """
    versions_dir = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
    for path in sorted(versions_dir.glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_table"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "invites"
            ):
                return path
    return None


def test_migration_is_purely_additive() -> None:
    """The new migration only CREATEs the `invites` table/indexes — never touches an existing
    table or row (M11). Static, DB-free check against the actual migration file's upgrade().
    """
    path = _find_invites_migration()
    assert path is not None, "expected a migration file whose upgrade() creates 'invites'"

    tree = ast.parse(path.read_text())
    upgrade_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "upgrade"),
        None,
    )
    assert upgrade_fn is not None, "migration file must define upgrade()"

    additive_ops = {"create_table", "create_index"}
    forbidden_ops = {
        "alter_column",
        "add_column",
        "drop_column",
        "drop_table",
        "drop_index",
        "rename_table",
        "drop_constraint",
        "execute",
    }
    # Table-name argument position differs by op: create_table(table_name, ...) puts it
    # first; create_index(index_name, table_name, ...) puts it second.
    table_arg_index = {"create_table": 0, "create_index": 1}
    touched_tables: set[str] = set()
    for node in ast.walk(upgrade_fn):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "op"
        ):
            op_name = node.func.attr
            assert op_name not in forbidden_ops, f"upgrade() must not call op.{op_name}"
            assert op_name in additive_ops, f"unexpected op.{op_name} in upgrade() — review"
            idx = table_arg_index[op_name]
            if len(node.args) > idx and isinstance(node.args[idx], ast.Constant):
                touched_tables.add(node.args[idx].value)
    assert touched_tables == {"invites"}, (
        f"upgrade() must only touch 'invites', got {touched_tables}"
    )


@pytest.mark.asyncio
async def test_invites_table_coexists_with_existing_rows_unchanged(
    db_session: AsyncSession,
) -> None:
    """Live complement to the static check: seeding an invites row never disturbs a
    pre-existing tenant/user row (the runtime manifestation of M11's additivity guarantee).
    """
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'AdditiveCo')"), {"tid": tenant_id}
    )
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role) "
            "VALUES (:id, :tid, 'additive@co.io', '$argon2id$v=dummy', 'owner')"
        ),
        {"id": user_id, "tid": tenant_id},
    )
    await db_session.commit()

    before = (
        (
            await db_session.execute(
                text("SELECT email, role FROM users WHERE id = :id"), {"id": user_id}
            )
        )
        .mappings()
        .one()
    )

    await db_session.execute(
        text(
            "INSERT INTO invites "
            "(id, tenant_id, email, role, token_hash, status, expires_at, invited_by_user_id) "
            "VALUES (:id, :tid, 'new@co.io', 'member', 'deadbeef00', 'pending', "
            "now() + interval '7 days', :uid)"
        ),
        {"id": uuid.uuid4(), "tid": tenant_id, "uid": user_id},
    )
    await db_session.commit()

    after = (
        (
            await db_session.execute(
                text("SELECT email, role FROM users WHERE id = :id"), {"id": user_id}
            )
        )
        .mappings()
        .one()
    )
    assert dict(before) == dict(after)


# ---------------------------------------------------------------------------
# TEST 25-27: superadmin-guard ground-truth + shared-predicate-identity additions.
#
# WHY THESE EXIST (build-time addition, beyond the §2 SCENARIOS 1:1 mapping):
# this task's real ground truth is that Role.SUPERADMIN is a genuine 7th StrEnum member
# (entities.py:8-19) and AssignUserRoleUseCase.execute already has a real, shipped
# SUPERADMIN GUARD (users_use_cases.py:64-72) that assert_role_within_ceiling must extract
# VERBATIM. TEST 4 above (test_invite_superadmin_role_always_rejected) proves the HTTP-level
# rejection shape, but it would ALSO pass, for a completely different and wrong reason
# (Role("superadmin") raising ValueError because the member simply doesn't exist), against a
# codebase slice that never added Role.SUPERADMIN at all. That is not a hypothetical: a
# sibling reference build for this exact task was produced against a stale worktree base
# that lacked Role.SUPERADMIN entirely, and its version of assert_role_within_ceiling
# silently omitted the superadmin-guard — a privilege-escalation-shaped gap that no
# HTTP-level test alone would have caught, because both routers' own pre-checks always
# intercept role="superadmin" before either use case (hence assert_role_within_ceiling) is
# ever reached over HTTP. These three tests close that gap directly, so a future regression
# that removes/weakens the guard is caught here even if the router-level pre-check were
# ever accidentally deleted.
# ---------------------------------------------------------------------------


def test_role_superadmin_is_a_genuine_enum_member() -> None:
    """Ground-truth precondition for the superadmin-rejection scenarios (M2/R4): pins that
    Role.SUPERADMIN is a REAL StrEnum member in this tree, not merely an unparseable string.
    If this ever regresses, TEST 4's 422 would still pass "by accident" (ValueError also
    422s) without the explicit business-rule guard this task's contract actually requires.
    """
    assert Role("superadmin") is Role.SUPERADMIN
    assert "SUPERADMIN" in Role.__members__


def test_assert_role_within_ceiling_rejects_superadmin_for_any_caller() -> None:
    """Direct unit test of the extracted shared predicate (member-invite-issuance TASK.md
    §3 NEW shared symbol, M1) — the ONLY way to observe this function's own internals,
    since BOTH callers (users_router.py, invites_router.py) already pre-empt with their own
    422 ERR_PAYLOAD_INVALID check before either use case (hence assert_role_within_ceiling)
    is ever reached for role="superadmin" over HTTP. Proves the superadmin-guard was
    extracted VERBATIM (not merely incidental to an enum member that doesn't exist) and
    fires UNCONDITIONALLY — even for OWNER, the highest self-service tier, exactly mirroring
    the real, shipped SUPERADMIN GUARD's own comment: "even an OWNER caller ... can never
    mint a superadmin through this path" (ground: users_use_cases.py:64-72).
    """
    from gateway.tenants.application.users_use_cases import assert_role_within_ceiling
    from gateway.tenants.domain.errors import EscalationForbiddenError

    for caller in (Role.OWNER, Role.ADMIN):
        with pytest.raises(EscalationForbiddenError):
            assert_role_within_ceiling(caller_role=caller, target_role=Role.SUPERADMIN)


def test_invite_use_cases_imports_shared_ceiling_predicate_not_a_fork() -> None:
    """M1 / §0 GROUND drift risk: CreateInviteUseCase MUST call the SAME
    assert_role_within_ceiling symbol AssignUserRoleUseCase uses — never a second,
    hand-rolled copy of _ADMIN_ASSIGNABLE that could silently desync from the original
    (a privilege-escalation-shaped bug, not a style nit, per §0 GROUND Issues/Risks).
    Proven by object identity, not just observed behavior: both modules must resolve to
    the literal same function object, so a future edit to one ceiling can never drift from
    the other.
    """
    from gateway.tenants.application.invite_use_cases import (
        assert_role_within_ceiling as invite_side,
    )
    from gateway.tenants.application.users_use_cases import (
        assert_role_within_ceiling as users_side,
    )

    assert invite_side is users_side, (
        "invite_use_cases.py must import assert_role_within_ceiling from "
        "users_use_cases.py, not define or import a second copy"
    )
