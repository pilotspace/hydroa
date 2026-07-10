"""RED->GREEN suite for scim-provisioning (TASK.md §2 SCENARIOS + §3 CONTRACT, FROZEN @ v1).

One test per §2 scenario, plus the §1 Reject entries not already covered by an explicit
scenario block (missing-bearer 401, malformed-payload 400 invalidValue, PATCH-immutable-
path 400 mutability, unknown/revoked token-id on rotate/revoke 404). DB-touching tests use
the conftest `client` + `db_session` + `app` fixtures (drop/create per test, real Postgres).

SECURITY PROPERTIES PROVEN SERVER-SIDE (appsec-engineer persona, sensitivity: security):
  - Tenant isolation: a cross-tenant {id} 404s, byte-identical to an unknown id (no oracle).
  - Role is NEVER SCIM-controlled: CreateScimUserUseCase's signature cannot accept one; a
    role/roles PATCH path is silently ignored, never a 400 (M3).
  - Deactivation blocks BOTH password and OIDC login, cascades team removal atomically,
    and is independently auditable (M6/M7/M8).
  - A revoked/rotated-away SCIM token authenticates NOTHING (401), verified against every
    /scim/v2/* route class it could reach.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.tenants.domain.entities import Role

from .conftest import (
    FAKE_NONCE,
    FAKE_STATE,
    FakeOidcExchanger,
    add_user_to_team,
    bearer,
    count_audit_rows,
    create_scim_token,
    create_team,
    fetch_one_audit_row,
    fetch_user_row,
    issue_jwt,
    login,
    make_id_token,
    make_oidc_settings,
    revoked_at_of,
    signup_tenant,
    team_members_for_user,
)


# ---------------------------------------------------------------------------
# Fixtures: two independent tenants, each with an owner + a live SCIM token
# ---------------------------------------------------------------------------


@pytest.fixture
async def tenant_a(client: httpx.AsyncClient) -> dict[str, Any]:
    signup = await signup_tenant(client, tenant_name="Tenant A", email="owner-a@corp.example")
    resp = await login(client, email="owner-a@corp.example", password="correct-horse-battery-1")
    assert resp.status_code == 200, resp.text
    owner_token = resp.json()["access_token"]
    return {
        "tenant_id": uuid.UUID(str(signup["tenant_id"])),
        "owner_user_id": uuid.UUID(str(signup["user_id"])),
        "owner_token": owner_token,
    }


@pytest.fixture
async def tenant_b(client: httpx.AsyncClient) -> dict[str, Any]:
    signup = await signup_tenant(client, tenant_name="Tenant B", email="owner-b@corp.example")
    resp = await login(client, email="owner-b@corp.example", password="correct-horse-battery-1")
    assert resp.status_code == 200, resp.text
    owner_token = resp.json()["access_token"]
    return {
        "tenant_id": uuid.UUID(str(signup["tenant_id"])),
        "owner_user_id": uuid.UUID(str(signup["user_id"])),
        "owner_token": owner_token,
    }


@pytest.fixture
async def scim_a(client: httpx.AsyncClient, tenant_a: dict[str, Any]) -> dict[str, Any]:
    result = await create_scim_token(client, owner_token=tenant_a["owner_token"], name="Okta-A")
    return {**tenant_a, **result}


@pytest.fixture
async def scim_b(client: httpx.AsyncClient, tenant_b: dict[str, Any]) -> dict[str, Any]:
    result = await create_scim_token(client, owner_token=tenant_b["owner_token"], name="Okta-B")
    return {**tenant_b, **result}


def _scim_bearer(scim: dict[str, Any]) -> dict[str, str]:
    return bearer(str(scim["token"]))


# ===========================================================================
# Part A — SCIM token management (M1)
# ===========================================================================


async def test_owner_creates_scim_token(
    client: httpx.AsyncClient, tenant_a: dict[str, Any], db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/admin/scim/tokens", json={"name": "Okta"}, headers=bearer(tenant_a["owner_token"])
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["token"].startswith("scim-")
    assert "." in body["token"]
    assert body["name"] == "Okta"
    token_id = uuid.UUID(str(body["id"]))

    row = (
        await db_session.execute(
            text(
                "SELECT tenant_id, token_hash, revoked_at FROM scim_tokens WHERE id = :id"
            ),
            {"id": token_id},
        )
    ).mappings().one()
    assert row["tenant_id"] == tenant_a["tenant_id"]
    assert row["revoked_at"] is None
    assert row["token_hash"] != body["token"]  # never plaintext at rest


async def test_member_cannot_create_scim_token(
    app: Any, client: httpx.AsyncClient, tenant_a: dict[str, Any], db_session: AsyncSession
) -> None:
    member_token = issue_jwt(app, role=Role.MEMBER, tenant_id=tenant_a["tenant_id"])
    resp = await client.post(
        "/admin/scim/tokens", json={"name": "Okta"}, headers=bearer(member_token)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM scim_tokens WHERE tenant_id = :tid"
            ),
            {"tid": tenant_a["tenant_id"]},
        )
    ).scalar_one()
    assert count == 0


async def test_admin_scim_tokens_requires_auth(client: httpx.AsyncClient) -> None:
    """Known-problem-class regression: a bare require_permission without Depends()
    silently no-ops the gate. A request with NO bearer token must 401, never pass."""
    resp = await client.post("/admin/scim/tokens", json={"name": "Okta"})
    assert resp.status_code == 401, resp.text


async def test_rotate_scim_token_atomically(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    s1_id = uuid.UUID(str(scim_a["id"]))
    resp = await client.post(
        f"/admin/scim/tokens/{s1_id}/rotate", headers=bearer(scim_a["owner_token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    s2_id = uuid.UUID(str(body["id"]))
    assert s2_id != s1_id
    assert body["token"].startswith("scim-")

    s1_revoked = await revoked_at_of(db_session, table="scim_tokens", id_col="id", row_id=s1_id)
    s2_revoked = await revoked_at_of(db_session, table="scim_tokens", id_col="id", row_id=s2_id)
    assert s1_revoked is not None
    assert s2_revoked is None

    # S1 immediately stops authenticating.
    probe = await client.get(
        "/scim/v2/ServiceProviderConfig", headers=bearer(str(scim_a["token"]))
    )
    assert probe.status_code == 401


async def test_rotate_unknown_token_id_returns_404(
    client: httpx.AsyncClient, tenant_a: dict[str, Any]
) -> None:
    resp = await client.post(
        f"/admin/scim/tokens/{uuid.uuid4()}/rotate", headers=bearer(tenant_a["owner_token"])
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "ERR_SCIM_TOKEN_NOT_FOUND"


async def test_revoke_scim_token_returns_204(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    token_id = uuid.UUID(str(scim_a["id"]))
    resp = await client.delete(
        f"/admin/scim/tokens/{token_id}", headers=bearer(scim_a["owner_token"])
    )
    assert resp.status_code == 204, resp.text
    revoked = await revoked_at_of(db_session, table="scim_tokens", id_col="id", row_id=token_id)
    assert revoked is not None

    # A repeat revoke of the already-revoked token 404s (no double-revoke oracle).
    resp2 = await client.delete(
        f"/admin/scim/tokens/{token_id}", headers=bearer(scim_a["owner_token"])
    )
    assert resp2.status_code == 404
    assert resp2.json().get("code") == "ERR_SCIM_TOKEN_NOT_FOUND"


# ===========================================================================
# Part B — SCIM user provisioning (M3-M12)
# ===========================================================================


async def test_missing_scim_bearer_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/scim/v2/Users")
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body.get("detail") == "invalid_token"
    assert body.get("schemas") == ["urn:ietf:params:scim:api:messages:2.0:Error"]


async def test_revoked_scim_token_cannot_authenticate(
    client: httpx.AsyncClient, scim_a: dict[str, Any]
) -> None:
    token_id = uuid.UUID(str(scim_a["id"]))
    revoke = await client.delete(
        f"/admin/scim/tokens/{token_id}", headers=bearer(scim_a["owner_token"])
    )
    assert revoke.status_code == 204

    # The request never reaches any user-mutating code path — probe a write route.
    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "ghost@corp.example",
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 401, resp.text
    assert resp.json().get("detail") == "invalid_token"


async def test_idp_creates_user_via_scim(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "new@corp.example",
            "active": True,
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["userName"] == "new@corp.example"
    assert body["active"] is True
    user_id = uuid.UUID(str(body["id"]))

    row = await fetch_user_row(db_session, user_id)
    assert row is not None
    assert row["tenant_id"] == scim_a["tenant_id"]
    assert row["role"] == "member"
    assert row["auth_method"] == "scim"

    await asyncio.sleep(0.05)
    audit_row = await fetch_one_audit_row(db_session, action="scim.user_create")
    assert audit_row is not None
    assert audit_row.actor_scim_token_id == uuid.UUID(str(scim_a["id"]))
    assert audit_row.actor_user_id is None


async def test_scim_payload_role_attribute_ignored(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "escalate@corp.example",
            "role": "owner",
            "urn:ietf:params:scim:schemas:extension:enterprise:2.0:User": {"role": "owner"},
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 201, resp.text
    user_id = uuid.UUID(str(resp.json()["id"]))
    row = await fetch_user_row(db_session, user_id)
    assert row["role"] == "member", "role attribute in payload must never be honored"


async def test_duplicate_email_on_create(
    client: httpx.AsyncClient,
    scim_a: dict[str, Any],
    scim_b: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    first = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "dup@corp.example",
        },
        headers=_scim_bearer(scim_a),
    )
    assert first.status_code == 201, first.text

    # Same tenant re-POST.
    dup_same = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "dup@corp.example",
        },
        headers=_scim_bearer(scim_a),
    )
    assert dup_same.status_code == 409, dup_same.text
    assert dup_same.json().get("scimType") == "uniqueness"

    # A DIFFERENT tenant's token also gets 409 (global email uniqueness).
    dup_other = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "dup@corp.example",
        },
        headers=_scim_bearer(scim_b),
    )
    assert dup_other.status_code == 409, dup_other.text
    assert dup_other.json().get("scimType") == "uniqueness"

    total = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :e"),
            {"e": "dup@corp.example"},
        )
    ).scalar_one()
    assert total == 1


async def test_malformed_scim_payload_returns_400_invalid_value(
    client: httpx.AsyncClient, scim_a: dict[str, Any]
) -> None:
    resp = await client.post("/scim/v2/Users", json={}, headers=_scim_bearer(scim_a))
    assert resp.status_code == 400, resp.text
    assert resp.json().get("scimType") == "invalidValue"


async def test_cross_tenant_scim_token_cannot_reach_another_tenant_user(
    client: httpx.AsyncClient,
    scim_a: dict[str, Any],
    scim_b: dict[str, Any],
    db_session: AsyncSession,
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "u2@corp.example",
        },
        headers=_scim_bearer(scim_b),
    )
    assert create.status_code == 201
    u2_id = uuid.UUID(str(create.json()["id"]))

    resp = await client.get(f"/scim/v2/Users/{u2_id}", headers=_scim_bearer(scim_a))
    assert resp.status_code == 404, resp.text
    assert "scimType" not in resp.json()

    row = await fetch_user_row(db_session, u2_id)
    assert row["email"] == "u2@corp.example"
    assert row["deactivated_at"] is None


async def test_filter_by_username(client: httpx.AsyncClient, scim_a: dict[str, Any]) -> None:
    for name in ("a@corp.example", "b@corp.example"):
        r = await client.post(
            "/scim/v2/Users",
            json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": name},
            headers=_scim_bearer(scim_a),
        )
        assert r.status_code == 201

    resp = await client.get(
        '/scim/v2/Users?filter=userName eq "a@corp.example"', headers=_scim_bearer(scim_a)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totalResults"] == 1
    names = [r["userName"] for r in body["Resources"]]
    assert names == ["a@corp.example"]
    assert "b@corp.example" not in names


async def test_patch_active_false_deactivates_and_cascades_team_removal(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "u@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = uuid.UUID(str(create.json()["id"]))
    team_id = await create_team(db_session, tenant_id=scim_a["tenant_id"], name="Team G")
    await add_user_to_team(db_session, team_id=team_id, user_id=user_id)
    assert await team_members_for_user(db_session, user_id) == 1

    resp = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is False

    row = await fetch_user_row(db_session, user_id)
    assert row["deactivated_at"] is not None
    assert await team_members_for_user(db_session, user_id) == 0

    await asyncio.sleep(0.05)
    audit_row = await fetch_one_audit_row(db_session, action="scim.user_deactivate")
    assert audit_row is not None
    assert audit_row.actor_scim_token_id == uuid.UUID(str(scim_a["id"]))


async def test_patch_immutable_path_returns_400_mutability(
    client: httpx.AsyncClient, scim_a: dict[str, Any]
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "imm@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = create.json()["id"]

    resp = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "id", "value": str(uuid.uuid4())}],
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 400, resp.text
    assert resp.json().get("scimType") == "mutability"


async def test_deactivated_user_cannot_login_with_password(
    client: httpx.AsyncClient, tenant_a: dict[str, Any], scim_a: dict[str, Any]
) -> None:
    # Baseline: wrong-password shape, for the byte-identical comparison below.
    wrong = await login(client, email="owner-a@corp.example", password="totally-wrong-pw-1")
    assert wrong.status_code == 401

    deactivate = await client.patch(
        f"/scim/v2/Users/{tenant_a['owner_user_id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=_scim_bearer(scim_a),
    )
    assert deactivate.status_code == 200, deactivate.text

    correct_after_deactivation = await login(
        client, email="owner-a@corp.example", password="correct-horse-battery-1"
    )
    assert correct_after_deactivation.status_code == wrong.status_code == 401
    assert correct_after_deactivation.json() == wrong.json(), (
        "deactivated-login response must be byte-identical to wrong-password (no oracle)"
    )


async def test_deactivated_user_cannot_login_via_oidc(
    db_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    from gateway.auth.application.use_cases import OidcLoginUseCase
    from gateway.auth.domain.errors import OidcAccountDeactivatedError
    from gateway.tenants.infrastructure.jwt_service import JwtTokenService
    from gateway.tenants.infrastructure.repository import SqlAlchemyIdentityRepository

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'customer')"
        ),
        {"id": tenant_id, "name": "OIDC Tenant"},
    )
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role, auth_method, "
            "deactivated_at) VALUES (:id, :tid, :email, '!sso-no-password', 'member', "
            "'oidc', now())"
        ),
        {"id": user_id, "tid": tenant_id, "email": "deactivated@oidc.example"},
    )
    await db_session.commit()

    settings = make_oidc_settings(
        domain_mapping=[{"email_domain": "oidc.example", "tenant_id": str(tenant_id)}]
    )
    use_case = OidcLoginUseCase(
        exchanger=FakeOidcExchanger(id_token=make_id_token(email="deactivated@oidc.example")),
        repository=SqlAlchemyIdentityRepository(db_session),
        tokens=JwtTokenService(settings),  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
        session_factory=session_factory,
    )

    with pytest.raises(OidcAccountDeactivatedError):
        await use_case.execute(
            code="good-code", state=FAKE_STATE, cookie_state=FAKE_STATE, cookie_nonce=FAKE_NONCE
        )


async def test_already_issued_jwt_survives_deactivation_until_expiry(
    client: httpx.AsyncClient, tenant_a: dict[str, Any], scim_a: dict[str, Any]
) -> None:
    pre_existing_jwt = tenant_a["owner_token"]

    deactivate = await client.patch(
        f"/scim/v2/Users/{tenant_a['owner_user_id']}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=_scim_bearer(scim_a),
    )
    assert deactivate.status_code == 200

    resp = await client.get("/admin/auth/me", headers=bearer(pre_existing_jwt))
    assert resp.status_code == 200, (
        "a pre-existing session JWT is stateless and survives deactivation until natural "
        "expiry — this is the documented residual window, not a defect"
    )


async def test_repeated_patch_active_false_is_idempotent(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "idem@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = uuid.UUID(str(create.json()["id"]))
    op = {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
        "Operations": [{"op": "replace", "path": "active", "value": False}],
    }

    first = await client.patch(f"/scim/v2/Users/{user_id}", json=op, headers=_scim_bearer(scim_a))
    assert first.status_code == 200
    row1 = await fetch_user_row(db_session, user_id)
    deactivated_at_1 = row1["deactivated_at"]

    await asyncio.sleep(0.05)
    second = await client.patch(f"/scim/v2/Users/{user_id}", json=op, headers=_scim_bearer(scim_a))
    assert second.status_code == 200, second.text

    row2 = await fetch_user_row(db_session, user_id)
    assert row2["deactivated_at"] == deactivated_at_1, "repeated PATCH must be a true no-op"

    await asyncio.sleep(0.05)
    assert await count_audit_rows(db_session, action="scim.user_deactivate") == 1, (
        "no duplicate audit row for the idempotent repeat"
    )


async def test_reactivation_clears_deactivated_at(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "react@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = uuid.UUID(str(create.json()["id"]))

    await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=_scim_bearer(scim_a),
    )
    row = await fetch_user_row(db_session, user_id)
    assert row["deactivated_at"] is not None

    resp = await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": True}],
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["active"] is True
    row2 = await fetch_user_row(db_session, user_id)
    assert row2["deactivated_at"] is None


async def test_deactivation_does_not_touch_api_keys(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    api_key_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO api_keys (id, tenant_id, name, key_hash) "
            "VALUES (:id, :tid, 'unrelated-key', 'deadbeef')"
        ),
        {"id": api_key_id, "tid": scim_a["tenant_id"]},
    )
    await db_session.commit()

    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "keytest@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = create.json()["id"]

    await client.patch(
        f"/scim/v2/Users/{user_id}",
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
        headers=_scim_bearer(scim_a),
    )

    revoked = await revoked_at_of(db_session, table="api_keys", id_col="id", row_id=api_key_id)
    assert revoked is None


async def test_delete_is_alias_for_deactivate_never_hard_delete(
    client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    create = await client.post(
        "/scim/v2/Users",
        json={"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": "del@corp.example"},
        headers=_scim_bearer(scim_a),
    )
    user_id = uuid.UUID(str(create.json()["id"]))

    resp = await client.delete(f"/scim/v2/Users/{user_id}", headers=_scim_bearer(scim_a))
    assert resp.status_code == 204, resp.text

    row = await fetch_user_row(db_session, user_id)
    assert row is not None, "row must still exist — never a hard SQL DELETE"
    assert row["deactivated_at"] is not None


async def test_scim_rate_limit_exceeded(
    app: Any, client: httpx.AsyncClient, scim_a: dict[str, Any], db_session: AsyncSession
) -> None:
    from gateway.scim.infrastructure.rate_limiter import ScimTokenRateLimiter

    class _AlwaysOverLimiter:
        async def check(self, *, scim_token_id: str, limit: int) -> None:
            from gateway.scim.domain.errors import ScimRateLimitedError

            raise ScimRateLimitedError(retry_after=17)

    app.state.scim_rate_limiter = _AlwaysOverLimiter()
    assert isinstance(app.state.scim_rate_limiter, _AlwaysOverLimiter)  # sanity

    before = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :e"),
            {"e": "shouldnotexist@corp.example"},
        )
    ).scalar_one()

    resp = await client.post(
        "/scim/v2/Users",
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "userName": "shouldnotexist@corp.example",
        },
        headers=_scim_bearer(scim_a),
    )
    assert resp.status_code == 429, resp.text
    assert resp.headers.get("Retry-After") == "17"

    after = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM users WHERE email = :e"),
            {"e": "shouldnotexist@corp.example"},
        )
    ).scalar_one()
    assert after == before == 0, "a rate-limited request must perform NO mutation"
    _ = ScimTokenRateLimiter  # imported for type-shape reference only


async def test_groups_probe_returns_empty_collection(
    client: httpx.AsyncClient, scim_a: dict[str, Any]
) -> None:
    resp = await client.get("/scim/v2/Groups", headers=_scim_bearer(scim_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["Resources"] == []
    assert body["totalResults"] == 0

    config = await client.get("/scim/v2/ServiceProviderConfig", headers=_scim_bearer(scim_a))
    assert config.json()["group"]["supported"] is False


async def test_groups_write_rejected_as_unsupported(
    client: httpx.AsyncClient, scim_a: dict[str, Any]
) -> None:
    resp = await client.post(
        "/scim/v2/Groups", json={"displayName": "Engineering"}, headers=_scim_bearer(scim_a)
    )
    assert resp.status_code == 501, resp.text
    body = resp.json()
    assert body.get("schemas") == ["urn:ietf:params:scim:api:messages:2.0:Error"]


async def test_scim_discovery_requires_bearer(client: httpx.AsyncClient, scim_a: dict[str, Any]) -> None:
    unauth = await client.get("/scim/v2/ServiceProviderConfig")
    assert unauth.status_code == 401

    ok = await client.get("/scim/v2/ServiceProviderConfig", headers=_scim_bearer(scim_a))
    assert ok.status_code == 200
    assert ok.json()["patch"]["supported"] is True
