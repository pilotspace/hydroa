"""Failing-first (RED) suite for response-caching (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  S1 (default off, upstream called twice):
    No X-Cache header when cache_enabled=false. The X-Cache header does not exist yet,
    so asserting its ABSENCE would PASS today — but asserting that the POST /admin/keys
    body includes a `cache_enabled` field fails with AssertionError because the field is
    absent from the current CreateKeyResponse schema. Red reason: AssertionError on
    missing `cache_enabled` in POST /admin/keys 201 response body.

  S2 (key-enabled cache hit):
    asserting upstream.calls == 1 after two requests fails because caching is not
    implemented: upstream.calls == 2. AssertionError: 2 != 1.

  S3 (tenant-enabled cache hit):
    PUT /admin/cache does not exist → 404 or 405. Asserting status == 200 FAILS
    with AssertionError showing 404/405 vs 200.

  S4 (bypass header forces upstream):
    The X-Cache response header does not exist yet → asserting X-Cache == "bypass" FAILS
    with KeyError / AssertionError. Additionally, upstream.calls assertion would fail
    because no cache logic exists (upstream always called).

  S5 (different payload is cache miss):
    X-Cache: miss header absent → AssertionError on header assertion.
    Also upstream.calls count wrong relative to expectations.

  S6 (different tenant, identical payload → miss):
    cache_enabled field absent from CreateKeyResponse → AssertionError during arrange step.

  S7 (upstream 4xx not cached):
    cache_enabled absent from CreateKeyResponse schema → AssertionError during key creation.

  S8 (governance blocks before cache):
    This test relies on cache_enabled being settable. asserting cache_enabled=true in key
    creation response → AssertionError (field absent). Even if bypassed: cache not implemented,
    governance already blocks (this test would pass for the wrong reason without cache_enabled
    field check). The arrange step fails first: cache_enabled absent → AssertionError.

  S9 (streaming bypasses cache entirely):
    Asserting cache_enabled in key creation response → AssertionError (field absent in schema).
    Even if bypassed: streams already bypass (no X-Cache header) so this could pass for the
    wrong reason. We strengthen the assertion: upstream.calls == 2 AND no X-Cache header;
    the cache_enabled field in key creation is the first red gate.

  S10 (Prometheus counter increments):
    MetricsRegistry has no `cache_events_total` counter attribute yet → AttributeError.

  S11 (PATCH key cache_enabled toggle):
    PATCH /admin/keys/{key_id} with {"cache_enabled": true}: the field is not in PatchKeyRequest
    schema → either silently ignored (no effect on DB, assertion on response body fails) or
    validation error. KeyInfoResponse has no cache_enabled field → AssertionError asserting
    response body["cache_enabled"] == True.

  S12 (member role 403 on PUT /admin/cache):
    PUT /admin/cache does not exist → 404. Asserting 403 FAILS with AssertionError.

All arrangements use CANONICAL routes only:
  /admin/auth/signup, /admin/auth/login, /admin/keys, /admin/cache,
  /internal/metrics, /v1/chat/completions

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
    (schema rebuilt per test via conftest.py fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380 db 9 (flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network — same as all existing suites)
  - FakeCompletionUpstream injected via app.state.completion_upstream
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from tests import _redis_env
from tests._polling import poll_for_count, poll_until

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_CACHE = "/admin/cache"
INTERNAL_METRICS = "/internal/metrics"
COMPLETIONS = "/v1/chat/completions"

# ---------------------------------------------------------------------------
# Upstream body used by fakes
# ---------------------------------------------------------------------------
UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-cache-1",
    "choices": [{"message": {"role": "assistant", "content": "hello from upstream"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

SSE_CHUNKS = [
    b'data: {"id":"gen-stream-1","choices":[{"delta":{"content":"hi"}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n',
    b"data: [DONE]\n\n",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    """Assert RFC 9457 problem+json shape; return parsed body."""
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


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
    cache_enabled: bool = False,
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body (body['key'] = plaintext key)."""
    payload: dict[str, Any] = {"name": name, "cache_enabled": cache_enabled}
    resp = await client.post(ADMIN_KEYS, json=payload, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    body = resp.json()
    # TRUE-RED: if cache_enabled is absent from CreateKeyResponse, this assertion fails
    # RIGHT HERE — AssertionError: 'cache_enabled' not in body.
    assert "cache_enabled" in body, (
        f"cache_enabled field absent from POST /admin/keys response: {body}"
    )
    return body


def completion_payload(
    model: str,
    *,
    stream: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "what is 2+2?"}],
    }
    if stream is not None:
        body["stream"] = stream
    if extra:
        body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Fake non-streaming + streaming upstream — tracks call count."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls: int = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in SSE_CHUNKS:
                yield chunk

        return _gen()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def fake_upstream(app: object) -> FakeCompletionUpstream:
    """Inject a FakeCompletionUpstream into app.state; return it for call-count inspection."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    return upstream


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert minimal active model + pricing snapshot so recorder computes cost."""
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {
            "sid": str(snap_id),
            "mid": model_id,
            "p": "0.000001",
            "c": "0.000002",
        },
    )
    await db_session.commit()
    return model_id


