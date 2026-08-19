"""Suite for superadmin-login (contract FROZEN @ v1 — TASK.md §2/§3).

This task PINS already-shipped, already-generic Role.SUPERADMIN behavior across three
existing surfaces — POST /admin/auth/login + GET /admin/auth/me (LoginUseCase.execute /
JwtTokenService.issue+decode, fully generic over Role, zero branching), and the two
owner-literal admin gates (_get_owner_identity in oidc_admin_router.py,
_require_owner_identity in provider_keys_admin_router.py) — purely via a NEW test suite.
Zero src/ files are touched by this task's Build (§3 CONTRACT's own claim).

This suite provisions its own superadmin User row directly — an SQL insert under the
platform tenant's id (resolved via get_platform_tenant, seeding one directly when the fast
create_all test schema has not run the seed migration) — with a REAL Argon2 hash via
app.state.password_hasher.hash(...). Mirrors tests/test_users_role.py::seeded_users's
direct-SQL pattern, with two deltas: platform tenant instead of a signed-up one, and a REAL
hash (never test_users_role.py's '$argon2id$v=dummy' placeholder), since this suite
exercises genuine POST /admin/auth/login round-trips, not a token_service.issue() bypass.
The two owner-literal-gate rejection scenarios (4/5) DO use the issue() bypass — no real
login round-trip needed there — mirroring tests/rbac_roles/test_rbac_roles.py::_issue_token.

FLAGGED, not silently resolved: the frozen §2/§3 prose names the OIDC gate's path as
"/admin/auth/oidc-config"; the router's actual mounted prefix (oidc_admin_router.py:45) is
"/admin/oidc" (GET/PUT "" under that prefix). The §0 GROUND anchor is unambiguous
(oidc_admin_router.py:130-143, `_get_owner_identity`) and apps/gateway/tests/rbac_roles/
test_rbac_roles.py already exercises this exact real path against the same gate — so these
tests target the REAL route, not the prose path string. See the Build report for detail.

DO NOT change these tests to make them pass — that is the Build phase's job. If a scenario
below genuinely cannot pass against current src/, STOP: the frozen contract's premise is
"zero src/ files should need to change" and that would be a finding to report, not a gap to
quietly patch.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPERADMIN_EMAIL = "root@platform.internal"
SUPERADMIN_PASSWORD = "superadmin-horse-battery-01"  # noqa: S105 — test fixture credential

#: The exact INSERT this suite's fixture uses to provision its superadmin test row —
#: a module-level constant (not inlined in the fixture) so the structural claim test below
#: can verify the REAL statement the fixture executes, not a restated copy of it.
_SUPERADMIN_INSERT_SQL = """
    INSERT INTO users (id, tenant_id, email, password_hash, role)
    VALUES (:id, :tid, :email, :password_hash, 'superadmin')
    """


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant (§3's sanctioned lookup);
    seed one directly when unmigrated/unseeded — the fast create_all test schema runs no
    data migrations (mirrors superadmin_role/test_superadmin_role.py's platform_tenant_id
    fixture precedent of applying migration-only seed data manually)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant  # noqa: PLC0415

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()

    tenant = await get_platform_tenant(db_session)
    assert tenant is not None, "platform tenant seed failed"
    return tenant.id


@pytest.fixture
async def superadmin_user(
    db_session: AsyncSession,
    platform_tenant_id: uuid.UUID,
    app: Any,
) -> dict[str, Any]:
    """Provision a real, loginable superadmin User row directly under the platform tenant.

    §3 CONTRACT's Schema section: direct SQL insert, real Argon2 hash via
    app.state.password_hasher.hash(...) — never a production HTTP path, because none
    exists and none is built by this task (§1 flag: first-superadmin bootstrap is
    explicitly out of scope).
    """
    uid = uuid.uuid4()
    password_hash = app.state.password_hasher.hash(SUPERADMIN_PASSWORD)

    await db_session.execute(
        text(_SUPERADMIN_INSERT_SQL),
        {
            "id": uid,
            "tid": platform_tenant_id,
            "email": SUPERADMIN_EMAIL,
            "password_hash": password_hash,
        },
    )
    await db_session.commit()

    return {"user_id": uid, "tenant_id": platform_tenant_id, "email": SUPERADMIN_EMAIL}


@pytest.fixture
async def ordinary_owner(client: httpx.AsyncClient) -> dict[str, Any]:
    """An ordinary (non-superadmin) owner in its own customer tenant — the comparison
    oracle for the wrong-password byte-identical-shape scenario (Scenario 3)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "OrdinaryCo",
            "email": "owner@ordinaryco.io",
            "password": "ordinary-horse-battery-01",
        },
    )
    assert signup.status_code == 201, signup.text
    return {"email": "owner@ordinaryco.io", "tenant_id": signup.json()["tenant_id"]}


# ---------------------------------------------------------------------------
# Scenario 1: a superadmin logs in and receives a correctly-shaped JWT
# ---------------------------------------------------------------------------


