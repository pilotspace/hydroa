"""RED suite for RBAC role tiers + allowlist permission matrix (rbac-roles TASK.md §4).

Eight tests per the §4 plan:
  1. test_backcompat_owner_admin_member          — byte-identical behavior for existing roles
  2. test_operator_permissions                   — operator holds ROUTING/CATALOG/KEYS/OPS/AUDIT
  3. test_billing_admin_permissions              — billing_admin holds BUDGETS + USAGE read only
  4. test_viewer_readonly                        — viewer holds USAGE_READ + OPS_READ only
  5. test_escalation_guard                       — MEMBERS_MANAGE only owner/admin
  6. test_owner_only_secrets                     — PROVIDER_SECRETS / SECURITY_CONFIG owner only
  7. test_matrix_complete                        — every Role has a ROLE_PERMISSIONS entry
  8. test_require_permission_unit                — require_permission dep semantics

All assertions are behavioral (HTTP responses).  Identity JWTs are issued directly via
app.state.token_service.issue() — same pattern as tests/budgets/test_budgets.py Scenario 7.
DB-touching tests use the conftest `client` + `db_session` fixtures (drop/create per test).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── helpers ──────────────────────────────────────────────────────────────────


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_forbidden(resp: httpx.Response) -> None:
    """403 ERR_AUTH_FORBIDDEN — the standard rejection shape."""
    assert resp.status_code == 403, f"expected 403 got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN", resp.text


def _assert_not_forbidden(resp: httpx.Response) -> None:
    """Anything except 403 — we don't care about 404/409/422 from missing data."""
    assert resp.status_code != 403, (
        f"expected non-403 (permission granted) but got 403: {resp.text}"
    )


def _issue_token(app: Any, *, role_str: str) -> str:
    """Issue a JWT for an arbitrary user with the given role string.

    Uses the live token_service so the token decodes correctly in the gateway.
    The user_id / tenant_id are random UUIDs; we only care about role-based auth.
    """
    from gateway.tenants.domain.entities import Role

    role = Role(role_str)
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        role=role,
        email=f"{role_str}@rbac-test.local",
    )
    return token


# ── Fixture: a seeded tenant so HTTP paths that read DB don't 404 ─────────────


@pytest.fixture
async def seeded_tenant(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup an owner tenant and return {tenant_id, jwt}."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RBACCo",
            "email": "owner@rbactest.io",
            "password": "rbac-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login",
        json={"email": "owner@rbactest.io", "password": "rbac-horse-battery-01"},
    )
    return {
        "tenant_id": signup.json()["tenant_id"],
        "jwt": login.json()["access_token"],
    }


# ── TEST 1: Back-compat — owner/admin pass; member 403 on existing admin endpoints ──────────