# ===========================================================================
# S1 — Default off: cache_enabled=false → upstream called twice; no X-Cache header
#
# TRUE-RED REASON: POST /admin/keys response does not include cache_enabled field yet.
#   The create_key() helper asserts "cache_enabled" in body → AssertionError.
#   (Strengthened: even if that passes, asserting body["cache_enabled"] == False
#    would also fail with KeyError. The missing-field assertion fires first.)
# ===========================================================================


async def test_cache_default_off_upstream_called_twice(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """With cache disabled (default), upstream is called for every identical request."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="NoCacheCo", email="owner@nocache.io"
    )
    # TRUE-RED: create_key asserts "cache_enabled" in response → AssertionError today
    key_info = await create_key(client, jwt, name="no-cache-key", cache_enabled=False)
    assert key_info["cache_enabled"] is False

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    payload = completion_payload(active_model)
    headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Both calls hit upstream — no caching
    assert upstream.calls == 2, f"expected upstream.calls==2, got {upstream.calls}"
    # X-Cache header must be absent when cache is not enabled
    assert "x-cache" not in resp1.headers, (
        f"unexpected X-Cache header on resp1: {resp1.headers.get('x-cache')}"
    )
    assert "x-cache" not in resp2.headers, (
        f"unexpected X-Cache header on resp2: {resp2.headers.get('x-cache')}"
    )


# ===========================================================================
# S2 — Key-enabled cache hit flow
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError
#   in create_key() helper at "cache_enabled" in body assertion (same as S1).
#   Even if bypassed: upstream.calls==2 after two requests (no cache logic) → fails
#   with AssertionError: 2 != 1.
# ===========================================================================


async def test_key_enabled_cache_hit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """With key cache_enabled=true, second identical request served from cache.

    Upstream called exactly once; second response has X-Cache: hit;
    second usage row has cached=true in raw, cost_usd=0, real token counts.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="CacheCo", email="owner@cache.io"
    )
    # TRUE-RED: cache_enabled absent from CreateKeyResponse → AssertionError
    key_info = await create_key(client, jwt, name="cache-key", cache_enabled=True)
    assert key_info["cache_enabled"] is True

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model)
    headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Upstream called exactly once — second request served from cache
    assert upstream.calls == 1, (
        f"expected upstream.calls==1 (cache hit on 2nd request), got {upstream.calls}"
    )

    # First response: cache miss header
    assert resp1.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss on first request, got: {resp1.headers.get('x-cache')!r}"
    )
    # Second response: cache hit header
    assert resp2.headers.get("x-cache") == "hit", (
        f"expected X-Cache: hit on second request, got: {resp2.headers.get('x-cache')!r}"
    )

    # Bodies must be identical
    assert resp1.json() == resp2.json(), (
        "cached response body should be byte-identical to original"
    )

    # Verify usage row 2 has cached=true and cost_usd=0.
    # Both rows are written fire-and-forget, so they land AFTER the HTTP response
    # returns. The fixed `await asyncio.sleep(0.1)` this replaces failed twice under
    # `-n 6` with "expected 2 usage rows, found 1" (todo #82) — the write had simply
    # not landed inside the window.
    async def _fetch_rows() -> list[Any]:
        db_session.expire_all()
        return list(
            (
                await db_session.execute(
                    text(
                        "SELECT cost_usd, raw FROM usage_records"
                        " WHERE key_id = :kid ORDER BY created_at ASC"
                    ),
                    {"kid": key_info["key_id"]},
                )
            ).fetchall()
        )

    rows = await poll_for_count(_fetch_rows, 2)

    # Two usage rows should exist (one per request)
    assert len(rows) == 2, f"expected 2 usage rows, found {len(rows)}"

    first_cost, first_raw = rows[0]
    second_cost, second_raw = rows[1]

    # First row: normal cost (non-zero, tokens priced)
    assert first_cost > 0, f"first usage row cost should be > 0, got {first_cost}"
    # First row raw should NOT have cached=true
    assert first_raw.get("cached") is not True, (
        f"first usage row should not be marked cached: {first_raw}"
    )

    # Second row: cached hit → cost_usd = 0
    assert second_cost == 0, (
        f"cached hit usage row should have cost_usd=0, got {second_cost}"
    )
    # Second row raw should have cached=true marker
    assert second_raw.get("cached") is True, (
        f"second usage row raw should have cached=true, got: {second_raw}"
    )

    # Token counts in second row must match the upstream body's usage
    assert second_raw.get("usage", {}).get("prompt_tokens") == 10
    assert second_raw.get("usage", {}).get("completion_tokens") == 5