async def test_superadmin_login_returns_correctly_shaped_jwt(
    client: httpx.AsyncClient,
    superadmin_user: dict[str, Any],
    platform_tenant_id: uuid.UUID,
    app: Any,
) -> None:
    resp = await client.post(
        "/admin/auth/login",
        json={"email": SUPERADMIN_EMAIL, "password": SUPERADMIN_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert isinstance(body.get("expires_in"), int)
    token = body["access_token"]

    # Functional decode via the live token service — role/tenant_id round-trip correctly.
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415

    identity = app.state.token_service.decode(token)
    assert identity.role == Role.SUPERADMIN
    assert str(identity.role) == "superadmin"
    assert identity.tenant_id == platform_tenant_id
    assert identity.email == SUPERADMIN_EMAIL

    # Raw claim-shape proof: exactly {sub, tenant_id, role, email, exp, iat, iss} — byte-
    # identical to every other role's claim set (milestone's own invariant, proven not
    # assumed). verify_signature=False is safe here: we are inspecting shape, not trusting
    # an untrusted token — the signature itself is exercised by the functional decode above.
    raw_claims = jwt.decode(token, options={"verify_signature": False})
    # SANCTIONED EDIT (auth-hardening-login-sessions §3 M5, 2026-08-18): newly issued
    # session tokens carry a revocable "jti" — additive; every other shape assertion here
    # is unchanged.
    assert set(raw_claims.keys()) == {
        "sub",
        "tenant_id",
        "role",
        "email",
        "exp",
        "iat",
        "iss",
        "jti",
    }
    assert raw_claims["role"] == "superadmin"
    assert raw_claims["tenant_id"] == str(platform_tenant_id)
    assert raw_claims["email"] == SUPERADMIN_EMAIL


# ---------------------------------------------------------------------------
# Scenario 2: GET /admin/auth/me reflects the superadmin role
# ---------------------------------------------------------------------------


async def test_me_endpoint_reflects_superadmin_role(
    client: httpx.AsyncClient,
    superadmin_user: dict[str, Any],
    platform_tenant_id: uuid.UUID,
) -> None:
    login = await client.post(
        "/admin/auth/login",
        json={"email": SUPERADMIN_EMAIL, "password": SUPERADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    token = login.json()["access_token"]

    resp = await client.get("/admin/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "superadmin"
    assert body["tenant_id"] == str(platform_tenant_id)
    assert body["email"] == SUPERADMIN_EMAIL
    assert body["user_id"] == str(superadmin_user["user_id"])


# ---------------------------------------------------------------------------
# Scenario 3: a superadmin's wrong password is rejected identically to any other role
# ---------------------------------------------------------------------------


async def test_superadmin_wrong_password_rejected_identically(
    client: httpx.AsyncClient,
    superadmin_user: dict[str, Any],
    ordinary_owner: dict[str, Any],
) -> None:
    superadmin_resp = await client.post(
        "/admin/auth/login",
        json={"email": SUPERADMIN_EMAIL, "password": "wrong-password-entirely"},
    )
    assert superadmin_resp.status_code == 401, superadmin_resp.text
    superadmin_body = superadmin_resp.json()
    assert superadmin_body.get("code") == "ERR_AUTH_INVALID_CREDENTIALS"
    assert "access_token" not in superadmin_body

    # Oracle 1: an ordinary (non-superadmin) owner's wrong-password rejection.
    ordinary_resp = await client.post(
        "/admin/auth/login",
        json={"email": ordinary_owner["email"], "password": "wrong-password-entirely"},
    )
    assert ordinary_resp.status_code == 401, ordinary_resp.text

    # Oracle 2: a wholly unknown email — the strongest form of "no role-existence signal
    # leaked": a superadmin's wrong password must be indistinguishable even from an email
    # that was never registered at all. Must be EmailStr-valid (LoginRequest.email is
    # EmailStr — a reserved/non-deliverable TLD like ".invalid" or ".local" fails Pydantic
    # syntax validation with 422 before reaching LoginUseCase at all; ".io" is used as a
    # plain, valid test domain throughout this codebase's own test suites).
    unknown_resp = await client.post(
        "/admin/auth/login",
        json={
            "email": "nobody-registered@wrongpass-oracle.io",
            "password": "wrong-password-entirely",
        },
    )
    assert unknown_resp.status_code == 401, unknown_resp.text

    # Byte-identical shape across all three — no signal that "superadmin" exists, holds an
    # elevated role, or is even a different case from "email never registered".
    assert superadmin_resp.status_code == ordinary_resp.status_code == unknown_resp.status_code
    assert superadmin_body == ordinary_resp.json() == unknown_resp.json()


# ---------------------------------------------------------------------------
# Scenario 4: a superadmin JWT is rejected by the OIDC admin config endpoint
# ---------------------------------------------------------------------------


async def test_superadmin_jwt_rejected_by_oidc_admin_config(
    client: httpx.AsyncClient,
    superadmin_user: dict[str, Any],
    app: Any,
) -> None:
    """_get_owner_identity's `identity.role != Role.OWNER` gate (oidc_admin_router.py:130-
    143) — TODAY's behavior, pinned here, not built here. Real mounted path is GET
    /admin/oidc (prefix declared at oidc_admin_router.py:45); see module docstring flag on
    the frozen contract's "/admin/auth/oidc-config" prose vs. the real route."""
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415

    token, _ = app.state.token_service.issue(
        user_id=superadmin_user["user_id"],
        tenant_id=superadmin_user["tenant_id"],
        role=Role.SUPERADMIN,
        email=SUPERADMIN_EMAIL,
    )

    resp = await client.get("/admin/oidc", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    # PUT is gated by the same _get_owner_identity call (oidc_admin_router.py:210) — the
    # Reject section's "GET/PUT" pairing, both proven. Body must be a FULLY valid
    # OidcConfigPutBody (email_domains is required, no default): _get_owner_identity is
    # the first statement in the handler body, but FastAPI validates the Pydantic request
    # body BEFORE the handler runs at all — an incomplete body 422s before the owner-gate
    # is ever reached, which would prove nothing about the gate itself.
    put_resp = await client.put(
        "/admin/oidc",
        json={
            "issuer": "https://idp.example.com",
            "client_id": "x",
            "client_secret": "y",
            "authorize_url": "https://idp.example.com/authorize",
            "token_url": "https://idp.example.com/token",
            "jwks_url": "https://idp.example.com/jwks",
            "email_domains": ["example.com"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert put_resp.status_code == 403, put_resp.text
    assert put_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"


# ---------------------------------------------------------------------------
# Scenario 5: a superadmin JWT is rejected by the provider-keys admin endpoint
# ---------------------------------------------------------------------------


async def test_superadmin_jwt_rejected_by_provider_keys_admin(
    client: httpx.AsyncClient,
    superadmin_user: dict[str, Any],
    app: Any,
) -> None:
    """_require_owner_identity's identical gate (provider_keys_admin_router.py:97-112) —
    TODAY's behavior, pinned here, not built here. Real mounted path is GET
    /admin/provider-keys (prefix declared at provider_keys_admin_router.py:57)."""
    from gateway.tenants.domain.entities import Role  # noqa: PLC0415

    token, _ = app.state.token_service.issue(
        user_id=superadmin_user["user_id"],
        tenant_id=superadmin_user["tenant_id"],
        role=Role.SUPERADMIN,
        email=SUPERADMIN_EMAIL,
    )

    resp = await client.get("/admin/provider-keys", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"


# ---------------------------------------------------------------------------
# Structural claim 1 (§2): the superadmin test row is provisioned EXCLUSIVELY by this
# suite's own fixture — a direct SQL insert under the platform tenant with a REAL Argon2
# hash — never any production HTTP path (none exists; §1 flag). §2 frames this as
# "confirmed by reading the fixture module itself ... not a pass/fail HTTP scenario"; the
# checks below re-verify that same claim programmatically against the actual statement
# and hasher the fixture uses, so it stays true as the suite evolves rather than being a
# one-time human read.
# ---------------------------------------------------------------------------


def test_fixture_provisions_superadmin_row_via_direct_sql_under_platform_tenant() -> None:
    assert "INSERT INTO users" in _SUPERADMIN_INSERT_SQL
    assert "'superadmin'" in _SUPERADMIN_INSERT_SQL
    assert ":tid" in _SUPERADMIN_INSERT_SQL  # bound to platform_tenant_id, not a literal
    assert ":password_hash" in _SUPERADMIN_INSERT_SQL  # bound param, never a hardcoded hash


def test_fixture_uses_real_argon2_hash_not_dummy_placeholder() -> None:
    """The fixture derives password_hash from app.state.password_hasher.hash(...) — the
    concrete Argon2PasswordHasher wired at main.py:633 — never test_users_role.py's dummy
    placeholder ('$argon2id$v=dummy'). Proven directly against that concrete class: a real
    hash verifies the correct password and rejects the wrong one; the dummy placeholder can
    never verify successfully. This is why Scenario 1's 200 response is only reachable
    because the fixture uses a real hash — a structural fact about the fixture's approach,
    not merely an assertion about it.
    """
    from gateway.tenants.infrastructure.argon2_hasher import Argon2PasswordHasher  # noqa: PLC0415

    hasher = Argon2PasswordHasher()
    real_hash = hasher.hash(SUPERADMIN_PASSWORD)

    assert real_hash != "$argon2id$v=dummy"
    assert hasher.verify(real_hash, SUPERADMIN_PASSWORD) is True
    assert hasher.verify(real_hash, "definitely-the-wrong-password") is False
    assert hasher.verify("$argon2id$v=dummy", SUPERADMIN_PASSWORD) is False
