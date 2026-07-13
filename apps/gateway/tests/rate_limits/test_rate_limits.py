"""Failing-first (RED) suite for rate-limits (contract DRAFT, TASK.md §4).

One test per scenario in TASK.md §2 SCENARIOS.

Right-reason red targets:
  - rpm_limit / tpm_limit fields on POST /admin/keys → KeyError or AssertionError
    because the schema / use-case does not accept / return them yet.
  - PATCH /admin/keys/{id} with rpm_limit / tpm_limit → fields absent from response
    because the schema does not extend PatchKeyRequest yet.
  - Enforcement tests (RPM exceeded, TPM exceeded) → 200 instead of 429 because
    CompletionUseCase has no rate-limit check yet.
  - Retry-After header absent on 429 → AssertionError because header is not set.
  - Concurrent burst test → over-admission because no Lua atomic check exists.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 (db index 8, flushed per test via redis_client fixture)
    (db index 8 chosen to avoid collision with key_governance suite on index 9)
  - httpx.ASGITransport (no network — same as existing suites)
"""

from __future__ import annotations

import asyncio
import datetime
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env

# ---------------------------------------------------------------------------
# Constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
ADMIN_KEYS = "/admin/keys"
COMPLETIONS = "/v1/chat/completions"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"

_WINDOW_MS = 60_000  # 60-second window per §3 CONTRACT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; full body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _rpm_key(key_id: str) -> str:
    return f"ratelimit:rpm:{key_id}"


def _tpm_key(key_id: str) -> str:
    return f"ratelimit:tpm:{key_id}"


def _tpm_sum_key(key_id: str) -> str:
    return f"ratelimit:tpm_sum:{key_id}"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Minimal non-streaming fake — reused from proxy/budgets/key_governance test pattern."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-rl-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-rl1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