# ===========================================================================
# S3 — Tenant-enabled cache hit flow
#
# TRUE-RED REASON: PUT /admin/cache does not exist → 404 or 405.
#   The test asserts status == 200 on PUT /admin/cache → AssertionError.
# ===========================================================================


async def test_tenant_enabled_cache_hit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Tenant-level cache_enabled=true via PUT /admin/cache enables caching for all keys."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="TenantCacheCo", email="owner@tenantcache.io"
    )

    # Key has cache_enabled=false — but tenant level will be set to true
    # TRUE-RED: create_key asserts "cache_enabled" in body → fails before this anyway
    key_info = await create_key(client, jwt, name="tenant-cache-key", cache_enabled=False)
    assert key_info["cache_enabled"] is False

    # TRUE-RED: PUT /admin/cache does not exist → 404/405, asserting 200 FAILS
    put_resp = await client.put(
        ADMIN_CACHE, json={"enabled": True}, headers=auth_jwt(jwt)
    )
    assert put_resp.status_code == 200, (
        f"expected 200 from PUT /admin/cache, got {put_resp.status_code}: {put_resp.text}"
    )
    assert put_resp.json().get("enabled") is True

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model)
    headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Tenant-level cache enabled → hit on second request
    assert upstream.calls == 1, (
        f"expected upstream.calls==1 (tenant cache hit), got {upstream.calls}"
    )
    assert resp2.headers.get("x-cache") == "hit", (
        f"expected X-Cache: hit with tenant cache enabled, got: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# S4 — Bypass header forces upstream; re-stores response; next request hits cache
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError.
#   Even if bypassed: X-Cache: bypass header absent → AssertionError on header assertion.
# ===========================================================================


async def test_bypass_header_forces_upstream_and_restores(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Cache-Control: no-cache forces upstream call; response is re-stored; next hit succeeds."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="BypassCo", email="owner@bypass.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError in create_key()
    key_info = await create_key(client, jwt, name="bypass-key", cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    payload = completion_payload(active_model)
    api_headers = auth_key(key_info["key"])

    # Warm the cache (first request — miss)
    warm_resp = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert warm_resp.status_code == 200
    assert upstream.calls == 1

    # Bypass: Cache-Control: no-cache forces upstream even though cache is warm
    bypass_headers = {**api_headers, "Cache-Control": "no-cache"}
    bypass_resp = await client.post(COMPLETIONS, json=payload, headers=bypass_headers)
    assert bypass_resp.status_code == 200
    # TRUE-RED: X-Cache: bypass header absent → AssertionError
    assert bypass_resp.headers.get("x-cache") == "bypass", (
        f"expected X-Cache: bypass, got: {bypass_resp.headers.get('x-cache')!r}"
    )
    assert upstream.calls == 2, (
        f"bypass should have called upstream (calls==2), got {upstream.calls}"
    )

    # Next identical request without bypass: cache hit (bypass re-stored the entry)
    hit_resp = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert hit_resp.status_code == 200
    assert hit_resp.headers.get("x-cache") == "hit", (
        f"expected X-Cache: hit after bypass re-warm, got: {hit_resp.headers.get('x-cache')!r}"
    )
    # Upstream NOT called again
    assert upstream.calls == 2, (
        f"no additional upstream call expected after bypass re-warm, got {upstream.calls}"
    )


# ===========================================================================
# S5 — Different payload → cache miss
#
# TRUE-RED REASON: cache_enabled absent → AssertionError in create_key().
#   Even if bypassed: X-Cache: miss header absent → AssertionError.
# ===========================================================================


async def test_different_payload_is_cache_miss(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Different message content produces a different cache key → miss."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="PayloadCo", email="owner@payload.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError
    key_info = await create_key(client, jwt, name="payload-key", cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    payload_a = {
        "model": active_model,
        "messages": [{"role": "user", "content": "question A"}],
    }
    payload_b = {
        "model": active_model,
        "messages": [{"role": "user", "content": "question B — different"}],
    }

    # Warm cache with payload A
    resp_a1 = await client.post(COMPLETIONS, json=payload_a, headers=api_headers)
    assert resp_a1.status_code == 200
    assert upstream.calls == 1

    # Second identical request for A: cache hit
    resp_a2 = await client.post(COMPLETIONS, json=payload_a, headers=api_headers)
    assert resp_a2.status_code == 200
    assert resp_a2.headers.get("x-cache") == "hit", (
        f"expected hit for payload A repeat, got: {resp_a2.headers.get('x-cache')!r}"
    )
    assert upstream.calls == 1  # not called again for A

    # Payload B: cache miss (different payload → different cache key)
    resp_b = await client.post(COMPLETIONS, json=payload_b, headers=api_headers)
    assert resp_b.status_code == 200
    # TRUE-RED: X-Cache: miss header absent → AssertionError
    assert resp_b.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss for different payload, got: {resp_b.headers.get('x-cache')!r}"
    )
    assert upstream.calls == 2, (
        f"upstream should be called for payload B miss, got calls={upstream.calls}"
    )


# ===========================================================================
# S6 — Different tenant, identical payload → cache miss (tenant isolation)
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError
#   in create_key() helper (first call for tenant A).
# ===========================================================================


async def test_different_tenant_same_payload_is_cache_miss(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Cache keys are tenant-scoped: Tenant A warming the cache does NOT serve Tenant B."""
    jwt_a, _tid_a = await signup_and_login(
        client, tenant_name="TenantAlpha", email="owner@alpha.io"
    )
    jwt_b, _tid_b = await signup_and_login(
        client, tenant_name="TenantBeta", email="owner@beta.io"
    )

    # TRUE-RED: cache_enabled absent → AssertionError for both calls
    key_a = await create_key(client, jwt_a, name="key-a", cache_enabled=True)
    key_b = await create_key(client, jwt_b, name="key-b", cache_enabled=True)

    upstream_a = FakeCompletionUpstream()
    upstream_b = FakeCompletionUpstream()

    payload = {
        "model": active_model,
        "messages": [{"role": "user", "content": "identical payload"}],
    }

    # Tenant A warms cache
    app.state.completion_upstream = upstream_a
    resp_a = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_a["key"]))
    assert resp_a.status_code == 200
    assert upstream_a.calls == 1

    # Tenant B sends same payload: must be a miss (different tenant_id in cache key)
    app.state.completion_upstream = upstream_b
    resp_b = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_b["key"]))
    assert resp_b.status_code == 200
    # TRUE-RED: X-Cache: miss absent → AssertionError
    assert resp_b.headers.get("x-cache") == "miss", (
        f"Tenant B should get a cache miss (tenant isolation), "
        f"got: {resp_b.headers.get('x-cache')!r}"
    )
    assert upstream_b.calls == 1, (
        f"Tenant B upstream should be called (cache miss), got calls={upstream_b.calls}"
    )


# ===========================================================================
# S7 — Upstream 4xx is NOT cached
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError
#   in create_key() helper.
# ===========================================================================


async def test_upstream_4xx_not_cached(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Upstream 4xx responses are never stored; identical follow-up still calls upstream."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="FourxxCo", email="owner@fourxx.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError
    key_info = await create_key(client, jwt, name="fourxx-key", cache_enabled=True)

    # Upstream returns 400 (simulated upstream model error)
    upstream_400 = FakeCompletionUpstream(
        status=400,
        body={"error": {"message": "bad request from upstream", "code": 400}},
    )
    app.state.completion_upstream = upstream_400

    payload = completion_payload(active_model)
    api_headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=api_headers)

    # Both responses should be from upstream (not cached)
    assert upstream_400.calls == 2, (
        f"upstream should be called twice (4xx not cached), got {upstream_400.calls}"
    )
    # No X-Cache: hit on either response
    assert resp2.headers.get("x-cache") != "hit", (
        f"4xx response should not be cached, but got X-Cache: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# S8 — Governance blocks before cache lookup; disabled model returns 403 even when warm
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError
#   in create_key() helper. Even if that passes: catalog-disable SQL update works,
#   but without cache the test would pass for the wrong reason (governance already fires);
#   the assert on "x-cache" header being absent (governance blocked before cache read)
#   would also pass trivially today. The primary red gate is the cache_enabled field check
#   in create_key(). The test strengthens correctness by asserting that the 403 is returned
#   (governance wins) and NOT a 200 from cache.
# ===========================================================================


async def test_governance_blocks_before_cache(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """Model disabled after cache warm → governance fires, 403 returned, not cached body."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="GovCo", email="owner@gov.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError
    key_info = await create_key(client, jwt, name="gov-key", cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model)
    api_headers = auth_key(key_info["key"])

    # Warm the cache
    warm_resp = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert warm_resp.status_code == 200
    assert warm_resp.headers.get("x-cache") == "miss"
    assert upstream.calls == 1

    # Disable the model after cache warm
    await db_session.execute(
        text("UPDATE models SET active = false WHERE id = :mid"),
        {"mid": active_model},
    )
    await db_session.commit()

    # Request with same payload: governance (model disabled) must block BEFORE cache
    blocked_resp = await client.post(COMPLETIONS, json=payload, headers=api_headers)

    # Must get governance error, NOT the cached body
    assert blocked_resp.status_code in (400, 403), (
        f"expected governance error (400 or 403), got {blocked_resp.status_code}: "
        f"{blocked_resp.text}"
    )
    assert blocked_resp.json().get("code") in ("ERR_MODEL_UNKNOWN", "ERR_MODEL_DISABLED"), (
        f"expected governance error code, got: {blocked_resp.json().get('code')!r}"
    )
    # X-Cache header must be absent (governance fired before cache read)
    assert "x-cache" not in blocked_resp.headers, (
        f"X-Cache header should be absent when governance blocks: "
        f"{blocked_resp.headers.get('x-cache')!r}"
    )
    # Upstream must NOT be called for the blocked request
    assert upstream.calls == 1, (
        f"upstream should not be called after governance block, got {upstream.calls}"
    )


# ===========================================================================
# S9 — Streaming requests bypass cache entirely (no read, no write)
#
# TRUE-RED REASON: cache_enabled absent from CreateKeyResponse → AssertionError
#   in create_key() helper. Even if bypassed: streaming currently works fine and
#   would have upstream.calls==2 naturally. The X-Cache header assertion is the
#   other gate; but actually X-Cache absent on stream is already satisfied (no header
#   added). The primary red gate is the cache_enabled field assertion in create_key().
#   Note: if the build incorrectly adds X-Cache on streaming, that would be a bug caught
#   by the "x-cache not in headers" assertion — so this test also guards against regressions.
# ===========================================================================


async def test_stream_bypasses_cache_entirely(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Streaming completions never read or write cache regardless of cache_enabled flag."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="StreamCo", email="owner@stream.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError
    key_info = await create_key(client, jwt, name="stream-key", cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = completion_payload(active_model, stream=True)
    api_headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Upstream must be called for BOTH requests (stream never cached)
    assert upstream.calls == 2, (
        f"streaming requests should always call upstream, got calls={upstream.calls}"
    )

    # X-Cache header must be absent on streaming responses
    assert "x-cache" not in resp1.headers, (
        f"X-Cache must not be set on streaming response 1: "
        f"{resp1.headers.get('x-cache')!r}"
    )
    assert "x-cache" not in resp2.headers, (
        f"X-Cache must not be set on streaming response 2: "
        f"{resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# S10 — Prometheus counter gateway_cache_events_total increments
#
# TRUE-RED REASON: MetricsRegistry has no `cache_events_total` attribute yet.
#   Accessing app.state.metrics_registry.cache_events_total → AttributeError.
#   Additionally, the counter label "miss"/"hit"/"bypass" would not exist.
# ===========================================================================


async def test_prometheus_counter_increments(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """gateway_cache_events_total counter increments for miss, hit, and bypass events."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="MetricsCo", email="owner@metrics.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError; also cache_events_total absent
    key_info = await create_key(client, jwt, name="metrics-key", cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])
    bypass_headers = {**api_headers, "Cache-Control": "no-cache"}
    payload = completion_payload(active_model)

    # TRUE-RED: accessing cache_events_total → AttributeError if not added to MetricsRegistry
    metrics_reg = app.state.metrics_registry  # type: ignore[attr-defined]
    # This assertion fails before any requests if the counter doesn't exist:
    assert hasattr(metrics_reg, "cache_events_total"), (
        "MetricsRegistry must have cache_events_total counter "
        "(gateway_cache_events_total{result=...})"
    )

    # Baseline counter values (all 0 at start of fresh test)
    def _sample(label: str) -> float:
        # Read current value from the counter for the given result label
        for metric in metrics_reg.cache_events_total.collect():
            for sample in metric.samples:
                if sample.labels.get("result") == label:
                    return sample.value
        return 0.0

    miss_before = _sample("miss")
    hit_before = _sample("hit")
    bypass_before = _sample("bypass")

    # First request: cache miss
    r_miss = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert r_miss.status_code == 200
    assert r_miss.headers.get("x-cache") == "miss"

    # Second request: cache hit
    r_hit = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert r_hit.status_code == 200
    assert r_hit.headers.get("x-cache") == "hit"

    # Third request: bypass
    r_bypass = await client.post(COMPLETIONS, json=payload, headers=bypass_headers)
    assert r_bypass.status_code == 200
    assert r_bypass.headers.get("x-cache") == "bypass"

    # Counter assertions
    assert _sample("miss") == miss_before + 1, (
        f"gateway_cache_events_total{{result='miss'}} should increment by 1"
    )
    assert _sample("hit") == hit_before + 1, (
        f"gateway_cache_events_total{{result='hit'}} should increment by 1"
    )
    assert _sample("bypass") == bypass_before + 1, (
        f"gateway_cache_events_total{{result='bypass'}} should increment by 1"
    )


# ===========================================================================
# S11 — PATCH /admin/keys/{key_id} with cache_enabled toggle
#
# TRUE-RED REASON: PatchKeyRequest and KeyInfoResponse do not have cache_enabled field.
#   The field is silently ignored by FastAPI/Pydantic; the response body lacks
#   cache_enabled → asserting body["cache_enabled"] == True → KeyError / AssertionError.
# ===========================================================================


async def test_patch_key_cache_enabled_toggle(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PATCH /admin/keys/{key_id} with cache_enabled toggles the flag; echoed in response."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="PatchCacheCo", email="owner@patchcache.io"
    )

    # Create key with cache_enabled=false (default)
    # TRUE-RED: cache_enabled absent from CreateKeyResponse → AssertionError in create_key()
    key_info = await create_key(client, jwt, name="patch-cache-key", cache_enabled=False)
    key_id = key_info["key_id"]
    assert key_info["cache_enabled"] is False

    # PATCH to enable caching
    patch_resp = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"cache_enabled": True},
        headers=auth_jwt(jwt),
    )
    assert patch_resp.status_code == 200, (
        f"PATCH /admin/keys/{key_id} failed: {patch_resp.status_code}: {patch_resp.text}"
    )
    patch_body = patch_resp.json()
    # TRUE-RED: cache_enabled absent from KeyInfoResponse → KeyError / AssertionError
    assert patch_body.get("cache_enabled") is True, (
        f"expected cache_enabled=True in PATCH response, got: {patch_body.get('cache_enabled')!r}"
    )

    # Verify DB persistence
    row = (
        await db_session.execute(
            text("SELECT cache_enabled FROM api_keys WHERE id = :kid"),
            {"kid": key_id},
        )
    ).fetchone()
    assert row is not None, "key row not found"
    assert row[0] is True, f"DB cache_enabled should be True, got: {row[0]!r}"

    # PATCH back to false
    patch_off = await client.patch(
        f"{ADMIN_KEYS}/{key_id}",
        json={"cache_enabled": False},
        headers=auth_jwt(jwt),
    )
    assert patch_off.status_code == 200
    assert patch_off.json().get("cache_enabled") is False


# ===========================================================================
# S12 — Member role 403 on PUT /admin/cache
#
# TRUE-RED REASON: PUT /admin/cache does not exist → 404 (or 405).
#   Asserting status == 403 FAILS with AssertionError: 404 != 403.
# ===========================================================================


async def test_member_role_forbidden_on_put_cache(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """PUT /admin/cache with member-role JWT → 403 ERR_AUTH_FORBIDDEN."""
    # DISPOSITION (response-caching TASK.md §6, 2026-06-11): the original arrange
    # called PUT /admin/cache with an OWNER JWT while asserting the member 403 —
    # unreachable by any honest implementation (an owner must get 200). Same
    # defect class as team-governance S11; corrected with the established
    # same-tenant-member pattern (direct insert + token_service.issue). The
    # assertion target (403 ERR_AUTH_FORBIDDEN for a member) is unchanged. A
    # build-side dependency-override shim that forced 403 was REJECTED at
    # orchestrator review as a fake-green (it never exercised the real guard).
    _owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="MemberForbidCo", email="owner@memberforbid.io"
    )

    member_user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', 'member')"
        ),
        {"id": member_user_id, "tid": tenant_id, "email": "member@memberforbid.io"},
    )
    await db_session.commit()

    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member@memberforbid.io",
    )

    resp = await client.put(
        ADMIN_CACHE,
        json={"enabled": True},
        headers=auth_jwt(member_jwt),
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# Additional scenario: GET /admin/cache returns current tenant cache setting
# (validates the read path of the tenant cache toggle)
# ===========================================================================


async def test_get_cache_returns_current_setting(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/cache returns {"enabled": bool} reflecting current tenant setting."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="GetCacheCo", email="owner@getcache.io"
    )

    # TRUE-RED: GET /admin/cache does not exist → 404 or 405
    # Asserting 200 FAILS with AssertionError
    get_resp = await client.get(ADMIN_CACHE, headers=auth_jwt(jwt))
    assert get_resp.status_code == 200, (
        f"expected 200 from GET /admin/cache, got {get_resp.status_code}: {get_resp.text}"
    )
    body = get_resp.json()
    assert "enabled" in body, f"GET /admin/cache response missing 'enabled' field: {body}"
    assert body["enabled"] is False  # default is false

    # Enable via PUT
    put_resp = await client.put(
        ADMIN_CACHE, json={"enabled": True}, headers=auth_jwt(jwt)
    )
    assert put_resp.status_code == 200

    # GET should now return true
    get_after = await client.get(ADMIN_CACHE, headers=auth_jwt(jwt))
    assert get_after.status_code == 200
    assert get_after.json().get("enabled") is True


# ===========================================================================
# Additional scenario: spend counters NOT incremented on cache hit
# (validates §3 "cost_usd=0 → INCRBYFLOAT no-op" invariant)
# ===========================================================================


async def test_spend_counter_not_incremented_on_cache_hit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """Advisory spend counter unchanged after cache hit (cost_usd=0 path)."""
    import asyncio

    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="SpendCheckCo", email="owner@spendcheck.io"
    )
    # TRUE-RED: cache_enabled absent → AssertionError
    key_info = await create_key(client, jwt, name="spend-key", cache_enabled=True)
    key_id = key_info["key_id"]

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    payload = completion_payload(active_model)
    api_headers = auth_key(key_info["key"])

    # First request: miss → upstream called → spend counter incremented
    resp1 = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert resp1.status_code == 200

    import datetime

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:key:{key_id}:{yyyymm}"

    async def _spend() -> float:
        return float((await redis_client.get(spend_key)) or b"0")

    # POSITIVE wait — the counter MUST appear; same fire-and-forget race as above.
    spend_after_miss = await poll_until(_spend, lambda value: value > 0)
    # poll_until returns the LAST value even on timeout, so this assertion still carries
    # the test. Without it a counter that never moves reads 0.0 here AND 0.0 below, and
    # the "must not change" assertion below would pass vacuously.
    assert spend_after_miss > 0, (
        f"spend counter should be > 0 after first request (miss), got {spend_after_miss}"
    )

    # Second request: hit → cost_usd=0 → spend counter must NOT change
    resp2 = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    assert resp2.status_code == 200
    assert resp2.headers.get("x-cache") == "hit"
    # DELIBERATELY a fixed sleep, not poll_until. This assertion is NEGATIVE — the
    # counter must NOT move — and polling would return the instant the current value is
    # read, never giving an unwanted write the chance to appear. Converting this one
    # would turn a real assertion into a vacuous one; see tests/_polling.py's warning.
    # NEGATIVE WAIT: the spend counter must NOT move on a cache hit (see the note above).
    await asyncio.sleep(0.15)

    spend_after_hit = await _spend()
    assert spend_after_hit == spend_after_miss, (
        f"spend counter should not change on cache hit: "
        f"before={spend_after_miss}, after={spend_after_hit}"
    )
