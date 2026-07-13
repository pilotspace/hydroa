"""RED suite for cross-tenant-keys-members (TASK.md §3 CONTRACT FROZEN @ v1).

Covers 7 new superadmin-only, cross-tenant routes:
  GET    /admin/platform/tenants/{tenant_id}/keys
  POST   /admin/platform/tenants/{tenant_id}/keys
  PATCH  /admin/platform/tenants/{tenant_id}/keys/{key_id}
  POST   /admin/platform/tenants/{tenant_id}/keys/{key_id}/rotate
  DELETE /admin/platform/tenants/{tenant_id}/keys/{key_id}
  GET    /admin/platform/tenants/{tenant_id}/users
  PUT    /admin/platform/tenants/{tenant_id}/users/{user_id}/role

Every use-case class called (CreateKeyUseCase/ListKeysUseCase/UpdateKeyUseCase/
RotateKeyUseCase/RevokeKeyUseCase/ListTenantUsersUseCase/AssignUserRoleUseCase) is REUSED
UNCHANGED — this router layer's ONLY job is parametrizing them by the PATH tenant_id
(never identity.tenant_id). A cross-tenant key_id/user_id must 404 IDENTICALLY to an
unknown one — no leak via a distinguishing signal. Every "happy path" test below targets
a tenant DIFFERENT from the superadmin's own (platform) tenant, so a router bug that
threads identity.tenant_id instead of the path value would surface as an unexpected 404
on a call that should succeed — the happy-path tests are therefore also the wiring proof,
not just the dedicated cross-tenant-rejection tests (see TASK.md §6 Refute-read verdict).

RED before BUILD: neither router exists yet -> every route not already covered by an
existing self-service router 404s with FastAPI's default {"detail": "Not Found"} body
(no ProblemError "code" field) — the right RED reason, confirmed before any src/ write.

Two scenarios (M12, M13) assert a permanent ABSENCE of a route. Empirically verified
(spike against the live app, see task notes) that FastAPI/Starlette resolves an
unregistered METHOD on an otherwise-registered PATH as 405 "Method Not Allowed", not 404
"Not Found" — e.g. POST /admin/users (self-service, GET+PUT/{id}/role only) -> 405, while
DELETE /admin/users/{id} (no route shape matches at all) -> 404. Both are FastAPI's
default, non-ProblemError rejection (no "code" field, no 2xx, no data leak); the frozen
scenario's prose names literal "404" but the binding §3 CONTRACT itself only requires
these routes are "NOT added" (no status code committed) — so these two tests assert the
achievable, semantically-equivalent condition (status in {404, 405}, no success, no
ProblemError body) rather than a literal 404 that no correct implementation could ever
produce for the POST sub-case once the sibling GET/PATCH/DELETE routes exist at the same
path shape. See TASK.md §7 OBSERVE spec-delta for the full note.

Superadmin identities are minted directly via app.state.token_service.issue(...) — no DB
user row needed for auth. M6/M7 (members list/reassign) DO need real `users` rows since
ListTenantUsersUseCase/AssignUserRoleUseCase query the users table directly. Mirrors
tests/platform_tenant_directory/test_platform_tenant_directory.py's fixture style +
tests/test_users_role.py's users-table seeding pattern.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.keys.api.platform_keys_router import platform_keys_router
from gateway.main import create_app
from gateway.tenants.api.platform_users_router import platform_users_router
from tests.credential_stub import install_stub_resolver

PLATFORM_TENANTS = "/admin/platform/tenants"

# ---------------------------------------------------------------------------
# Fixtures + helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    """Local OVERRIDE of the root conftest.py `app` fixture, scoped to THIS test module only.

    main.py is NOT touched by this task (shared with a parallel sibling task this session —
    see the task's §5 Scope note); the orchestrating session registers both new routers there
    after both parallel tasks finish. To genuinely verify (not just write and hope) that these
    routers work end-to-end against the REAL app — same middleware, same ProblemError exception
    handlers, same JWT token service, same sessionmaker — this fixture calls the SAME
    `create_app(settings)` factory main.py itself calls, then additionally registers the two new
    routers exactly as main.py will. Every other test file's `app` fixture (conftest.py's own,
    unmodified) is completely unaffected — pytest resolves fixture overrides per-module.
    """
    application = create_app(settings)
    install_stub_resolver(application)
    application.include_router(platform_keys_router)
    application.include_router(platform_users_router)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id via get_platform_tenant; seed one directly when the
    fast create_all test schema has not run the seed migration (mirrors
    tests/platform_tenant_directory/test_platform_tenant_directory.py's fixture of the
    same name)."""
    from gateway.tenants.infrastructure.repository import get_platform_tenant

    tenant = await get_platform_tenant(db_session)
    if tenant is not None:
        return tenant.id

    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, 'Platform', 'platform')"),
        {"id": tid},
    )
    await db_session.commit()
    return tid