async def test_backcompat_owner_admin_member(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Owner + admin must pass the admin surfaces; member must get 403. Byte-identical behavior."""
    owner_token = seeded_tenant["jwt"]

    # Issue an admin token (re-use the same tenant_id so DB reads succeed)
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    admin_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.ADMIN,
        email="admin@rbactest.io",
    )
    member_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.MEMBER,
        email="member@rbactest.io",
    )

    # --- /admin/budget PUT (BUDGETS_MANAGE) ---
    for good_token in (owner_token, admin_token):
        r = await client.put(
            "/admin/budget",
            json={"budget_usd_monthly": "99.00"},
            headers=_bearer(good_token),
        )
        _assert_not_forbidden(r)

    r = await client.put(
        "/admin/budget",
        json={"budget_usd_monthly": "99.00"},
        headers=_bearer(member_token),
    )
    _assert_forbidden(r)

    # --- /admin/routing GET (ROUTING_MANAGE) ---
    for good_token in (owner_token, admin_token):
        r = await client.get("/admin/routing", headers=_bearer(good_token))
        _assert_not_forbidden(r)

    r = await client.get("/admin/routing", headers=_bearer(member_token))
    _assert_forbidden(r)

    # --- /admin/cache PUT (maps to KEYS_MANAGE surface — currently require_owner_or_admin) ---
    for good_token in (owner_token, admin_token):
        r = await client.put(
            "/admin/cache",
            json={"enabled": False},
            headers=_bearer(good_token),
        )
        _assert_not_forbidden(r)

    r = await client.put(
        "/admin/cache",
        json={"enabled": False},
        headers=_bearer(member_token),
    )
    _assert_forbidden(r)

    # --- /admin/catalog/sync POST (CATALOG_SYNC) ---
    for good_token in (owner_token, admin_token):
        r = await client.post("/admin/catalog/sync", headers=_bearer(good_token))
        _assert_not_forbidden(r)

    r = await client.post("/admin/catalog/sync", headers=_bearer(member_token))
    _assert_forbidden(r)

    # --- /admin/reconciliation GET (USAGE_READ) ---
    for good_token in (owner_token, admin_token):
        r = await client.get("/admin/reconciliation", headers=_bearer(good_token))
        _assert_not_forbidden(r)

    r = await client.get("/admin/reconciliation", headers=_bearer(member_token))
    _assert_forbidden(r)


# ── TEST 2: Operator permissions ─────────────────────────────────────────────────────────────


async def test_operator_permissions(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Operator: ROUTING/CATALOG/KEYS/OPS/AUDIT pass; BUDGETS/PROVIDER_SECRETS/OIDC/MEMBERS 403."""
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    op_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.OPERATOR,
        email="operator@rbactest.io",
    )

    # ── should PASS (operator holds these perms) ──────────────────────────────
    # ROUTING_MANAGE
    r = await client.get("/admin/routing", headers=_bearer(op_token))
    _assert_not_forbidden(r)
    r = await client.put("/admin/routing", json={}, headers=_bearer(op_token))
    _assert_not_forbidden(r)

    # CATALOG_SYNC
    r = await client.post("/admin/catalog/sync", headers=_bearer(op_token))
    _assert_not_forbidden(r)

    # KEYS_MANAGE — admin models endpoint
    r = await client.get("/admin/models", headers=_bearer(op_token))
    _assert_not_forbidden(r)

    # OPS_READ — health / ratelimits / alerts
    r = await client.get("/admin/health/upstreams", headers=_bearer(op_token))
    _assert_not_forbidden(r)
    r = await client.get("/admin/ratelimits", headers=_bearer(op_token))
    _assert_not_forbidden(r)
    r = await client.get("/admin/alerts", headers=_bearer(op_token))
    _assert_not_forbidden(r)

    # USAGE_READ — reconciliation
    r = await client.get("/admin/reconciliation", headers=_bearer(op_token))
    _assert_not_forbidden(r)

    # ── should FAIL (operator does NOT hold these) ─────────────────────────────
    # BUDGETS_MANAGE
    r = await client.put(
        "/admin/budget", json={"budget_usd_monthly": "1.00"}, headers=_bearer(op_token)
    )
    _assert_forbidden(r)

    # MEMBERS_MANAGE (teams)
    r = await client.get("/admin/teams", headers=_bearer(op_token))
    _assert_forbidden(r)

    # PROVIDER_SECRETS (/admin/provider-keys) — owner-only
    r = await client.get("/admin/provider-keys", headers=_bearer(op_token))
    _assert_forbidden(r)

    # SECURITY_CONFIG (/admin/oidc) — owner-only
    r = await client.get("/admin/oidc", headers=_bearer(op_token))
    _assert_forbidden(r)


# ── TEST 3: Billing admin permissions ────────────────────────────────────────────────────────


