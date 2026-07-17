"""Shared fixtures/helpers for billing-owner-of-record (TASK.md §2 SCENARIOS + §3
CONTRACT, FROZEN @ v1).

DB-touching tests use the root conftest `client` + `db_session` + `app` fixtures
(drop/create per test, real Postgres) — this task's ORM column/CHECK (TenantRow.
billing_owner_user_id, ck_tenants_platform_no_billing_owner) is exercised via
`Base.metadata.create_all`, the migration's own backfill is exercised separately
under tests/migrations/.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

VALID_PASSWORD = "correct-horse-battery-1"  # >= MIN_PASSWORD_LENGTH (10)


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


async def login(client: httpx.AsyncClient, *, email: str, password: str = VALID_PASSWORD) -> str:
    resp = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return str(resp.json()["access_token"])


def issue_jwt(
    app: Any,
    *,
    role: Role,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    email: str | None = None,
) -> str:
    """Issue a JWT with a specific role/tenant_id/user_id directly — no DB user row
    needed for pure authz-gate tests (mirrors scim_provisioning's own issue_jwt)."""
    token, _ = app.state.token_service.issue(
        user_id=user_id or uuid.uuid4(),
        tenant_id=tenant_id,
        role=role,
        email=email or f"{role.value}@billingownertest.io",
    )
    return str(token)


async def insert_user(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    email: str,
    role: str,
    deactivated: bool = False,
) -> uuid.UUID:
    """Insert a users row directly (mirrors test_users_role.py's own seeding pattern)."""
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO users (id, tenant_id, email, password_hash, role, deactivated_at)
            VALUES (:id, :tid, :email, '$argon2id$v=dummy', :role, :deactivated_at)
            """
        ),
        {
            "id": user_id,
            "tid": tenant_id,
            "email": email,
            "role": role,
            "deactivated_at": datetime.now(UTC) if deactivated else None,
        },
    )
    await db_session.commit()
    return user_id


async def set_billing_owner(
    db_session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None
) -> None:
    await db_session.execute(
        text("UPDATE tenants SET billing_owner_user_id = :uid WHERE id = :tid"),
        {"uid": user_id, "tid": tenant_id},
    )
    await db_session.commit()


async def get_billing_owner_user_id(
    db_session: AsyncSession, tenant_id: uuid.UUID
) -> uuid.UUID | None:
    row = await db_session.execute(
        text("SELECT billing_owner_user_id FROM tenants WHERE id = :tid"), {"tid": tenant_id}
    )
    return row.scalar_one()


async def get_user_state(db_session: AsyncSession, user_id: uuid.UUID) -> tuple[str, Any]:
    """Returns (role, deactivated_at)."""
    row = await db_session.execute(
        text("SELECT role, deactivated_at FROM users WHERE id = :uid"), {"uid": user_id}
    )
    result = row.one()
    return result[0], result[1]


async def team_members_for_user(db_session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM team_members WHERE user_id = :uid"), {"uid": user_id}
    )
    return int(result.scalar_one())


async def create_team(db_session: AsyncSession, *, tenant_id: uuid.UUID, name: str) -> uuid.UUID:
    team_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO teams (id, tenant_id, name) VALUES (:id, :tid, :name)"),
        {"id": team_id, "tid": tenant_id, "name": name},
    )
    await db_session.commit()
    return team_id


async def add_user_to_team(
    db_session: AsyncSession, *, team_id: uuid.UUID, user_id: uuid.UUID, role: str = "member"
) -> None:
    await db_session.execute(
        text("INSERT INTO team_members (team_id, user_id, role) VALUES (:tid, :uid, :role)"),
        {"tid": team_id, "uid": user_id, "role": role},
    )
    await db_session.commit()


async def create_scim_token(
    client: httpx.AsyncClient, *, owner_token: str, name: str = "Okta"
) -> str:
    resp = await client.post("/admin/scim/tokens", json={"name": name}, headers=bearer(owner_token))
    assert resp.status_code == 201, resp.text
    return str(resp.json()["token"])


@pytest.fixture
async def platform_tenant_id(db_session: AsyncSession) -> uuid.UUID:
    """Resolve the platform tenant id; seed one directly when the fast create_all test
    schema has not run the platform-tenant-seed migration (mirrors
    tests/cross_tenant_keys_members's own fixture of the same name)."""
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