def _issue_token(
    app: Any, *, role: Any, tenant_id: uuid.UUID, email: str, user_id: uuid.UUID | None = None
) -> str:
    """Mint a Bearer token directly via the live token service — no DB user row required."""
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(), tenant_id=tenant_id, role=role, email=email
    )
    return token


@pytest.fixture
async def superadmin_token(app: Any, platform_tenant_id: uuid.UUID) -> str:
    from gateway.tenants.domain.entities import Role

    return _issue_token(
        app, role=Role.SUPERADMIN, tenant_id=platform_tenant_id, email="root@platform.internal"
    )


async def _seed_customer_tenant(db_session: AsyncSession, *, name: str) -> uuid.UUID:
    """Insert a kind='customer' tenant row — the cross-tenant TARGET in every scenario."""
    tid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"),
        {"id": tid, "name": name},
    )
    await db_session.commit()
    return tid


async def _seed_key(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    name: str = "seeded-key",
    revoked: bool = False,
    team_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a minimal api_keys row directly. key_hash is a dummy placeholder — these
    tests only manage keys via the admin API, never authenticate WITH them, so a
    non-conforming hash format is safe.

    revoked_at uses the SQL now() literal (not a bound Python datetime) — asyncpg's
    timestamptz codec cannot always infer the correct wire type for a raw text()
    positional parameter in this exact "prepare from literal SQL text" shape, and
    passing a tz-aware Python datetime through it raises
    "can't subtract offset-naive and offset-aware datetimes" (confirmed reproducing
    deterministically). Bypassing Python-side datetime binding entirely for this
    column sidesteps the issue; NULL/now() are both untyped SQL literals.
    """
    kid = uuid.uuid4()
    params = {"id": kid, "tid": tenant_id, "name": name, "team_id": team_id}
    if revoked:
        await db_session.execute(
            text(
                """
                INSERT INTO api_keys (id, tenant_id, name, key_hash, revoked_at, team_id)
                VALUES (:id, :tid, :name, 'dummy-hash-not-a-real-secret', now(), :team_id)
                """
            ),
            params,
        )
    else:
        await db_session.execute(
            text(
                """
                INSERT INTO api_keys (id, tenant_id, name, key_hash, revoked_at, team_id)
                VALUES (:id, :tid, :name, 'dummy-hash-not-a-real-secret', NULL, :team_id)
                """
            ),
            params,
        )
    await db_session.commit()
    return kid


async def _seed_user(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str = "member",
) -> uuid.UUID:
    """Insert a users row directly (email is globally-unique — always pass a fresh one)."""
    uid = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role)
            VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role)
            """
        ),
        {"id": uid, "tid": tenant_id, "email": email, "role": role},
    )
    await db_session.commit()
    return uid


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# KEYS — GET (list) — M1, M10
# ===========================================================================


