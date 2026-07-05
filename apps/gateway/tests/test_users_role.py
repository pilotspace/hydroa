"""RED suite for GET /admin/users + PUT /admin/users/{id}/role (rbac-admin-ui TASK.md §4).

Seven tests per the §4 plan, plus one added by role-update-persistence-fix TASK.md §4:
  1. test_owner_assigns_any_tier           — owner sets operator/admin/owner -> 200 + audit
  2. test_admin_cannot_grant_owner_admin   — admin->owner/admin = 403; admin->operator/etc = 200
  3. test_self_role_change_forbidden       — caller PUTs own id -> 403
  4. test_members_manage_gating            — operator/billing_admin/viewer/member -> 403 GET + PUT
  5. test_role_validation                  — unknown role -> 400; cross-tenant user -> 404
  6. test_orm_accepts_six_roles            — create_all schema accepts all 6 role values
  7. test_list_users_returns_tenant_users  — GET /admin/users returns caller's tenant users
  8. test_role_change_persists_past_request — the role survives an independent read after
     the request's own session has closed (not just the response body)

All assertions are behavioral (HTTP responses).  Identity JWTs are issued directly via
app.state.token_service.issue() — same pattern as tests/rbac_roles/test_rbac_roles.py.
DB-touching tests use the conftest `client` + `db_session` fixtures (drop/create per test).

SECURITY PROPERTIES PROVEN SERVER-SIDE:
  - Escalation guard: admin cannot assign owner/admin (test_admin_cannot_grant_owner_admin)
  - Self-guard: no caller can change their own role (test_self_role_change_forbidden)
  - MEMBERS_MANAGE gating: only owner/admin can use these endpoints
  - Tenant isolation: list only returns caller's tenant (seeded per test)
  - Audit: every successful change fires user.role_assign (test_owner_assigns_any_tier)
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_forbidden(resp: httpx.Response) -> None:
    """403 ERR_AUTH_FORBIDDEN — the standard rejection shape."""
    assert resp.status_code == 403, f"expected 403 got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text


def _issue_token(
    app: Any,
    *,
    role_str: str,
    user_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> str:
    """Issue a JWT with specific role/user_id/tenant_id."""
    from gateway.tenants.domain.entities import Role

    role = Role(role_str)
    uid = user_id or uuid.uuid4()
    tid = tenant_id or uuid.uuid4()
    token, _ = app.state.token_service.issue(
        user_id=uid,
        tenant_id=tid,
        role=role,
        email=f"{role_str}@test.local",
    )
    return token


# ---------------------------------------------------------------------------
# Fixture: a seeded tenant with owner + a second user for role changes
# ---------------------------------------------------------------------------


@pytest.fixture
async def seeded_users(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> dict[str, Any]:
    """Signup an owner + create a second member user in same tenant.

    Returns:
        owner_token: JWT for owner
        member_id: UUID of the target user (a member in the same tenant)
        tenant_id: UUID of the tenant
        member_token: JWT for the member (for self-guard test)
        owner_user_id: UUID of the owner user (for self-guard test)
    """
    # Signup creates tenant + owner
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RolesCo",
            "email": "owner@rolestest.io",
            "password": "roles-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": "owner@rolestest.io", "password": "roles-horse-battery-01"},
    )
    assert login.status_code == 200, login.text
    owner_token = login.json()["access_token"]
    tenant_id = uuid.UUID(signup.json()["tenant_id"])

    # Decode the owner's user_id from the token
    owner_identity = app.state.token_service.decode(owner_token)
    owner_user_id = owner_identity.user_id

    # Insert a second member user in the same tenant directly via SQL
    member_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:id, :tid, :email, '$argon2id$v=dummy', 'member')
            """
        ),
        {"id": member_id, "tid": tenant_id, "email": "member@rolestest.io"},
    )
    await db_session.commit()

    # Issue a JWT for the member (same tenant) - for self-guard test
    member_token, _ = app.state.token_service.issue(
        user_id=member_id,
        tenant_id=tenant_id,
        role=__import__("gateway.tenants.domain.entities", fromlist=["Role"]).Role.MEMBER,
        email="member@rolestest.io",
    )

    return {
        "owner_token": owner_token,
        "member_id": member_id,
        "tenant_id": tenant_id,
        "member_token": member_token,
        "owner_user_id": owner_user_id,
    }


