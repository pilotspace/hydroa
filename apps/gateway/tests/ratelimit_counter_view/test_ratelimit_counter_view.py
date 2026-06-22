"""RED suite for ratelimit-counter-view (TASK.md §4).

GET /admin/ratelimits is a thin, owner/admin-scoped, READ-ONLY view that returns the CURRENT
per-key rpm/tpm consumption (live Redis counters) vs the configured limits, for the caller's
tenant only. Frozen contract (§3):
  - 200 -> { keys:[{key_id, name, rpm_limit:int|null, tpm_limit:int|null,
                     rpm_current:int|null, tpm_current:float|null}] }
  - rpm_current = ZCARD ratelimit:rpm:{key_id}; tpm_current = float(GET ratelimit:tpm_sum:{key_id});
    0 / 0.0 when the key is unused (Redis up); null when Redis is unavailable.
  - tenant-scoped via list_by_tenant — never another tenant's key; member -> 403; missing bearer -> 401.
  - READ-ONLY: never writes a Redis key or an api_keys row.

RED before BUILD: the route does not exist yet, so success cases get 404 — the honest
missing-implementation red.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    RATELIMITS,
    active_key_count,
    auth,
    create_key,
    mint_role_token,
    rpm_zcard,
    seed_rpm,
    seed_tpm_sum,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _entry(body: dict[str, Any], key_id: str) -> dict[str, Any]:
    matches = [k for k in body["keys"] if k["key_id"] == key_id]
    assert matches, f"key {key_id} not in keys: {body}"
    return matches[0]


# ---------------------------------------------------------------------------
# Owner sees live counters vs limits for each tenant key
# ---------------------------------------------------------------------------
async def test_owner_sees_counters_vs_limits(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    token, _tid = await signup_tenant(client, tenant_name="RL A", email="a@rl.io")
    key_id = await create_key(client, token, name="key-a", rpm_limit=60, tpm_limit=1000)
    await seed_rpm(app, key_id, count=3)
    await seed_tpm_sum(app, key_id, value="150.0")

    resp = await client.get(RATELIMITS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _entry(resp.json(), key_id)
    assert entry["rpm_current"] == 3
    assert entry["rpm_limit"] == 60
    assert entry["tpm_current"] == 150.0
    assert entry["tpm_limit"] == 1000
    assert entry["name"] == "key-a"


# ---------------------------------------------------------------------------
# A key with no traffic reads zero (Redis up, key simply unused)
# ---------------------------------------------------------------------------
async def test_unused_key_reads_zero(client: Any, db_session: AsyncSession, app: Any) -> None:
    token, _tid = await signup_tenant(client, tenant_name="RL Z", email="z@rl.io")
    key_id = await create_key(client, token, name="key-z", rpm_limit=10, tpm_limit=500)

    resp = await client.get(RATELIMITS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _entry(resp.json(), key_id)
    assert entry["rpm_current"] == 0
    assert entry["tpm_current"] == 0.0


# ---------------------------------------------------------------------------
# Only the caller's tenant keys appear (cross-tenant isolation)
# ---------------------------------------------------------------------------
async def test_only_callers_tenant_keys(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    token_t, _tid_t = await signup_tenant(client, tenant_name="RL T", email="t@rl.io")
    token_u, _tid_u = await signup_tenant(client, tenant_name="RL U", email="u@rl.io")
    key_t = await create_key(client, token_t, name="key-t", rpm_limit=60)
    key_u = await create_key(client, token_u, name="key-u", rpm_limit=60)
    await seed_rpm(app, key_t, count=2)
    await seed_rpm(app, key_u, count=5)

    resp = await client.get(RATELIMITS, headers=auth(token_t))

    assert resp.status_code == 200, resp.text
    ids = {k["key_id"] for k in resp.json()["keys"]}
    assert key_t in ids
    assert key_u not in ids, "another tenant's key leaked into the response"


# ---------------------------------------------------------------------------
# Read-only — the GET never mutates Redis counters or api_keys
# ---------------------------------------------------------------------------
async def test_read_only_no_mutation(client: Any, db_session: AsyncSession, app: Any) -> None:
    token, tid = await signup_tenant(client, tenant_name="RL RO", email="ro@rl.io")
    key_id = await create_key(client, token, name="key-ro", rpm_limit=60)
    await seed_rpm(app, key_id, count=3)
    keys_before = await active_key_count(db_session, tid)

    resp = await client.get(RATELIMITS, headers=auth(token))
    assert resp.status_code == 200, resp.text

    assert await rpm_zcard(app, key_id) == 3, "the GET mutated the RPM counter"
    assert await active_key_count(db_session, tid) == keys_before


# ---------------------------------------------------------------------------
# Redis unavailable -> counters null, still 200 (design-for-failure / fail-open)
# ---------------------------------------------------------------------------
async def test_redis_unavailable_counters_null(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    token, _tid = await signup_tenant(client, tenant_name="RL DOWN", email="down@rl.io")
    key_id = await create_key(client, token, name="key-down", rpm_limit=60, tpm_limit=1000)
    # Simulate Redis being unavailable for the read path.
    app.state.redis_client = None

    resp = await client.get(RATELIMITS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _entry(resp.json(), key_id)
    assert entry["rpm_current"] is None
    assert entry["tpm_current"] is None
    # limits are from the DB, still shown
    assert entry["rpm_limit"] == 60
    assert entry["tpm_limit"] == 1000


# ---------------------------------------------------------------------------
# Admin (non-owner) is allowed
# ---------------------------------------------------------------------------
async def test_admin_allowed(client: Any, db_session: AsyncSession, app: Any) -> None:
    owner, tid = await signup_tenant(client, tenant_name="RL AD", email="ad-owner@rl.io")
    await create_key(client, owner, name="key-ad", rpm_limit=60)
    admin_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="ad-admin@rl.io"
    )

    resp = await client.get(RATELIMITS, headers=auth(admin_jwt))

    assert resp.status_code == 200, resp.text
    assert isinstance(resp.json()["keys"], list)
    assert len(resp.json()["keys"]) == 1


# ---------------------------------------------------------------------------
# Member is forbidden (403) and nothing is mutated
# ---------------------------------------------------------------------------
async def test_member_forbidden(client: Any, db_session: AsyncSession, app: Any) -> None:
    owner, tid = await signup_tenant(client, tenant_name="RL M", email="m-owner@rl.io")
    key_id = await create_key(client, owner, name="key-m", rpm_limit=60)
    await seed_rpm(app, key_id, count=4)
    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="m-member@rl.io"
    )

    resp = await client.get(RATELIMITS, headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await rpm_zcard(app, key_id) == 4


# ---------------------------------------------------------------------------
# Missing bearer is unauthorized (401)
# ---------------------------------------------------------------------------
async def test_missing_bearer_unauthorized(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    resp = await client.get(RATELIMITS)

    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Revoked keys are excluded (guards the WHERE revoked_at IS NULL clause) — refute EG-2
# ---------------------------------------------------------------------------
async def test_revoked_key_excluded(client: Any, db_session: AsyncSession, app: Any) -> None:
    token, _tid = await signup_tenant(client, tenant_name="RL REV", email="rev@rl.io")
    active = await create_key(client, token, name="key-active", rpm_limit=60)
    revoked = await create_key(client, token, name="key-revoked", rpm_limit=60)
    drevoke = await client.delete(f"/admin/keys/{revoked}", headers=auth(token))
    assert drevoke.status_code in (200, 204), drevoke.text

    resp = await client.get(RATELIMITS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    ids = {k["key_id"] for k in resp.json()["keys"]}
    assert active in ids
    assert revoked not in ids, "a revoked key leaked into the live rate-limit view"
