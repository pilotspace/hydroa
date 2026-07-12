"""Shared fixtures/helpers for scim-provisioning (TASK.md §2/§3 CONTRACT, FROZEN @ v1).

Fixture provenance (suite-local, per this repo's convention — see
tests/superadmin_audit_foundation/conftest.py's own docstring): OIDC fake-exchanger
helpers below are adapted (cosmetic renaming only) from that same suite, which itself
adapted them from tests/sso_oidc/test_sso_oidc.py.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx
import jwt as pyjwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.core.config import Settings
from gateway.tenants.domain.entities import Role

TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"
VALID_PASSWORD = "correct-horse-battery-1"  # >= MIN_PASSWORD_LENGTH (10)

FAKE_ISSUER = "https://fake-idp.test"
FAKE_CLIENT_ID = "test-client-id"
FAKE_NONCE = "test-nonce-abc123"
FAKE_STATE = "test-state-xyz789"
FAKE_SIGNING_KEY = "fake-idp-signing-secret-for-tests"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str, password: str = VALID_PASSWORD
) -> dict[str, Any]:
    """POST /admin/auth/signup; returns {tenant_id, user_id}."""
    resp = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def login(client: httpx.AsyncClient, *, email: str, password: str) -> httpx.Response:
    return await client.post("/admin/auth/login", json={"email": email, "password": password})


def issue_jwt(
    app: Any, *, role: Role, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> str:
    """Issue a JWT with a specific role/tenant_id directly (mirrors
    member_invite_issuance's own _issue_token helper) — for a MEMBER-role permission
    check that needs no real DB-backed user row."""
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        email=f"{role.value}@scimtest.io",
    )
    return token


async def create_scim_token(
    client: httpx.AsyncClient, *, owner_token: str, name: str = "Okta"
) -> dict[str, Any]:
    """POST /admin/scim/tokens with an owner/admin session JWT; returns the full
    response body {id, name, token, created_at}."""
    resp = await client.post("/admin/scim/tokens", json={"name": name}, headers=bearer(owner_token))
    assert resp.status_code == 201, resp.text
    return resp.json()


async def team_members_for_user(db_session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM team_members WHERE user_id = :uid"), {"uid": user_id}
    )
    return int(result.scalar_one())


async def add_user_to_team(
    db_session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID, role: str = "member"
) -> None:
    await db_session.execute(
        text("INSERT INTO team_members (team_id, user_id, role) VALUES (:tid, :uid, :role)"),
        {"tid": team_id, "uid": user_id, "role": role},
    )
    await db_session.commit()


async def create_team(db_session: AsyncSession, *, tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    team_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO teams (id, tenant_id, name) VALUES (:id, :tid, :name)"),
        {"id": team_id, "tid": tenant_id, "name": name},
    )
    await db_session.commit()
    return team_id


async def fetch_user_row(db_session: AsyncSession, user_id: uuid.UUID) -> Any:
    result = await db_session.execute(
        text(
            "SELECT id, tenant_id, email, role, auth_method, deactivated_at "
            "FROM users WHERE id = :id"
        ),
        {"id": user_id},
    )
    return result.mappings().one_or_none()


async def fetch_one_audit_row(db_session: AsyncSession, *, action: str) -> Any:
    result = await db_session.execute(
        text(
            "SELECT tenant_id, actor_user_id, actor_scim_token_id, actor_email, action, "
            "target_type, target_id, result, metadata FROM audit_events "
            "WHERE action = :action ORDER BY created_at DESC LIMIT 1"
        ),
        {"action": action},
    )
    return result.fetchone()


async def count_audit_rows(db_session: AsyncSession, *, action: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM audit_events WHERE action = :action"), {"action": action}
    )
    return int(result.scalar() or 0)


async def revoked_at_of(
    db_session: AsyncSession, *, table: str, id_col: str, row_id: uuid.UUID
) -> Any:
    result = await db_session.execute(
        text(f"SELECT revoked_at FROM {table} WHERE {id_col} = :id"),  # noqa: S608 — fixed test-only table/col names, never user input
        {"id": row_id},
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# OIDC fixtures (adapted from tests/superadmin_audit_foundation/conftest.py)
# ---------------------------------------------------------------------------


class FakeOidcExchanger:
    def __init__(self, *, id_token: str | None = None) -> None:
        self._id_token = id_token
        self.calls: list[dict[str, Any]] = []

    async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
        self.calls.append({"code": code, "redirect_uri": redirect_uri})
        if self._id_token is not None:
            return {
                "id_token": self._id_token,
                "access_token": "fake-access",
                "token_type": "bearer",
            }
        return {}


def make_id_token(*, email: str, exp_offset: int = 3600) -> str:
    now = int(time.time())
    claims = {
        "sub": str(uuid.uuid4()),
        "email": email,
        "iss": FAKE_ISSUER,
        "aud": FAKE_CLIENT_ID,
        "iat": now,
        "exp": now + exp_offset,
        "nonce": FAKE_NONCE,
    }
    return pyjwt.encode(claims, FAKE_SIGNING_KEY, algorithm="HS256")


def make_oidc_settings(*, domain_mapping: list[dict[str, str]]) -> Settings:
    return Settings(
        jwt_secret=TEST_JWT_SECRET,
        oidc_issuer=FAKE_ISSUER,
        oidc_client_id=FAKE_CLIENT_ID,
        oidc_client_secret="test-client-secret-not-real",  # noqa: S106
        oidc_redirect_uri="http://gateway.test/auth/oidc/callback",
        oidc_domain_mapping=json.dumps(domain_mapping),
        environment="test",
    )


@pytest.fixture
def session_factory(app: object) -> async_sessionmaker[AsyncSession]:
    return app.state.sessionmaker  # type: ignore[attr-defined,no-any-return]
