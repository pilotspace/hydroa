"""Suite-local fixtures for routing-config-write (TASK.md §4).

Real Postgres + the root `client`/`db_session`/`app` fixtures. A tenant+owner is created
via the canonical signup flow; a same-tenant member token is minted via
`app.state.token_service.issue(...)` after a direct `users` insert (the proven role pattern
from the alerts-events-viewer / reconciliation suites).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ROUTING = "/admin/routing"
PASSWORD = "correct horse battery"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def signup_tenant(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> tuple[str, str]:
    """Sign up a tenant+owner; return (owner_jwt, tenant_id)."""
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": PASSWORD}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def mint_role_token(
    app: Any,
    session: AsyncSession,
    *,
    tenant_id: str,
    role: Role,
    email: str,
) -> str:
    """Insert a same-tenant user with `role` and mint a JWT for it."""
    user_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', :role)"
        ),
        {"id": user_id, "tid": tenant_id, "email": email, "role": str(role)},
    )
    await session.commit()
    token, _ = app.state.token_service.issue(
        user_id=uuid.UUID(user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=email,
    )
    return str(token)


async def routing_row_count(app: Any) -> int:
    """Count rows in the routing_config singleton table."""
    async with app.state.sessionmaker() as session:
        row = (await session.execute(text("SELECT COUNT(*) FROM routing_config"))).fetchone()
        return int(row[0]) if row else 0
