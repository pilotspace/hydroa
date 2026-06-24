"""Suite-local fixtures for the bandwidth-counter-view tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; API keys are created via
POST /admin/keys. The LIVE bandwidth bucket level/ts are seeded DIRECTLY into the SAME Redis the
handler reads (`app.state.redis_client`) in the exact key format RedisTokenBucket writes:
  - LEVEL: String bandwidth:bucket:{key_id}     (float text, e.g. "150.0", may be negative)
  - TS:    String bandwidth:bucket_ts:{key_id}   (last-refill epoch ms, float text)
key_ids are fresh uuids per created key, so seeded keys never collide across tests.

Member/admin tokens (same tenant) are minted via `app.state.token_service.issue(...)` after a
direct `users` insert — the proven same-tenant-role pattern reused from ratelimit_counter_view.
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
KEYS = "/admin/keys"
BANDWIDTH = "/admin/bandwidth"
PASSWORD = "correct horse battery"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def level_key(key_id: str) -> str:
    return f"bandwidth:bucket:{key_id}"


def ts_key(key_id: str) -> str:
    return f"bandwidth:bucket_ts:{key_id}"


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


async def create_key(client: httpx.AsyncClient, token: str, *, name: str) -> str:
    """Create an API key via POST /admin/keys; return its key_id."""
    resp = await client.post(KEYS, headers=auth(token), json={"name": name})
    assert resp.status_code == 201, f"create key failed: {resp.text}"
    return str(resp.json()["key_id"])


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


async def seed_bandwidth(app: Any, key_id: str, *, level: str, ts_ms: float) -> None:
    """Seed the live bucket level + last-refill ts (the handler GETs both and refill-adjusts)."""
    r = app.state.redis_client
    await r.set(level_key(key_id), level)
    await r.set(ts_key(key_id), str(ts_ms))


async def get_level_raw(app: Any, key_id: str) -> bytes | None:
    """Read the raw persisted level string (for read-only / no-mutation assertions)."""
    return await app.state.redis_client.get(level_key(key_id))