class BrokenRedis:
    """Raises on every call — simulates Redis being down for rate-limiter and budget guard."""

    async def get(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def set(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def zadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def zcard(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def zrange(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def zremrangebyscore(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def expire(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xgroup_create(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xreadgroup(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def xack(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def ping(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("Redis unavailable (BrokenRedis fake)")

    async def aclose(self) -> None:
        pass

    def register_script(self, script: str) -> Any:
        # Return a fake callable that always raises so the rate limiter falls through to
        # the fail-open path — this is the correct behavior under BrokenRedis.
        class _BrokenScript:
            async def __call__(self_inner, keys: list[str], args: list[Any]) -> Any:
                raise ConnectionError("Redis unavailable (BrokenRedis fake)")
        return _BrokenScript()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 8; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(
        _redis_env.TEST_REDIS_URL, decode_responses=False
    )
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model for proxy tests."""
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o"},
    )
    await db_session.commit()
    return model_id


def _wire_rate_limiter(app: Any, redis: Any) -> None:
    """Wire a real RedisLuaRateLimiter to app.state.rate_limiter.

    RED phase: this import will fail with ImportError because
    gateway.rate_limits.infrastructure.redis_lua_limiter does not exist yet.
    That is the correct right-reason failure for enforcement tests.
    """
    from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter  # type: ignore[import]

    app.state.rate_limiter = RedisLuaRateLimiter(redis=redis)


def _wire_budget_guard(app: Any, redis: Any) -> None:
    """Wire a real RedisBudgetGuard backed by the given Redis client."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    app.state.budget_guard = RedisBudgetGuard(
        redis=redis,
        session_factory=app.state.sessionmaker,
    )


# ===========================================================================
# §2 Scenario: Create key with rpm_limit and tpm_limit persisted and echoed
# ===========================================================================


async def test_create_key_with_rpm_and_tpm_limits(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """POST /admin/keys with rpm_limit=60, tpm_limit=100000 → 201 + fields echoed."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo1", email="owner1@rateco.io")

    resp = await client.post(
        ADMIN_KEYS,
        json={"name": "rate-key", "rpm_limit": 60, "tpm_limit": 100000},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()

    # rpm_limit and tpm_limit must be present and correct in creation response
    assert body.get("rpm_limit") == 60, f"expected rpm_limit=60, got {body.get('rpm_limit')}"
    assert body.get("tpm_limit") == 100000, (
        f"expected tpm_limit=100000, got {body.get('tpm_limit')}"
    )

    # GET /admin/keys must echo the same fields
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    assert list_resp.status_code == 200
    items: list[dict[str, Any]] = list_resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["key_id"] == body["key_id"]
    assert item.get("rpm_limit") == 60, f"GET echoed rpm_limit={item.get('rpm_limit')}"
    assert item.get("tpm_limit") == 100000, f"GET echoed tpm_limit={item.get('tpm_limit')}"

    # DB row must carry the integer values
    row = (
        await db_session.execute(
            text("SELECT rpm_limit, tpm_limit FROM api_keys WHERE id = :id"),
            {"id": body["key_id"]},
        )
    ).one()
    assert row[0] == 60, f"DB rpm_limit={row[0]}"
    assert row[1] == 100000, f"DB tpm_limit={row[1]}"


# ===========================================================================
# §2 Scenario: Create key with no rate-limit fields defaults to null
# ===========================================================================


async def test_create_key_defaults_rpm_tpm_to_null(
    client: httpx.AsyncClient,
) -> None:
    """POST /admin/keys bare → 201 with rpm_limit=null, tpm_limit=null."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo2", email="owner2@rateco.io")

    resp = await client.post(
        ADMIN_KEYS, json={"name": "bare-key"}, headers=auth_jwt(jwt)
    )

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body.get("rpm_limit") is None, (
        f"expected rpm_limit=null, got {body.get('rpm_limit')}"
    )
    assert body.get("tpm_limit") is None, (
        f"expected tpm_limit=null, got {body.get('tpm_limit')}"
    )

    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    items = list_resp.json()
    assert items[0].get("rpm_limit") is None
    assert items[0].get("tpm_limit") is None


# ===========================================================================
# §2 Scenario: PATCH updates rpm_limit and tpm_limit on active key
# ===========================================================================


async def test_patch_key_updates_rpm_and_tpm(
    client: httpx.AsyncClient,
) -> None:
    """PATCH /admin/keys/{key_id} with rpm_limit=30, tpm_limit=50000 → 200 + updated."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo3", email="owner3@rateco.io")
    created = (
        await client.post(ADMIN_KEYS, json={"name": "patchable"}, headers=auth_jwt(jwt))
    ).json()
    key_id = created["key_id"]

    resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"rpm_limit": 30, "tpm_limit": 50000},
        headers=auth_jwt(jwt),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("rpm_limit") == 30, f"expected rpm_limit=30, got {body.get('rpm_limit')}"
    assert body.get("tpm_limit") == 50000, (
        f"expected tpm_limit=50000, got {body.get('tpm_limit')}"
    )

    # GET echoes the updated values
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    items = list_resp.json()
    item = next(i for i in items if i["key_id"] == key_id)
    assert item.get("rpm_limit") == 30
    assert item.get("tpm_limit") == 50000


# ===========================================================================
# §2 Scenario: RPM limit exceeded → 429 + Retry-After; sibling unaffected
# ===========================================================================


async def test_rpm_limit_exceeded_returns_429_with_retry_after(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key with rpm_limit=2: first 2 requests → 200; 3rd → 429 ERR_RATE_LIMITED + Retry-After."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo4", email="owner4@rateco.io")

    # Create key with rpm_limit=2
    created = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "rpm-limited", "rpm_limit": 2},
            headers=auth_jwt(jwt),
        )
    ).json()
    key = created["key"]

    # Wire real rate limiter (will fail RED due to missing module)
    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "hello"}],
    }

    # Request 1 — admitted
    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
    assert r1.status_code == 200, f"request 1 expected 200, got {r1.status_code}: {r1.text}"

    # Request 2 — admitted (limit = 2)
    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
    assert r2.status_code == 200, f"request 2 expected 200, got {r2.status_code}: {r2.text}"

    # Request 3 — window full → 429
    r3 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
    body = assert_problem(r3, 429, "ERR_RATE_LIMITED")

    # Retry-After must be present and be a positive integer
    retry_after = r3.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header missing on 429"
    assert int(retry_after) >= 1, f"Retry-After must be >= 1, got {retry_after}"

    # Upstream was called exactly twice (the two admitted requests)
    assert upstream.calls == 2, f"expected upstream.calls==2, got {upstream.calls}"

    # body detail should reference this key, not expose other keys
    detail = body.get("detail", "")
    assert created["key_id"] in detail or "RPM" in detail or "rpm" in detail, (
        f"detail should reference key or RPM: {detail}"
    )


# ===========================================================================
# §2 Scenario: Sibling key unaffected by rpm_limit of first key
# ===========================================================================


async def test_sibling_key_unaffected_by_rpm_limit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """key_A rpm_limit=1 (window full) → 429; key_B rpm_limit=null → 200 unaffected."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo5", email="owner5@rateco.io")

    key_a_data = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "key-a", "rpm_limit": 1},
            headers=auth_jwt(jwt),
        )
    ).json()
    key_b_data = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "key-b"},  # no rpm_limit
            headers=auth_jwt(jwt),
        )
    ).json()

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "hello"}],
    }

    # Fill key_A window (1 request = limit reached)
    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_a_data["key"]))
    assert r1.status_code == 200, f"key_A first request: {r1.text}"

    # key_A is now over limit
    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_a_data["key"]))
    assert r2.status_code == 429, f"key_A should be rate-limited, got {r2.status_code}"

    # key_B (no limit) must be unaffected — same tenant, different key
    r3 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_b_data["key"]))
    assert r3.status_code == 200, (
        f"key_B should be unaffected (no limit), got {r3.status_code}: {r3.text}"
    )