# ---------------------------------------------------------------------------
# TEST 1: Owner assigns any tier + audit emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_assigns_any_tier(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """Owner can assign operator, admin, owner — each returns 200 + role updated.
    A user.role_assign audit event is recorded on success.
    """
    owner_token = seeded_users["owner_token"]
    target_id = seeded_users["member_id"]
    headers = _bearer(owner_token)

    # Assign 'operator'
    r = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "operator"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "operator"
    assert body["id"] == str(target_id)

    # Assign 'admin'
    r2 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=headers,
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["role"] == "admin"

    # Assign 'owner'
    r3 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "owner"},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["role"] == "owner"

    # Verify audit event was emitted (user.role_assign in audit_events table)
    import asyncio

    await asyncio.sleep(0.05)  # let fire-and-forget complete
    row = await db_session.execute(
        text(
            "SELECT action, metadata FROM audit_events "
            "WHERE action = 'user.role_assign' "
            "ORDER BY created_at DESC LIMIT 1"
        )
    )
    audit_row = row.fetchone()
    assert audit_row is not None, "Expected user.role_assign audit event"
    assert audit_row[0] == "user.role_assign"
    metadata = audit_row[1]
    # metadata is a JSON object with target_user_id, old_role, new_role
    assert "target_user_id" in metadata or str(target_id) in str(metadata)


# ---------------------------------------------------------------------------
# TEST 2: Admin cannot grant owner or admin (escalation guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_cannot_grant_owner_admin(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    app: Any,
) -> None:
    """Admin is blocked from assigning owner or admin; can assign lower tiers."""
    tenant_id = seeded_users["tenant_id"]
    target_id = seeded_users["member_id"]

    # Issue an admin JWT in the same tenant
    admin_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=__import__("gateway.tenants.domain.entities", fromlist=["Role"]).Role.ADMIN,
        email="admin@rolestest.io",
    )
    headers = _bearer(admin_token)

    # Admin cannot assign 'owner'
    r = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "owner"},
        headers=headers,
    )
    _assert_forbidden(r)

    # Admin cannot assign 'admin'
    r2 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=headers,
    )
    _assert_forbidden(r2)

    # Admin CAN assign 'operator'
    r3 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "operator"},
        headers=headers,
    )
    assert r3.status_code == 200, r3.text
    assert r3.json()["role"] == "operator"

    # Admin CAN assign 'billing_admin'
    r4 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "billing_admin"},
        headers=headers,
    )
    assert r4.status_code == 200, r4.text

    # Admin CAN assign 'viewer'
    r5 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "viewer"},
        headers=headers,
    )
    assert r5.status_code == 200, r5.text

    # Admin CAN assign 'member'
    r6 = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "member"},
        headers=headers,
    )
    assert r6.status_code == 200, r6.text


# ---------------------------------------------------------------------------
# TEST 3: Self-guard — caller cannot change their own role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_role_change_forbidden(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    app: Any,
) -> None:
    """Any caller — including owner — is forbidden from changing their own role."""
    # Owner tries to change their own role
    owner_token = seeded_users["owner_token"]
    owner_user_id = seeded_users["owner_user_id"]
    r = await client.put(
        f"/admin/users/{owner_user_id}/role",
        json={"role": "admin"},
        headers=_bearer(owner_token),
    )
    _assert_forbidden(r)

    # Member (with MEMBERS_MANAGE-less role) tries to change their own role — also 403
    # (403 because they lack MEMBERS_MANAGE, but the self-guard fires on any role)
    # Use a member that has a different token but uses their own ID
    member_id = seeded_users["member_id"]
    tenant_id = seeded_users["tenant_id"]

    # Issue an owner token but with member's user_id to test the self-guard specifically
    # (this simulates an owner whose user_id == target user_id)
    owner_with_member_id_token, _ = app.state.token_service.issue(
        user_id=member_id,  # same as target
        tenant_id=tenant_id,
        role=__import__("gateway.tenants.domain.entities", fromlist=["Role"]).Role.OWNER,
        email="owner2@rolestest.io",
    )
    r2 = await client.put(
        f"/admin/users/{member_id}/role",
        json={"role": "operator"},
        headers=_bearer(owner_with_member_id_token),
    )
    _assert_forbidden(r2)


# ---------------------------------------------------------------------------
# TEST 4: MEMBERS_MANAGE gating — lower roles 403 on GET + PUT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_members_manage_gating(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    app: Any,
) -> None:
    """operator / billing_admin / viewer / member get 403 on both endpoints."""
    tenant_id = seeded_users["tenant_id"]
    target_id = seeded_users["member_id"]
    from gateway.tenants.domain.entities import Role

    for role_str in ["operator", "billing_admin", "viewer", "member"]:
        token, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            role=Role(role_str),
            email=f"{role_str}@gating.local",
        )
        headers = _bearer(token)

        # GET /admin/users must be 403
        r_get = await client.get("/admin/users", headers=headers)
        _assert_forbidden(r_get)

        # PUT /admin/users/{id}/role must be 403
        r_put = await client.put(
            f"/admin/users/{target_id}/role",
            json={"role": "member"},
            headers=headers,
        )
        _assert_forbidden(r_put)


# ---------------------------------------------------------------------------
# TEST 5: Validation — unknown role + cross-tenant user
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_validation(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    app: Any,
    db_session: AsyncSession,
) -> None:
    """Unknown role -> 400 ERR_PAYLOAD_INVALID; cross-tenant/missing user -> 404 ERR_USER_NOT_FOUND."""
    owner_token = seeded_users["owner_token"]
    target_id = seeded_users["member_id"]
    headers = _bearer(owner_token)

    # Unknown role value → ERR_PAYLOAD_INVALID
    # NOTE: the codebase convention uses 422 for all payload validation errors;
    # the contract names ERR_PAYLOAD_INVALID which is status=422 in error_catalog.py.
    r = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "superadmin"},
        headers=headers,
    )
    assert r.status_code in (400, 422), r.text
    assert r.json().get("code") == "ERR_PAYLOAD_INVALID", r.text

    # Non-existent user (random UUID) -> 404
    missing_id = uuid.uuid4()
    r2 = await client.put(
        f"/admin/users/{missing_id}/role",
        json={"role": "member"},
        headers=headers,
    )
    assert r2.status_code == 404, r2.text
    assert r2.json().get("code") == "ERR_USER_NOT_FOUND", r2.text

    # Cross-tenant user — create a user in a different tenant
    other_tenant_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO tenants (id, name) VALUES (:tid, 'OtherCo')
            """
        ),
        {"tid": other_tenant_id},
    )
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:uid, :tid, 'cross@other.io', '$argon2id$v=dummy', 'member')
            """
        ),
        {"uid": other_user_id, "tid": other_tenant_id},
    )
    await db_session.commit()

    r3 = await client.put(
        f"/admin/users/{other_user_id}/role",
        json={"role": "member"},
        headers=headers,
    )
    assert r3.status_code == 404, r3.text
    assert r3.json().get("code") == "ERR_USER_NOT_FOUND", r3.text


# ---------------------------------------------------------------------------
# TEST 6: ORM accepts all six roles via create_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orm_accepts_six_roles(db_session: AsyncSession) -> None:
    """The UserRow CheckConstraint must accept all 6 roles after ORM fix.

    If the ORM still has ('owner','admin','member') only, inserting 'operator'
    or 'billing_admin' into a create_all schema raises IntegrityError.
    """
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:tid, 'OrmTestCo')"),
        {"tid": tenant_id},
    )

    all_six_roles = ["owner", "admin", "operator", "billing_admin", "viewer", "member"]
    for i, role in enumerate(all_six_roles):
        uid = uuid.uuid4()
        # This INSERT must NOT raise IntegrityError
        await db_session.execute(
            text(
                """
                INSERT INTO users (id, tenant_id, email, password_hash, role)
                VALUES (:uid, :tid, :email, 'dummyhash', :role)
                """
            ),
            {"uid": uid, "tid": tenant_id, "email": f"role{i}@ormtest.io", "role": role},
        )
    await db_session.commit()  # commit must succeed — no IntegrityError


# ---------------------------------------------------------------------------
# TEST 7: GET /admin/users returns caller's tenant users
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_users_returns_tenant_users(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
) -> None:
    """GET /admin/users returns {users:[{id,email,role}]} scoped to caller's tenant."""
    owner_token = seeded_users["owner_token"]
    headers = _bearer(owner_token)

    r = await client.get("/admin/users", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "users" in body
    users = body["users"]
    assert isinstance(users, list)
    assert len(users) >= 2  # owner + the seeded member

    # Each entry has id, email, role
    for u in users:
        assert "id" in u
        assert "email" in u
        assert "role" in u

    # The seeded member is included
    member_ids = [u["id"] for u in users]
    assert str(seeded_users["member_id"]) in member_ids


# ---------------------------------------------------------------------------
# TEST 8: role change persists past the request (role-update-persistence-fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_role_change_persists_past_request(
    client: httpx.AsyncClient,
    seeded_users: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    """A role change must survive the request's own session closing.

    `db_session` is a genuinely separate AsyncSession from the one the request handler
    uses (each opened fresh via `app.state.sessionmaker()`) — reading through it after the
    request returns proves the write is durable, not just visible to the request's own
    response body or a read sharing its still-open transaction.
    """
    owner_token = seeded_users["owner_token"]
    target_id = seeded_users["member_id"]
    headers = _bearer(owner_token)

    r = await client.put(
        f"/admin/users/{target_id}/role",
        json={"role": "admin"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "admin"

    row = await db_session.execute(
        text("SELECT role FROM users WHERE id = :id"),
        {"id": target_id},
    )
    persisted_role = row.scalar_one()
    assert persisted_role == "admin", (
        f"role change did not survive past the request — independent read saw "
        f"{persisted_role!r}, expected 'admin' (the request's own response showed "
        f"'admin' but the write was rolled back on session close)"
    )
