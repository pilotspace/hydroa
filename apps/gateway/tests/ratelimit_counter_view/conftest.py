"""Suite-local fixtures for the ratelimit-counter-view tests (TASK.md §4).

Real Postgres (localhost:5433/gateway_test) + Redis + the root `client`/`db_session`/`app`
fixtures. A tenant+owner is created via the canonical signup flow; API keys are created via
POST /admin/keys (so rpm_limit/tpm_limit persist). The LIVE rate-limit counters are seeded
DIRECTLY into the SAME Redis the handler reads (`app.state.redis_client`, db 9) in the exact
key format the limiter writes:
  - RPM: ZSET  ratelimit:rpm:{key_id}      (ZCARD = current rpm)
  - TPM: String ratelimit:tpm_sum:{key_id}  (GET   = current tpm)
key_ids are fresh uuids per created key, so seeded keys never collide across tests.

Member/admin tokens (same tenant) are minted via `app.state.token_service.issue(...)` after a
direct `users` insert — the proven same-tenant-role pattern reused from upstream_health_view.
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
RATELIMITS = "/admin/ratelimits"
PASSWORD = "correct horse battery"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def rpm_key(key_id: str) -> str:
    return f"ratelimit:rpm:{key_id}"


def tpm_sum_key(key_id: str) -> str:
    return f"ratelimit:tpm_sum:{key_id}"


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


async def create_key(
    client: httpx.AsyncClient,
    token: str,
    *,
    name: str,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
) -> str:
    """Create an API key via POST /admin/keys; return its key_id."""
    body: dict[str, object] = {"name": name}
    if rpm_limit is not None:
        body["rpm_limit"] = rpm_limit
    if tpm_limit is not None:
        body["tpm_limit"] = tpm_limit
    resp = await client.post(KEYS, headers=auth(token), json=body)
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


async def seed_rpm(app: Any, key_id: str, *, count: int) -> None:
    """Seed `count` members into the live RPM ZSET (the handler reads ZCARD)."""
    r = app.state.redis_client
    mapping = {uuid.uuid4().hex: float(1000 + i) for i in range(count)}
    await r.zadd(rpm_key(key_id), mapping)


async def seed_tpm_sum(app: Any, key_id: str, *, value: str) -> None:
    """Seed the live TPM sum string (the handler reads GET + float())."""
    await app.state.redis_client.set(tpm_sum_key(key_id), value)


async def rpm_zcard(app: Any, key_id: str) -> int:
    return int(await app.state.redis_client.zcard(rpm_key(key_id)))


async def active_key_count(session: AsyncSession, tenant_id: str) -> int:
    row = (
        await session.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )
    ).fetchone()
    return int(row[0]) if row else 0