# ===========================================================================
# §2 Scenario: TPM limit exceeded → 429 + Retry-After
# ===========================================================================


async def test_tpm_limit_exceeded_returns_429_with_retry_after(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key with tpm_limit=100, pre-seeded TPM sum at 95 → pre-flight sees >= limit → 429."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo6", email="owner6@rateco.io")

    created = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "tpm-limited", "tpm_limit": 100},
            headers=auth_jwt(jwt),
        )
    ).json()
    key = created["key"]
    key_id = created["key_id"]

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    # Pre-seed the TPM sum counter so pre-flight check sees window >= tpm_limit
    # The rate limiter reads ratelimit:tpm_sum:{key_id} for the current window total.
    # We set it to 100 (== tpm_limit) so any new request is immediately rejected.
    now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    tpm_sum_redis_key = _tpm_sum_key(key_id)
    await redis_client.set(tpm_sum_redis_key, b"100")
    # Also pre-seed the ZSET with a single entry so Retry-After can be computed from oldest_ms
    tpm_redis_key = _tpm_key(key_id)
    await redis_client.zadd(tpm_redis_key, {f"100:{key_id[:8]}": now_ms - 30000})
    await redis_client.expire(tpm_redis_key, 61)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(key),
    )

    assert_problem(resp, 429, "ERR_RATE_LIMITED")

    # Retry-After must be present
    retry_after = resp.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header missing on TPM 429"
    assert int(retry_after) >= 1, f"Retry-After must be >= 1, got {retry_after}"

    # Upstream must NOT have been called
    assert upstream.calls == 0, (
        f"upstream should not be called on TPM 429; got {upstream.calls} calls"
    )


# ===========================================================================
# §2 Scenario: Redis down → fail-open (rate-limited key passes through)
# ===========================================================================