async def test_superadmin_lists_target_tenant_keys_redacted(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M1, M10: complete key list (active + revoked, unfiltered), redacted shape only."""
    other = await _seed_customer_tenant(db_session, name="KeysCo")
    await _seed_key(db_session, tenant_id=other, name="active-1")
    await _seed_key(db_session, tenant_id=other, name="active-2")
    await _seed_key(db_session, tenant_id=other, name="revoked-1", revoked=True)

    resp = await client.get(f"{PLATFORM_TENANTS}/{other}/keys", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 3
    for item in items:
        assert "key_hash" not in item
        assert "key" not in item
        assert "secret" not in item


async def test_list_keys_empty_tenant_returns_empty_list(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M1 (boundary): a target tenant with zero keys returns 200 + empty array, not an error."""
    other = await _seed_customer_tenant(db_session, name="EmptyKeysCo")
    resp = await client.get(f"{PLATFORM_TENANTS}/{other}/keys", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


# ===========================================================================
# KEYS — POST (create) — M2, R8
# ===========================================================================


async def test_superadmin_creates_key_for_target_tenant_plaintext_once(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M2: creates a key FOR the target tenant via CreateKeyUseCase; plaintext shown once;
    the row's real tenant_id is T_other, not the superadmin's own (platform) tenant — the
    wiring proof that the PATH tenant_id, not identity.tenant_id, reached the use-case."""
    other = await _seed_customer_tenant(db_session, name="CreateForCo")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys",
        json={"name": "cross-tenant-minted"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"].startswith("sk-")
    key_id = body["key_id"]

    row = (
        await db_session.execute(
            text("SELECT tenant_id FROM api_keys WHERE id = :id"), {"id": key_id}
        )
    ).one()
    assert str(row.tenant_id) == str(other)

    list_resp = await client.get(
        f"{PLATFORM_TENANTS}/{other}/keys", headers=_bearer(superadmin_token)
    )
    listed = {i["key_id"]: i for i in list_resp.json()}
    assert key_id in listed
    assert "key" not in listed[key_id]
    assert "key_hash" not in listed[key_id]


async def test_create_key_rejects_team_id_from_different_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """R8: a team_id belonging to a DIFFERENT tenant than the path 404s; no key row created."""
    from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository

    other = await _seed_customer_tenant(db_session, name="TeamTargetCo")
    third = await _seed_customer_tenant(db_session, name="TeamThirdCo")
    foreign_team = await SqlAlchemyTeamRepository(db_session).create(
        team_id=uuid.uuid4(), tenant_id=third, name="ThirdTeam"
    )

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys",
        json={"name": "should-fail", "team_id": str(foreign_team.id)},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TEAM_NOT_FOUND"

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM api_keys WHERE tenant_id = :tid"), {"tid": other}
        )
    ).scalar_one()
    assert count == 0


# ===========================================================================
# KEYS — PATCH — M3, R4
# ===========================================================================


async def test_superadmin_patches_target_tenant_key(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M3: updates governance fields on an active key owned by the target tenant."""
    other = await _seed_customer_tenant(db_session, name="PatchCo")
    key_id = await _seed_key(db_session, tenant_id=other, name="patch-target")

    resp = await client.patch(
        f"{PLATFORM_TENANTS}/{other}/keys/{key_id}",
        json={"monthly_budget_usd": "42.50"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["monthly_budget_usd"] == "42.50"

    row = (
        await db_session.execute(
            text("SELECT tenant_id FROM api_keys WHERE id = :id"), {"id": key_id}
        )
    ).one()
    assert str(row.tenant_id) == str(other)


async def test_patch_rejects_cross_tenant_key_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M3, R4: a key_id belonging to a DIFFERENT tenant than the path 404s identically;
    K3's fields under T_third are completely unchanged."""
    other = await _seed_customer_tenant(db_session, name="PatchTargetCo")
    third = await _seed_customer_tenant(db_session, name="PatchThirdCo")
    key3 = await _seed_key(db_session, tenant_id=third, name="third-key")

    resp = await client.patch(
        f"{PLATFORM_TENANTS}/{other}/keys/{key3}",
        json={"monthly_budget_usd": "99.00"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_KEY_NOT_FOUND"

    row = (
        await db_session.execute(
            text("SELECT tenant_id, monthly_budget_usd FROM api_keys WHERE id = :id"), {"id": key3}
        )
    ).one()
    assert str(row.tenant_id) == str(third)
    assert row.monthly_budget_usd is None


# ===========================================================================
# KEYS — ROTATE — M4, R4
# ===========================================================================


async def test_superadmin_rotates_target_tenant_key(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M4: atomically rotates a key owned by the target tenant; new plaintext shown once;
    old key revoked, new key's tenant_id is T_other."""
    other = await _seed_customer_tenant(db_session, name="RotateCo")
    key_id = await _seed_key(db_session, tenant_id=other, name="rotate-target")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys/{key_id}/rotate",
        json={},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["key"].startswith("sk-")
    new_key_id = body["new_key_id"]
    assert new_key_id != str(key_id)
    assert body["superseded_key_id"] == str(key_id)

    old_row = (
        await db_session.execute(
            text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": key_id}
        )
    ).one()
    assert old_row.revoked_at is not None

    new_row = (
        await db_session.execute(
            text("SELECT tenant_id, revoked_at FROM api_keys WHERE id = :id"), {"id": new_key_id}
        )
    ).one()
    assert str(new_row.tenant_id) == str(other)
    assert new_row.revoked_at is None


async def test_rotate_rejects_cross_tenant_key_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M4, R4: a key_id belonging to a DIFFERENT tenant than the path 404s; K3 is NOT
    revoked and NO new key is created — combined with the happy-path test above (which
    proves the PATH tenant_id, not the superadmin's own, is what lets a rotate succeed),
    this proves RotateKeyUseCase's own old_key.tenant_id != tenant_id check is fed the
    PATH value everywhere, not just in the case that happens to also reject."""
    other = await _seed_customer_tenant(db_session, name="RotateTargetCo")
    third = await _seed_customer_tenant(db_session, name="RotateThirdCo")
    key3 = await _seed_key(db_session, tenant_id=third, name="third-rotate-key")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys/{key3}/rotate",
        json={},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_KEY_NOT_FOUND"

    row = (
        await db_session.execute(
            text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": key3}
        )
    ).one()
    assert row.revoked_at is None

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM api_keys WHERE tenant_id = :tid"), {"tid": third}
        )
    ).scalar_one()
    assert count == 1  # only the original seeded key — no new key minted under T_third


# ===========================================================================
# KEYS — REVOKE (DELETE) — M5, R4
# ===========================================================================


async def test_superadmin_revokes_target_tenant_key(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M5: soft-revokes a key owned by the target tenant."""
    other = await _seed_customer_tenant(db_session, name="RevokeCo")
    key_id = await _seed_key(db_session, tenant_id=other, name="revoke-target")

    resp = await client.delete(
        f"{PLATFORM_TENANTS}/{other}/keys/{key_id}", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 204, resp.text

    row = (
        await db_session.execute(
            text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": key_id}
        )
    ).one()
    assert row.revoked_at is not None


async def test_revoke_rejects_cross_tenant_key_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M5, R4: a key_id belonging to a DIFFERENT tenant than the path 404s; K3 under
    T_third remains active (revoked_at still null)."""
    other = await _seed_customer_tenant(db_session, name="RevokeTargetCo")
    third = await _seed_customer_tenant(db_session, name="RevokeThirdCo")
    key3 = await _seed_key(db_session, tenant_id=third, name="third-revoke-key")

    resp = await client.delete(
        f"{PLATFORM_TENANTS}/{other}/keys/{key3}", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_KEY_NOT_FOUND"

    row = (
        await db_session.execute(
            text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": key3}
        )
    ).one()
    assert row.revoked_at is None


# ===========================================================================
# KEYS — non-superadmin rejection + tenant-not-found — R2, R3
# ===========================================================================


async def test_non_superadmin_rejected_on_every_keys_route(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """R2: an OWNER (holds every Permission, including KEYS_MANAGE) is 403'd on every
    keys route regardless; the target key is untouched; T_owner's own /admin/keys
    remains fully functional and unaffected by this new gate."""
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    other = await _seed_customer_tenant(db_session, name="TargetForOwnerCo")
    key_id = await _seed_key(db_session, tenant_id=other, name="untouchable")
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@ownerkeys.io"
    )
    headers = _bearer(owner_token)

    get_resp = await client.get(f"{PLATFORM_TENANTS}/{other}/keys", headers=headers)
    assert get_resp.status_code == 403, get_resp.text
    assert get_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    post_resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys", json={"name": "x"}, headers=headers
    )
    assert post_resp.status_code == 403, post_resp.text

    patch_resp = await client.patch(
        f"{PLATFORM_TENANTS}/{other}/keys/{key_id}", json={}, headers=headers
    )
    assert patch_resp.status_code == 403, patch_resp.text

    rotate_resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys/{key_id}/rotate", json={}, headers=headers
    )
    assert rotate_resp.status_code == 403, rotate_resp.text

    delete_resp = await client.delete(f"{PLATFORM_TENANTS}/{other}/keys/{key_id}", headers=headers)
    assert delete_resp.status_code == 403, delete_resp.text

    key_row = (
        await db_session.execute(
            text("SELECT revoked_at, monthly_budget_usd FROM api_keys WHERE id = :id"),
            {"id": key_id},
        )
    ).one()
    assert key_row.revoked_at is None
    assert key_row.monthly_budget_usd is None

    own_list_resp = await client.get("/admin/keys", headers=headers)
    assert own_list_resp.status_code == 200, own_list_resp.text


async def test_list_keys_nonexistent_tenant_404s(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    """R3: a tenant_id with no matching row 404s ERR_TENANT_NOT_FOUND."""
    missing_id = uuid.uuid4()
    resp = await client.get(
        f"{PLATFORM_TENANTS}/{missing_id}/keys", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"


async def test_create_key_nonexistent_tenant_404s_not_500(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """R3: creating against a nonexistent tenant_id 404s cleanly — NOT an unhandled 500 /
    IntegrityError from api_keys.tenant_id's FK constraint; no orphaned row is created."""
    missing_id = uuid.uuid4()
    resp = await client.post(
        f"{PLATFORM_TENANTS}/{missing_id}/keys",
        json={"name": "orphan-attempt"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"

    count = (await db_session.execute(text("SELECT count(*) FROM api_keys"))).scalar_one()
    assert count == 0


# ===========================================================================
# KEYS — redaction field-set — M10
# ===========================================================================


async def test_redacted_key_list_field_set_exact(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M10: each entry's field set is EXACTLY the KeyInfoResponse shape — no more, no less."""
    other = await _seed_customer_tenant(db_session, name="FieldSetCo")
    await _seed_key(db_session, tenant_id=other, name="field-set-key")

    resp = await client.get(f"{PLATFORM_TENANTS}/{other}/keys", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    items = resp.json()
    assert len(items) == 1
    # SANCTIONED EDIT (payload-capture-store TASK.md §3 manifest maintenance,
    # 2026-07-10): added capture_enabled to this manifest — additive field on
    # KeyInfoResponse (same PATCH partial-update idiom precedent as cache_enabled).
    assert set(items[0].keys()) == {
        "key_id",
        "name",
        "prefix",
        "created_at",
        "revoked_at",
        "monthly_budget_usd",
        "soft_budget_usd",
        "expires_at",
        "model_allowlist",
        "rpm_limit",
        "tpm_limit",
        "team_id",
        "cache_enabled",
        "capture_enabled",
        # SANCTIONED EDIT (service-tiers TASK.md §3 manifest maintenance,
        # 2026-07-13): added tier to this manifest — additive field on
        # KeyInfoResponse (per-key service tier; same additive idiom as above).
        "tier",
    }


# ===========================================================================
# MEMBERS — GET (list) — M6
# ===========================================================================


async def test_superadmin_lists_target_tenant_members(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M6: returns the target tenant's complete user/member roster."""
    other = await _seed_customer_tenant(db_session, name="MembersCo")
    await _seed_user(db_session, tenant_id=other, email="u1@membersco.io", role="owner")
    await _seed_user(db_session, tenant_id=other, email="u2@membersco.io", role="member")
    await _seed_user(db_session, tenant_id=other, email="u3@membersco.io", role="admin")

    resp = await client.get(f"{PLATFORM_TENANTS}/{other}/users", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["users"]) == 3
    emails = {u["email"] for u in body["users"]}
    assert emails == {"u1@membersco.io", "u2@membersco.io", "u3@membersco.io"}
    for u in body["users"]:
        assert set(u.keys()) == {"id", "email", "role"}


async def test_list_members_empty_tenant_returns_empty_roster(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M6 (boundary): a target tenant with zero users returns 200 + empty array."""
    other = await _seed_customer_tenant(db_session, name="NoMembersCo")
    resp = await client.get(f"{PLATFORM_TENANTS}/{other}/users", headers=_bearer(superadmin_token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"users": []}


# ===========================================================================
# MEMBERS — PUT role — M7, M8, R5, R6
# ===========================================================================


async def test_superadmin_reassigns_target_tenant_member_role(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M7: assigns a new role to a user owned by the target tenant."""
    other = await _seed_customer_tenant(db_session, name="ReassignCo")
    user_id = await _seed_user(
        db_session, tenant_id=other, email="target@reassignco.io", role="member"
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{other}/users/{user_id}/role",
        json={"role": "admin"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["role"] == "admin"
    assert body["id"] == str(user_id)

    row = (
        await db_session.execute(
            text("SELECT tenant_id, role FROM users WHERE id = :id"), {"id": user_id}
        )
    ).one()
    assert str(row.tenant_id) == str(other)
    assert row.role == "admin"


async def test_assign_role_rejects_cross_tenant_user_id(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M7, R5: a user_id belonging to a DIFFERENT tenant than the path 404s; U3's role
    under T_third is completely unchanged."""
    other = await _seed_customer_tenant(db_session, name="AssignTargetCo")
    third = await _seed_customer_tenant(db_session, name="AssignThirdCo")
    user3 = await _seed_user(
        db_session, tenant_id=third, email="u3@assignthirdco.io", role="member"
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{other}/users/{user3}/role",
        json={"role": "admin"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_USER_NOT_FOUND"

    row = (
        await db_session.execute(
            text("SELECT tenant_id, role FROM users WHERE id = :id"), {"id": user3}
        )
    ).one()
    assert str(row.tenant_id) == str(third)
    assert row.role == "member"


async def test_assign_superadmin_role_rejected_same_shape_as_self_service(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    app: Any,
) -> None:
    """M8, R6: role=="superadmin" is hard-rejected 422 ERR_PAYLOAD_INVALID BEFORE the
    use-case runs; U's role is unchanged. Proven BYTE-IDENTICAL to self-service's own
    rejection of the exact same payload (not merely "some rejection happened") by hitting
    the pre-existing /admin/users/{id}/role directly with an equivalent owner caller and
    diffing the two response bodies."""
    from gateway.tenants.domain.entities import Role

    other = await _seed_customer_tenant(db_session, name="NoSuperadminCo")
    user_id = await _seed_user(
        db_session, tenant_id=other, email="target@nosuperadminco.io", role="member"
    )

    cross_resp = await client.put(
        f"{PLATFORM_TENANTS}/{other}/users/{user_id}/role",
        json={"role": "superadmin"},
        headers=_bearer(superadmin_token),
    )
    assert cross_resp.status_code == 422, cross_resp.text
    assert cross_resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = (
        await db_session.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id})
    ).one()
    assert row.role == "member"

    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=other, email="owner@nosuperadminco.io"
    )
    self_service_resp = await client.put(
        f"/admin/users/{user_id}/role",
        json={"role": "superadmin"},
        headers=_bearer(owner_token),
    )
    assert self_service_resp.status_code == 422, self_service_resp.text
    assert self_service_resp.json() == cross_resp.json(), (
        "cross-tenant and self-service superadmin-rejection bodies must be byte-identical"
    )


async def test_assign_role_unparseable_literal_rejected(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """R6: an unparseable role literal is rejected identically to self-service."""
    other = await _seed_customer_tenant(db_session, name="BogusRoleCo")
    user_id = await _seed_user(
        db_session, tenant_id=other, email="target@bogusroleco.io", role="member"
    )

    resp = await client.put(
        f"{PLATFORM_TENANTS}/{other}/users/{user_id}/role",
        json={"role": "bogus"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    row = (
        await db_session.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id})
    ).one()
    assert row.role == "member"


# ===========================================================================
# MEMBERS — non-superadmin rejection + tenant-not-found — R2, R3
# ===========================================================================


async def test_non_superadmin_rejected_on_every_members_route(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
) -> None:
    """R2: an OWNER (holds MEMBERS_MANAGE) is 403'd on every members route regardless;
    the target user's role is untouched; T_owner's own /admin/users remains functional."""
    from gateway.tenants.domain.entities import Role

    owner_tenant_id = uuid.uuid4()
    other = await _seed_customer_tenant(db_session, name="TargetMembersCo")
    user_id = await _seed_user(
        db_session, tenant_id=other, email="target@targetmembersco.io", role="member"
    )
    owner_token = _issue_token(
        app, role=Role.OWNER, tenant_id=owner_tenant_id, email="owner@ownermembersco.io"
    )
    headers = _bearer(owner_token)

    get_resp = await client.get(f"{PLATFORM_TENANTS}/{other}/users", headers=headers)
    assert get_resp.status_code == 403, get_resp.text
    assert get_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    put_resp = await client.put(
        f"{PLATFORM_TENANTS}/{other}/users/{user_id}/role",
        json={"role": "admin"},
        headers=headers,
    )
    assert put_resp.status_code == 403, put_resp.text
    assert put_resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    row = (
        await db_session.execute(text("SELECT role FROM users WHERE id = :id"), {"id": user_id})
    ).one()
    assert row.role == "member"

    own_users_resp = await client.get("/admin/users", headers=headers)
    assert own_users_resp.status_code == 200, own_users_resp.text


async def test_list_members_nonexistent_tenant_404s(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    """R3: a tenant_id with no matching row 404s ERR_TENANT_NOT_FOUND."""
    missing_id = uuid.uuid4()
    resp = await client.get(
        f"{PLATFORM_TENANTS}/{missing_id}/users", headers=_bearer(superadmin_token)
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"


# ===========================================================================
# CROSS-CUTTING — missing token, no invite/remove, no playground-token, no audit — R1, M11-M13
# ===========================================================================


async def test_missing_bearer_token_rejected_both_routers(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """R1: missing Authorization header 401s on both new router files; no data returned."""
    other = await _seed_customer_tenant(db_session, name="NoTokenCo")

    keys_resp = await client.get(f"{PLATFORM_TENANTS}/{other}/keys")
    assert keys_resp.status_code == 401, keys_resp.text
    assert keys_resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"
    assert "key_id" not in keys_resp.text

    users_resp = await client.get(f"{PLATFORM_TENANTS}/{other}/users")
    assert users_resp.status_code == 401, users_resp.text
    assert users_resp.json().get("code") == "ERR_AUTH_INVALID_TOKEN"
    assert "users" not in users_resp.json()


async def test_no_invite_or_remove_member_route_exists(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M12: no invite (POST collection) or remove (DELETE item) route exists for members.

    See module docstring: POST to the registered GET-only collection path resolves to
    405 (Method Not Allowed, aggregated across the app's whole route table) rather than a
    bare 404 once the sibling GET/PUT routes are registered at overlapping path shapes;
    DELETE to a path shape with no route at all is a genuine 404. Both assert the same
    thing operationally: no such capability exists, no 2xx, no ProblemError body, no data
    mutated or leaked.
    """
    other = await _seed_customer_tenant(db_session, name="NoInviteCo")
    headers = _bearer(superadmin_token)

    invite_resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/users", json={"email": "new@noinviteco.io"}, headers=headers
    )
    assert invite_resp.status_code in (404, 405), invite_resp.text
    assert "code" not in invite_resp.json()  # never a ProblemError — no handler ran

    remove_resp = await client.delete(
        f"{PLATFORM_TENANTS}/{other}/users/{uuid.uuid4()}", headers=headers
    )
    assert remove_resp.status_code == 404, remove_resp.text

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM users WHERE tenant_id = :tid"), {"tid": other}
        )
    ).scalar_one()
    assert count == 0  # the invite attempt created no row


async def test_no_cross_tenant_playground_token_route_exists(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M13: no cross-tenant playground-token route exists (session/auth mechanic reserved
    for tenant-impersonation, milestone 3 — out of scope here). See module docstring for
    why this resolves to 405 (Method Not Allowed), not a bare 404, once PATCH/DELETE
    /{key_id} are registered at the same one-segment path shape — no 2xx, no ProblemError,
    no key minted regardless."""
    other = await _seed_customer_tenant(db_session, name="NoPlaygroundCo")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys/playground-token",
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code in (404, 405), resp.text
    assert "code" not in resp.json()

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM api_keys WHERE tenant_id = :tid"), {"tid": other}
        )
    ).scalar_one()
    assert count == 0


async def test_all_7_routes_now_emit_audit_events(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Updated by admin-console-audit (TASK.md §3, FROZEN @ v1): one successful call against
    each of these 7 routes now writes exactly one audit_events row per call (7 total) —
    closing the deferral this test used to pin ("the deliberate, documented deferral to
    admin-console-audit (task 4), not an oversight" — that deferral's own named target task
    is the one making this exact change). Full per-route action-name/metadata shape coverage
    lives in that task's own suite (tests/admin_console_audit/); this test only proves
    aggregate coverage across all 7 routes in one pass, mirroring its own prior structure.
    """
    other = await _seed_customer_tenant(db_session, name="AuditedNowCo")
    user_id = await _seed_user(
        db_session, tenant_id=other, email="audit-target@auditfreeco.io", role="member"
    )
    key_id = await _seed_key(db_session, tenant_id=other, name="audit-key")
    headers = _bearer(superadmin_token)

    assert (
        await client.get(f"{PLATFORM_TENANTS}/{other}/keys", headers=headers)
    ).status_code == 200

    create_resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys", json={"name": "audit-check-key"}, headers=headers
    )
    assert create_resp.status_code == 201, create_resp.text
    created_key_id = create_resp.json()["key_id"]

    assert (
        await client.patch(
            f"{PLATFORM_TENANTS}/{other}/keys/{key_id}",
            json={"monthly_budget_usd": "10.00"},
            headers=headers,
        )
    ).status_code == 200

    rotate_resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys/{created_key_id}/rotate", json={}, headers=headers
    )
    assert rotate_resp.status_code == 201, rotate_resp.text

    assert (
        await client.delete(f"{PLATFORM_TENANTS}/{other}/keys/{key_id}", headers=headers)
    ).status_code == 204

    assert (
        await client.get(f"{PLATFORM_TENANTS}/{other}/users", headers=headers)
    ).status_code == 200

    assert (
        await client.put(
            f"{PLATFORM_TENANTS}/{other}/users/{user_id}/role",
            json={"role": "admin"},
            headers=headers,
        )
    ).status_code == 200

    await asyncio.sleep(0.05)  # let each fire-and-forget audit write complete

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_events WHERE tenant_id = :tid"), {"tid": other}
        )
    ).scalar_one()
    assert count == 7


# ===========================================================================
# PAYLOAD VALIDATION — R7
# ===========================================================================


async def test_create_and_patch_payload_validation_matches_self_service(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    app: Any,
) -> None:
    """R7: create/patch behave identically to self-service — same reused field validators,
    same ProblemError body, since both call sites share the EXACT SAME CreateKeyRequest
    Pydantic model (verbatim reuse, no re-validation logic written for this task)."""
    from gateway.tenants.domain.entities import Role

    other = await _seed_customer_tenant(db_session, name="ValidationCo")

    resp = await client.post(
        f"{PLATFORM_TENANTS}/{other}/keys",
        json={"name": "bad-budget", "monthly_budget_usd": "-5.00"},
        headers=_bearer(superadmin_token),
    )
    assert resp.status_code == 422, resp.text
    assert resp.json().get("code") == "ERR_PAYLOAD_INVALID"

    count = (
        await db_session.execute(
            text("SELECT count(*) FROM api_keys WHERE tenant_id = :tid"), {"tid": other}
        )
    ).scalar_one()
    assert count == 0

    owner_token = _issue_token(app, role=Role.OWNER, tenant_id=other, email="owner@validationco.io")
    self_service_resp = await client.post(
        "/admin/keys",
        json={"name": "bad-budget", "monthly_budget_usd": "-5.00"},
        headers=_bearer(owner_token),
    )
    assert self_service_resp.status_code == 422, self_service_resp.text
    assert self_service_resp.json() == resp.json(), (
        "cross-tenant and self-service payload-validation bodies must be byte-identical"
    )