async def test_billing_admin_permissions(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Billing_admin: BUDGETS + USAGE read pass; routing/keys/catalog/secrets/oidc/members/audit 403."""
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    ba_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.BILLING_ADMIN,
        email="billing@rbactest.io",
    )

    # ── should PASS ───────────────────────────────────────────────────────────
    # BUDGETS_MANAGE — write budget
    r = await client.put(
        "/admin/budget", json={"budget_usd_monthly": "5.00"}, headers=_bearer(ba_token)
    )
    _assert_not_forbidden(r)

    # USAGE_READ — reconciliation
    r = await client.get("/admin/reconciliation", headers=_bearer(ba_token))
    _assert_not_forbidden(r)

    # OPS_READ — health/ratelimits
    r = await client.get("/admin/health/upstreams", headers=_bearer(ba_token))
    _assert_not_forbidden(r)
    r = await client.get("/admin/ratelimits", headers=_bearer(ba_token))
    _assert_not_forbidden(r)
    r = await client.get("/admin/alerts", headers=_bearer(ba_token))
    _assert_not_forbidden(r)

    # ── should FAIL ───────────────────────────────────────────────────────────
    # ROUTING_MANAGE
    r = await client.get("/admin/routing", headers=_bearer(ba_token))
    _assert_forbidden(r)

    # KEYS_MANAGE
    r = await client.get("/admin/models", headers=_bearer(ba_token))
    _assert_forbidden(r)

    # CATALOG_SYNC
    r = await client.post("/admin/catalog/sync", headers=_bearer(ba_token))
    _assert_forbidden(r)

    # AUDIT_READ (just the permission check — endpoint may return 403 still since no audit table)
    # billed_admin does NOT hold AUDIT_READ per matrix
    # (no audit endpoint yet — we just test via require_permission unit test below)

    # MEMBERS_MANAGE
    r = await client.get("/admin/teams", headers=_bearer(ba_token))
    _assert_forbidden(r)

    # PROVIDER_SECRETS
    r = await client.get("/admin/provider-keys", headers=_bearer(ba_token))
    _assert_forbidden(r)

    # SECURITY_CONFIG
    r = await client.get("/admin/oidc", headers=_bearer(ba_token))
    _assert_forbidden(r)


# ── TEST 4: Viewer is read-only ──────────────────────────────────────────────────────────────


async def test_viewer_readonly(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Viewer: usage/ops reads pass; every write/admin endpoint 403."""
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])
    viewer_token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id,
        role=Role.VIEWER,
        email="viewer@rbactest.io",
    )

    # ── should PASS (USAGE_READ + OPS_READ) ──────────────────────────────────
    r = await client.get("/admin/reconciliation", headers=_bearer(viewer_token))
    _assert_not_forbidden(r)

    r = await client.get("/admin/health/upstreams", headers=_bearer(viewer_token))
    _assert_not_forbidden(r)

    r = await client.get("/admin/ratelimits", headers=_bearer(viewer_token))
    _assert_not_forbidden(r)

    r = await client.get("/admin/alerts", headers=_bearer(viewer_token))
    _assert_not_forbidden(r)

    # ── should FAIL (all write / manage surfaces) ─────────────────────────────
    # BUDGETS_MANAGE
    r = await client.put(
        "/admin/budget", json={"budget_usd_monthly": "1.00"}, headers=_bearer(viewer_token)
    )
    _assert_forbidden(r)

    # ROUTING_MANAGE
    r = await client.get("/admin/routing", headers=_bearer(viewer_token))
    _assert_forbidden(r)

    # CATALOG_SYNC
    r = await client.post("/admin/catalog/sync", headers=_bearer(viewer_token))
    _assert_forbidden(r)

    # KEYS_MANAGE
    r = await client.get("/admin/models", headers=_bearer(viewer_token))
    _assert_forbidden(r)

    # MEMBERS_MANAGE
    r = await client.get("/admin/teams", headers=_bearer(viewer_token))
    _assert_forbidden(r)

    # PROVIDER_SECRETS
    r = await client.get("/admin/provider-keys", headers=_bearer(viewer_token))
    _assert_forbidden(r)

    # SECURITY_CONFIG
    r = await client.get("/admin/oidc", headers=_bearer(viewer_token))
    _assert_forbidden(r)


# ── TEST 5: Escalation guard — MEMBERS_MANAGE owner/admin only ──────────────────────────────