async def test_redis_down_fails_open(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
) -> None:
    """key with rpm_limit=1 + BrokenRedis → request admitted (fail-open), not 429."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo7", email="owner7@rateco.io")

    created = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "fail-open-key", "rpm_limit": 1},
            headers=auth_jwt(jwt),
        )
    ).json()
    key = created["key"]

    broken = BrokenRedis()

    # Wire broken Redis for BOTH rate limiter and budget guard so neither blocks the request
    # Note: _wire_rate_limiter will ImportError RED; test still validates fail-open intent.
    # The test explicitly checks that even with rpm_limit=1 and Redis broken,
    # the request must pass through (fail-open per §1 M14).
    try:
        from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter  # type: ignore[import]
        app.state.rate_limiter = RedisLuaRateLimiter(redis=broken)
    except ImportError:
        # RED phase: module not yet implemented; skip wiring
        pass

    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    app.state.budget_guard = RedisBudgetGuard(
        redis=broken,
        session_factory=app.state.sessionmaker,
    )

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(key),
    )

    assert resp.status_code == 200, (
        f"Redis down should fail-open (admit), got {resp.status_code}: {resp.text}"
    )
    assert upstream.calls == 1, (
        f"upstream should be called once on fail-open; got {upstream.calls}"
    )


# ===========================================================================
# §2 Scenario: Null limits → never rate-limited
# ===========================================================================


async def test_null_limits_never_rate_limited(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Key with rpm_limit=null, tpm_limit=null → 5 sequential requests all succeed."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo8", email="owner8@rateco.io")

    created = (
        await client.post(ADMIN_KEYS, json={"name": "unlimited"}, headers=auth_jwt(jwt))
    ).json()
    key = created["key"]

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "hi"}],
    }

    for i in range(5):
        resp = await client.post(COMPLETIONS, json=payload, headers=auth_key(key))
        assert resp.status_code == 200, (
            f"null-limit key request {i + 1} expected 200, got {resp.status_code}: {resp.text}"
        )

    assert upstream.calls == 5, f"expected 5 upstream calls, got {upstream.calls}"


# ===========================================================================
# §2 Scenario: 429 does not expose sibling key state (R7 — no cross-key leak)
# ===========================================================================


async def test_429_does_not_expose_sibling_key_state(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """429 body and headers for key_F must not reference key_G's state."""
    jwt, _ = await signup_and_login(client, tenant_name="RateCo9", email="owner9@rateco.io")

    key_f_data = (
        await client.post(
            ADMIN_KEYS, json={"name": "key-f", "rpm_limit": 1}, headers=auth_jwt(jwt)
        )
    ).json()
    key_g_data = (
        await client.post(
            ADMIN_KEYS, json={"name": "key-g", "rpm_limit": 10}, headers=auth_jwt(jwt)
        )
    ).json()

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "hello"}],
    }

    # Exhaust key_F window
    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_f_data["key"]))
    assert r1.status_code == 200

    # Pre-seed key_G partial window (5 of 10 slots used) in Redis
    now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    rpm_key_g = _rpm_key(key_g_data["key_id"])
    for i in range(5):
        await redis_client.zadd(rpm_key_g, {f"entry_{i}": now_ms - (i * 1000)})
    await redis_client.expire(rpm_key_g, 61)

    # key_F should be rate-limited
    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_f_data["key"]))
    assert r2.status_code == 429, f"key_F should be 429, got {r2.status_code}"

    resp_body = r2.json()
    resp_text = str(resp_body)

    # No reference to key_G's id in body or headers
    key_g_id = key_g_data["key_id"]
    assert key_g_id not in resp_text, (
        f"429 body must not expose key_G id ({key_g_id}): {resp_text}"
    )
    for header_name, header_val in r2.headers.items():
        assert key_g_id not in header_val, (
            f"429 header {header_name}={header_val!r} must not reference key_G"
        )


# ===========================================================================
# §2 Scenarios: Input validation rejections (R1-R4)
# ===========================================================================


