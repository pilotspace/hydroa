"""RED suite for bandwidth-counter-view (TASK.md §4, §3 FROZEN @ v1).

GET /admin/bandwidth is a thin, owner/admin-scoped, READ-ONLY view returning the per-key
REFILL-ADJUSTED bandwidth bucket level vs the configured capacity, for the caller's tenant only.
Frozen contract (§3):
  - 200 -> { enabled:bool, rate_per_sec:int, burst:int,
             keys:[{key_id, name, level:int|null}] }
  - level = int(min(burst, float(stored) + rate*max(0, now_ms-float(ts))/1000)) read from
    bandwidth:bucket:{key_id} + bandwidth:bucket_ts:{key_id} (GET only); null when the level key is
    absent, Redis is unavailable, or pacing is disabled (rate<=0).
  - tenant-scoped; member -> 403 ERR_AUTH_FORBIDDEN; missing bearer -> 401 ERR_AUTH_INVALID_TOKEN.
  - READ-ONLY: never writes a Redis key or an api_keys row.

RED before BUILD: the route does not exist yet, so success cases get 404 — the honest
missing-implementation red.
"""

from __future__ import annotations

import time
from typing import Any

from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings
from gateway.tenants.domain.entities import Role

from .conftest import (
    BANDWIDTH,
    auth,
    create_key,
    get_level_raw,
    mint_role_token,
    seed_bandwidth,
    signup_tenant,
)


def _enable(app: Any, *, rate: int, burst: int) -> None:
    """Replace app.state.settings with a copy that turns pacing ON (preserve all other fields)."""
    app.state.settings = app.state.settings.model_copy(
        update={"bandwidth_tokens_per_sec": rate, "bandwidth_burst_tokens": burst}
    )


def _now_ms() -> float:
    return time.time() * 1000.0


def _entry(body: dict[str, Any], key_id: str) -> dict[str, Any]:
    for k in body["keys"]:
        if k["key_id"] == key_id:
            return k
    raise AssertionError(f"key_id {key_id} not in {body['keys']}")


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"want {status}, got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == code, resp.text