async def test_escalation_guard(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Operator/billing_admin/viewer cannot touch /admin/teams (MEMBERS_MANAGE); owner+admin can."""
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])

    def _tok(role: Role) -> str:
        t, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            role=role,
            email=f"{role.value}@rbactest.io",
        )
        return t

    # Non-escalating roles must be denied
    for role in (Role.OPERATOR, Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER):
        tok = _tok(role)
        r = await client.get("/admin/teams", headers=_bearer(tok))
        assert r.status_code == 403, (
            f"role={role.value}: expected 403 on /admin/teams but got {r.status_code}"
        )

    # owner + admin must be allowed
    for role in (Role.OWNER, Role.ADMIN):
        tok = _tok(role)
        r = await client.get("/admin/teams", headers=_bearer(tok))
        assert r.status_code != 403, (
            f"role={role.value}: expected non-403 on /admin/teams but got 403: {r.text}"
        )


# ── TEST 6: Owner-only secrets unchanged ────────────────────────────────────────────────────


async def test_owner_only_secrets(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """Non-owner (admin/operator/billing_admin/viewer/member) 403 on /admin/provider-keys + /admin/oidc."""
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])

    def _tok(role: Role) -> str:
        t, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            role=role,
            email=f"{role.value}@rbactest.io",
        )
        return t

    non_owners = [Role.ADMIN, Role.OPERATOR, Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER]
    for role in non_owners:
        tok = _tok(role)
        r = await client.get("/admin/provider-keys", headers=_bearer(tok))
        assert r.status_code == 403, (
            f"role={role.value}: expected 403 on /admin/provider-keys but got {r.status_code}"
        )
        r = await client.get("/admin/oidc", headers=_bearer(tok))
        assert r.status_code == 403, (
            f"role={role.value}: expected 403 on /admin/oidc but got {r.status_code}"
        )

    # owner must pass provider-keys (OIDC GET needs a row — skip the 404 pass)
    owner_tok = _tok(Role.OWNER)
    r = await client.get("/admin/provider-keys", headers=_bearer(owner_tok))
    assert r.status_code != 403, f"owner: expected non-403 on /admin/provider-keys got 403"


# ── TEST 7: Matrix completeness guard ───────────────────────────────────────────────────────


def test_matrix_complete() -> None:
    """Every Role value must have an entry in ROLE_PERMISSIONS; a missing entry raises."""
    from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission, Role

    for role in Role:
        assert role in ROLE_PERMISSIONS, (
            f"incomplete_matrix: Role.{role.name} has no ROLE_PERMISSIONS entry"
        )
        # value must be a frozenset of Permission
        perms = ROLE_PERMISSIONS[role]
        assert isinstance(perms, frozenset), (
            f"ROLE_PERMISSIONS[{role!r}] must be frozenset[Permission], got {type(perms)}"
        )
        for p in perms:
            assert isinstance(p, Permission), (
                f"ROLE_PERMISSIONS[{role!r}] contains non-Permission value: {p!r}"
            )

    # Sentinel: a hypothetical Role not in ROLE_PERMISSIONS should have been caught above.
    # Validate owner holds ALL perms (the superuser invariant)
    assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission), (
        "owner must hold ALL permissions"
    )

    # member holds none of the admin permissions
    assert ROLE_PERMISSIONS[Role.MEMBER] == frozenset(), (
        "member must hold NO admin permissions"
    )


# ── TEST 8: require_permission unit test ────────────────────────────────────────────────────


async def test_require_permission_unit(
    client: httpx.AsyncClient,
    seeded_tenant: dict[str, str],
    app: Any,
) -> None:
    """require_permission(perm): passes iff ROLE_PERMISSIONS[role] contains perm; 403 otherwise.

    Proven via /admin/budget PUT (BUDGETS_MANAGE):
      - owner:         has BUDGETS_MANAGE → pass
      - admin:         has BUDGETS_MANAGE → pass
      - billing_admin: has BUDGETS_MANAGE → pass
      - operator:      NOT BUDGETS_MANAGE → 403 ERR_AUTH_FORBIDDEN
      - viewer:        NOT BUDGETS_MANAGE → 403 ERR_AUTH_FORBIDDEN
      - member:        NOT BUDGETS_MANAGE → 403 ERR_AUTH_FORBIDDEN
    """
    from gateway.tenants.domain.authz import ROLE_PERMISSIONS, Permission
    from gateway.tenants.domain.entities import Role

    tenant_id = uuid.UUID(seeded_tenant["tenant_id"])

    def _tok(role: Role) -> str:
        t, _ = app.state.token_service.issue(
            user_id=uuid.uuid4(),
            tenant_id=tenant_id,
            role=role,
            email=f"{role.value}@rbactest.io",
        )
        return t

    # roles that HOLD BUDGETS_MANAGE
    for role in (Role.OWNER, Role.ADMIN, Role.BILLING_ADMIN):
        assert Permission.BUDGETS_MANAGE in ROLE_PERMISSIONS[role], (
            f"{role.value} must hold BUDGETS_MANAGE"
        )
        tok = _tok(role)
        r = await client.put(
            "/admin/budget",
            json={"budget_usd_monthly": "1.00"},
            headers=_bearer(tok),
        )
        _assert_not_forbidden(r)

    # roles that do NOT hold BUDGETS_MANAGE
    for role in (Role.OPERATOR, Role.VIEWER, Role.MEMBER):
        assert Permission.BUDGETS_MANAGE not in ROLE_PERMISSIONS[role], (
            f"{role.value} must NOT hold BUDGETS_MANAGE"
        )
        tok = _tok(role)
        r = await client.put(
            "/admin/budget",
            json={"budget_usd_monthly": "1.00"},
            headers=_bearer(tok),
        )
        _assert_forbidden(r)
        # Shape must be exactly AUTH_FORBIDDEN
        assert r.json()["code"] == "ERR_AUTH_FORBIDDEN", r.json()