async def test_rpm_limit_zero_rejected(client: httpx.AsyncClient) -> None:
    """POST /admin/keys with rpm_limit=0 → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="RateVal1", email="val1@rate.io")

    resp = await client.post(
        ADMIN_KEYS, json={"name": "zero-rpm", "rpm_limit": 0}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")

    # No key row must have been created
    list_resp = await client.get(ADMIN_KEYS, headers=auth_jwt(jwt))
    assert list_resp.json() == [], "No key should be created on validation error"


async def test_rpm_limit_negative_rejected(client: httpx.AsyncClient) -> None:
    """POST /admin/keys with rpm_limit=-5 → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="RateVal2", email="val2@rate.io")

    resp = await client.post(
        ADMIN_KEYS, json={"name": "neg-rpm", "rpm_limit": -5}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_tpm_limit_zero_rejected(client: httpx.AsyncClient) -> None:
    """POST /admin/keys with tpm_limit=0 → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="RateVal3", email="val3@rate.io")

    resp = await client.post(
        ADMIN_KEYS, json={"name": "zero-tpm", "tpm_limit": 0}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_tpm_limit_negative_rejected(client: httpx.AsyncClient) -> None:
    """POST /admin/keys with tpm_limit=-100 → 422 ERR_PAYLOAD_INVALID."""
    jwt, _ = await signup_and_login(client, tenant_name="RateVal4", email="val4@rate.io")

    resp = await client.post(
        ADMIN_KEYS, json={"name": "neg-tpm", "tpm_limit": -100}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# §2 Scenario: Governance checks fire before rate limits (M10)
# ===========================================================================


async def test_expired_key_gets_401_not_429(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Expired key with rpm_limit=100 (fresh window) → 401 ERR_AUTH_KEY_EXPIRED, not 429.

    Enforcement order: expiry → allowlist → budget → RPM → TPM → upstream.
    Governance (expiry) fires before rate limits.
    """
    jwt, _ = await signup_and_login(client, tenant_name="RateCo10", email="owner10@rateco.io")

    # Create an expired key with a generous rpm_limit (window is empty)
    past = (
        datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=1)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    created = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "expired-limited", "expires_at": past, "rpm_limit": 100},
            headers=auth_jwt(jwt),
        )
    ).json()
    key = created["key"]

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": [{"role": "user", "content": "hi"}]},
        headers=auth_key(key),
    )

    # Must get 401 from expiry, NOT 429 from rate limiter
    assert_problem(resp, 401, "ERR_AUTH_KEY_EXPIRED")
    assert upstream.calls == 0, "upstream must not be called for expired key"


# ===========================================================================
# §2 Scenario: Concurrent burst — exactly limit admitted (atomicity proof, A6)
# ===========================================================================


async def test_concurrent_burst_admits_exactly_limit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """10 concurrent completions with rpm_limit=5 → exactly 5 admitted, 5 rejected.

    This test validates the atomicity guarantee of the Lua sliding-window check:
    no over-admission beyond the configured limit under concurrent burst.

    Concurrency bound documented in §1 A6:
      admitted ≤ rpm_limit (exact equality expected in a fresh window with N > limit).
    """
    jwt, _ = await signup_and_login(
        client, tenant_name="RateCo11", email="owner11@rateco.io"
    )

    created = (
        await client.post(
            ADMIN_KEYS,
            json={"name": "burst-key", "rpm_limit": 5},
            headers=auth_jwt(jwt),
        )
    ).json()
    key = created["key"]

    _wire_rate_limiter(app, redis_client)
    _wire_budget_guard(app, redis_client)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "burst"}],
    }
    headers = auth_key(key)

    # Fire 10 concurrent requests
    tasks = [
        client.post(COMPLETIONS, json=payload, headers=headers) for _ in range(10)
    ]
    responses = await asyncio.gather(*tasks, return_exceptions=True)

    statuses = [
        r.status_code if isinstance(r, httpx.Response) else -1 for r in responses
    ]
    admitted = sum(1 for s in statuses if s == 200)
    rejected = sum(1 for s in statuses if s == 429)

    # No over-admission — atomic Lua must enforce the limit exactly
    assert admitted <= 5, (
        f"admitted ({admitted}) must not exceed rpm_limit (5) — over-admission detected"
    )
    assert rejected >= 5, (
        f"rejected ({rejected}) must be >= 5 (10 - limit); got admitted={admitted}"
    )
    # In a fresh window, exactly 5 should be admitted (no prior entries)
    assert admitted == 5, (
        f"expected exactly 5 admitted in fresh window, got {admitted}; "
        f"statuses: {statuses}"
    )
    assert rejected == 5, f"expected exactly 5 rejected, got {rejected}"

    # Retry-After present on all 429 responses
    for r in responses:
        if isinstance(r, httpx.Response) and r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            assert retry_after is not None, "Retry-After header missing on concurrent 429"
            assert int(retry_after) >= 1