# ── owner sees per-key levels vs capacity ────────────────────────────────────
async def test_owner_sees_levels_vs_capacity(client: Any, app: Any) -> None:
    _enable(app, rate=100, burst=200)
    token, _tid = await signup_tenant(client, tenant_name="A", email="a@x.io")
    seeded = await create_key(client, token, name="seeded")
    untouched = await create_key(client, token, name="untouched")
    await seed_bandwidth(app, seeded, level="150.0", ts_ms=_now_ms())  # fresh ts ⇒ ~no refill

    resp = await client.get(BANDWIDTH, headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["rate_per_sec"] == 100
    assert body["burst"] == 200
    assert [k["name"] for k in body["keys"]] == ["seeded", "untouched"]  # created_at ASC
    # rate=100/sec ⇒ ±15 tolerates ~150ms of real HTTP+DB+Redis round-trip latency
    # (a loaded CI runner can exceed a ±1/±10ms budget while still proving no large refill)
    assert 135 <= _entry(body, seeded)["level"] <= 165
    assert _entry(body, untouched)["level"] is None  # never touched ⇒ unknown


# ── refill-adjusted level reflects elapsed time (clamp = deterministic) ───────
async def test_refill_adjusted_level(client: Any, app: Any) -> None:
    _enable(app, rate=100, burst=200)
    token, _tid = await signup_tenant(client, tenant_name="R", email="r@x.io")
    clamped = await create_key(client, token, name="clamped")
    fresh = await create_key(client, token, name="fresh")
    # stored 0, ts 100s ago → 0 + 100*100 = 10000 → clamped to burst 200 (EXACT, latency-proof)
    await seed_bandwidth(app, clamped, level="0.0", ts_ms=_now_ms() - 100_000.0)
    # stored 150, fresh ts → ~150 (proves no spurious refill)
    await seed_bandwidth(app, fresh, level="150.0", ts_ms=_now_ms())

    resp = await client.get(BANDWIDTH, headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert _entry(body, clamped)["level"] == 200  # refill + clamp, exact
    # rate=100/sec ⇒ ±15 tolerates ~150ms of real HTTP+DB+Redis round-trip latency
    # (a loaded CI runner can exceed a ±1/±10ms budget while still proving no large refill)
    assert 135 <= _entry(body, fresh)["level"] <= 165


# ── float / negative persisted level parses without a 500 ────────────────────
async def test_float_negative_level_parses(client: Any, app: Any) -> None:
    _enable(app, rate=100, burst=200)
    token, _tid = await signup_tenant(client, tenant_name="F", email="f@x.io")
    half = await create_key(client, token, name="half")
    debt = await create_key(client, token, name="debt")
    await seed_bandwidth(app, half, level="199.5", ts_ms=_now_ms())
    await seed_bandwidth(app, debt, level="-20.0", ts_ms=_now_ms())

    resp = await client.get(BANDWIDTH, headers=auth(token))
    assert resp.status_code == 200, resp.text  # no 500 from int("199.5")
    body = resp.json()
    assert isinstance(_entry(body, half)["level"], int)
    assert 198 <= _entry(body, half)["level"] <= 200
    assert isinstance(_entry(body, debt)["level"], int)
    assert _entry(body, debt)["level"] <= 0  # over-budget debt shown honestly


# ── disabled pacing reports honestly ─────────────────────────────────────────
async def test_disabled_reports_honestly(client: Any, app: Any) -> None:
    # default settings: bandwidth_tokens_per_sec == 0 (disabled)
    token, _tid = await signup_tenant(client, tenant_name="D", email="d@x.io")
    kid = await create_key(client, token, name="k")
    await seed_bandwidth(app, kid, level="150.0", ts_ms=_now_ms())  # even with data present

    resp = await client.get(BANDWIDTH, headers=auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    assert body["rate_per_sec"] == 0
    assert body["burst"] == 0
    assert _entry(body, kid)["level"] is None  # disabled ⇒ no refill math, null


# ── Redis unavailable → null levels, still 200 ───────────────────────────────
async def test_redis_unavailable_levels_null(client: Any, app: Any) -> None:
    _enable(app, rate=100, burst=200)
    token, _tid = await signup_tenant(client, tenant_name="U", email="u@x.io")
    kid = await create_key(client, token, name="k")
    await seed_bandwidth(app, kid, level="150.0", ts_ms=_now_ms())

    class _BoomRedis:
        async def get(self, *_a: Any, **_k: Any) -> Any:
            # redis-py raises redis.exceptions.ConnectionError (a RedisError) in prod — model
            # that exact arm, not the builtin ConnectionError (which would ride the OSError arm).
            raise RedisConnectionError("redis down")

    app.state.redis_client = _BoomRedis()
    resp = await client.get(BANDWIDTH, headers=auth(token))
    assert resp.status_code == 200, resp.text  # fail-open, never 500
    body = resp.json()
    assert _entry(body, kid)["level"] is None


# ── tenant isolation ─────────────────────────────────────────────────────────
async def test_tenant_isolation(client: Any, app: Any) -> None:
    _enable(app, rate=100, burst=200)
    a_token, _a = await signup_tenant(client, tenant_name="TA", email="ta@x.io")
    b_token, _b = await signup_tenant(client, tenant_name="TB", email="tb@x.io")
    a_key = await create_key(client, a_token, name="a-key")
    b_key = await create_key(client, b_token, name="b-key")
    await seed_bandwidth(app, a_key, level="10.0", ts_ms=_now_ms())
    await seed_bandwidth(app, b_key, level="20.0", ts_ms=_now_ms())

    resp = await client.get(BANDWIDTH, headers=auth(a_token))
    assert resp.status_code == 200, resp.text
    ids = {k["key_id"] for k in resp.json()["keys"]}
    assert a_key in ids
    assert b_key not in ids  # never another tenant's key


# ── read-only, no mutation ───────────────────────────────────────────────────
async def test_read_only_no_mutation(client: Any, app: Any, db_session: AsyncSession) -> None:
    _enable(app, rate=100, burst=200)
    token, tid = await signup_tenant(client, tenant_name="RO", email="ro@x.io")
    kid = await create_key(client, token, name="k")
    await seed_bandwidth(app, kid, level="150.0", ts_ms=_now_ms())
    before_level = await get_level_raw(app, kid)
    before_keys = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :t"), {"t": tid}
        )
    ).scalar()

    await client.get(BANDWIDTH, headers=auth(token))
    await client.get(BANDWIDTH, headers=auth(token))

    assert await get_level_raw(app, kid) == before_level  # GET never wrote the bucket
    after_keys = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM api_keys WHERE tenant_id = :t"), {"t": tid}
        )
    ).scalar()
    assert after_keys == before_keys


# ── member is forbidden (REJECTION) ──────────────────────────────────────────
async def test_member_forbidden(client: Any, app: Any, db_session: AsyncSession) -> None:
    _enable(app, rate=100, burst=200)
    owner, tid = await signup_tenant(client, tenant_name="M", email="owner-m@x.io")
    _ = owner
    member = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="member-m@x.io"
    )
    resp = await client.get(BANDWIDTH, headers=auth(member))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ── admin is allowed ─────────────────────────────────────────────────────────
async def test_admin_allowed(client: Any, app: Any, db_session: AsyncSession) -> None:
    _enable(app, rate=100, burst=200)
    _owner, tid = await signup_tenant(client, tenant_name="AD", email="owner-ad@x.io")
    admin = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="admin-ad@x.io"
    )
    resp = await client.get(BANDWIDTH, headers=auth(admin))
    assert resp.status_code == 200, resp.text


# ── missing bearer is unauthorized (REJECTION) ───────────────────────────────
async def test_missing_bearer_unauthorized(client: Any) -> None:
    resp = await client.get(BANDWIDTH)
    assert resp.status_code == 401
