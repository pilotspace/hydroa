"""Failing-first (RED) suite for semantic-cache (contract DRAFT, TASK.md §4).

One test per scenario in §2 SCENARIOS.

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets per test:

  SC1 (normalized variant hits semantic cache):
    The semantic lookup layer does not exist yet. After exact MISS, no semantic lookup runs.
    upstream.calls == 2 after both requests (no semantic hit). AssertionError: 2 != 1.
    Also: X-Cache: semantic_hit header absent → AssertionError.

  SC2 (negation-changed prompt MISSES — precision contract):
    This test is precision-correct by design: even without the semantic layer, the negation
    variant misses (upstream called twice). However, the ARRANGE step requires
    semantic_cache_enabled to be settable — PUT /admin/cache {"semantic_enabled": true} does
    not work yet (field absent from handler) → 422 or field silently ignored → GET returns
    {"enabled": bool} without semantic_enabled → AssertionError on the arrange assertion.
    Right-reason red: AssertionError confirming semantic_enabled absent from PUT/GET response.

  SC3 (number-changed prompt MISSES — precision contract):
    Same as SC2: arrange fails on semantic_enabled field absent from admin/cache response.

  SC4 (semantic layer inactive when semantic_cache_enabled=false):
    PUT /admin/cache {"semantic_enabled": false} — field absent → arrange fails.
    Alternatively: the test asserts that after a variant hit the second call used upstream.
    Without the semantic layer, upstream.calls==2 which is the expected outcome — this test
    could pass trivially. RED GATE: the arrange asserts that GET /admin/cache returns a
    semantic_enabled field (False by default). Without semantic_enabled in the response,
    the assertion fails: AssertionError on "semantic_enabled" not in body.

  SC5 (semantic layer inactive when cache_disabled):
    Arrange asserts semantic_enabled field in GET /admin/cache → AssertionError (absent).

  SC6 (tenant isolation: different tenant → miss):
    Arrange: PUT /admin/cache {"semantic_enabled": true} for tenant A → field missing → 422
    or silently ignored → GET lacks semantic_enabled → AssertionError.

  SC7 (model isolation: different model → miss):
    Same arrange failure as SC6 (semantic_enabled absent from cache endpoint).

  SC8 (exact cache regression — byte-identical still gets X-Cache: hit):
    This test could be GREEN-by-design because the exact cache layer is already implemented.
    Marked as a regression anchor: may pass before build. Still included to guard against
    regressions where the semantic layer accidentally shadows the exact layer.
    TRUE-RED GATE: the arrange still requires semantic_enabled field in GET response →
    AssertionError. Post-build this should remain green.

  SC9 (ledger row for semantic hit: cached=true cost_usd=0):
    Semantic hit does not exist yet → second request goes to upstream, usage row has non-zero
    cost. AssertionError: second_cost != 0 (it will be > 0).

  SC10 (semantic_hit metric increments):
    cache_events_total{result="semantic_hit"} counter value does not increment because the
    semantic layer does not exist. _sample("semantic_hit") stays 0 → AssertionError.

  SC11 (PII re-masked on semantic hit body):
    Semantic hit does not occur → test fails at upstream.calls==1 assertion (calls==2).
    Also: guardrail evaluate_post would not be called on a cache-miss path in the same way.

  SC12 (admin toggle semantic_enabled round-trip):
    PUT /admin/cache {"semantic_enabled": true} — semantic_enabled field unknown to the handler.
    Either: 422 ERR_PAYLOAD_INVALID (pydantic strict model rejects unknown field) OR silently
    ignored. In either case, GET /admin/cache does not return {"semantic_enabled": bool} →
    AssertionError: "semantic_enabled" not in body.

  SC13 (member role 403 on semantic toggle):
    PUT /admin/cache with member JWT — the endpoint exists but semantic_enabled field is absent.
    The handler currently accepts {"enabled": bool} only; {"semantic_enabled": true} is either
    rejected with 422 or silently ignored. BUT: the test sends any body and asserts 403 (member
    role check fires BEFORE body validation in the require_owner_or_admin dependency). So this
    test is actually GREEN-by-design today (the auth check fires before body parsing). Marked
    with a note; included as a regression anchor to ensure the auth check order is preserved.

  SC14 (streaming bypasses semantic layer):
    This test is GREEN-by-design: streaming has never touched the cache layer. Included as a
    regression anchor to guard against accidental streaming cache regression.
    TRUE-RED GATE: the arrange asserts semantic_enabled in GET /admin/cache → AssertionError.

  SC15 (bypass restores semantic key; subsequent variant gets semantic_hit):
    After bypass, semantic key not stored (semantic layer absent) → subsequent variant request
    misses → upstream.calls==3 not 2 → AssertionError. Also X-Cache: semantic_hit absent.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (flushed per test)
  - httpx.ASGITransport (no network)
  - FakeCompletionUpstream injected via app.state.completion_upstream
  - asyncio_mode = "auto" (pyproject.toml)
  - Fixture patterns mirror tests/response_caching EXACTLY (see conftest.py in this dir)
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

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
ADMIN_CACHE = "/admin/cache"
COMPLETIONS = "/v1/chat/completions"

# ---------------------------------------------------------------------------
# Upstream body used by fakes
# ---------------------------------------------------------------------------
UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-sem-1",
    "choices": [{"message": {"role": "assistant", "content": "hello from upstream"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

# Upstream body with a PII-like string (for SC11)
UPSTREAM_BODY_PII: dict[str, Any] = {
    "id": "gen-sem-pii",
    "choices": [{"message": {"role": "assistant", "content": "User email: john@example.com"}}],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}

SSE_CHUNKS = [
    b'data: {"id":"gen-stream-sem","choices":[{"delta":{"content":"hi"}}]}\n\n',
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
    assert "cache_enabled" in body, (
        f"cache_enabled field absent from POST /admin/keys response: {body}"
    )
    return body


async def enable_semantic_cache(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    semantic_enabled: bool = True,
    cache_enabled: bool = True,
) -> None:
    """PUT /admin/cache to set both cache and semantic_cache flags.

    TRUE-RED GATE: this function asserts that the PUT response contains
    "semantic_enabled" in the body. Until the semantic layer is built,
    this assertion FAILS with AssertionError: "semantic_enabled" not in response body.
    This is the primary red gate for ALL tests that call this helper.
    """
    body: dict[str, Any] = {"enabled": cache_enabled, "semantic_enabled": semantic_enabled}
    resp = await client.put(ADMIN_CACHE, json=body, headers=auth_jwt(jwt))
    assert resp.status_code == 200, (
        f"PUT /admin/cache failed ({resp.status_code}): {resp.text}"
    )
    resp_body = resp.json()
    # TRUE-RED: "semantic_enabled" absent from PUT /admin/cache response → AssertionError
    assert "semantic_enabled" in resp_body, (
        f"PUT /admin/cache response missing 'semantic_enabled' field: {resp_body}"
    )
    assert resp_body["semantic_enabled"] is semantic_enabled, (
        f"expected semantic_enabled={semantic_enabled!r}, "
        f"got {resp_body.get('semantic_enabled')!r}"
    )


async def assert_semantic_enabled_in_get(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    expected: bool,
) -> None:
    """GET /admin/cache and assert semantic_enabled field is present with expected value.

    TRUE-RED GATE: fails until semantic_enabled is added to GET /admin/cache response.
    """
    resp = await client.get(ADMIN_CACHE, headers=auth_jwt(jwt))
    assert resp.status_code == 200, (
        f"GET /admin/cache failed ({resp.status_code}): {resp.text}"
    )
    body = resp.json()
    # TRUE-RED: "semantic_enabled" absent → AssertionError
    assert "semantic_enabled" in body, (
        f"GET /admin/cache response missing 'semantic_enabled' field: {body}"
    )
    assert body["semantic_enabled"] is expected, (
        f"expected semantic_enabled={expected!r}, got {body.get('semantic_enabled')!r}"
    )


def completion_payload(
    model: str,
    *,
    content: str = "what is 2+2?",
    stream: bool | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
    }
    if stream is not None:
        body["stream"] = stream
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
# Fixtures (suite-local; same pattern as response_caching conftest)
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
# SC1 — Normalized variant hits semantic cache (precision contract, POSITIVE side)
#
# TRUE-RED REASON: semantic layer absent → second request calls upstream (calls==2).
#   Also: enable_semantic_cache() asserts "semantic_enabled" in PUT response → AssertionError.
#   Primary red gate: AssertionError from enable_semantic_cache() helper.
#   If somehow that passes: AssertionError on upstream.calls==1 (will be 2).
# ===========================================================================


async def test_semantic_cache_normalized_variant_hits(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """Normalized variant prompt hits semantic cache; upstream called ONCE.

    SC1: "  What is the capital of France?  " and "what is the capital of france"
    normalize to the same key → semantic HIT on second request.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="SemCacheCo", email="owner@semcache.io"
    )
    # Arrange: key with cache_enabled=true, tenant with semantic_cache_enabled=true
    key_info = await create_key(client, jwt, name="sem-key", cache_enabled=True)
    # TRUE-RED PRIMARY GATE: asserts "semantic_enabled" in PUT response → AssertionError today
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    # First request: a prompt with extra leading/trailing whitespace and mixed case
    p1 = completion_payload(active_model, content="  What is the capital of France?  ")
    # Second request: lowercase, no extra spaces, no trailing punctuation
    p2 = completion_payload(active_model, content="what is the capital of france")

    api_headers = auth_key(key_info["key"])
    resp1 = await client.post(COMPLETIONS, json=p1, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=p2, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # TRUE-RED: upstream.calls should be 1 (semantic HIT on second request)
    # Without semantic layer: upstream.calls == 2 → AssertionError: 2 != 1
    assert upstream.calls == 1, (
        f"expected upstream.calls==1 (semantic hit on 2nd request), got {upstream.calls}"
    )

    # First response: miss (exact miss, semantic key stored)
    assert resp1.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss on first request, got: {resp1.headers.get('x-cache')!r}"
    )

    # Second response: semantic_hit
    # TRUE-RED: X-Cache: semantic_hit absent → AssertionError
    assert resp2.headers.get("x-cache") == "semantic_hit", (
        f"expected X-Cache: semantic_hit on normalized variant, "
        f"got: {resp2.headers.get('x-cache')!r}"
    )

    # Bodies must be identical (semantic hit returns exact same cached body)
    assert resp1.json() == resp2.json(), (
        "semantic hit body should be identical to original response body"
    )

    # Verify usage row 2 has cached=true and cost_usd=0
    import asyncio
    await asyncio.sleep(0.1)

    rows = (
        await db_session.execute(
            text(
                "SELECT cost_usd, raw FROM usage_records"
                " WHERE key_id = :kid ORDER BY created_at ASC"
            ),
            {"kid": key_info["key_id"]},
        )
    ).fetchall()

    assert len(rows) == 2, f"expected 2 usage rows, found {len(rows)}"

    _first_cost, _first_raw = rows[0]
    second_cost, second_raw = rows[1]

    # Second row: semantic hit → cost_usd=0, cached=true
    assert second_cost == 0, (
        f"semantic hit usage row should have cost_usd=0, got {second_cost}"
    )
    assert second_raw.get("cached") is True, (
        f"semantic hit usage row raw should have cached=true, got: {second_raw}"
    )


# ===========================================================================
# SC2 — Negation-changed prompt MISSES (precision contract — NEGATIVE side)
#
# TRUE-RED REASON: enable_semantic_cache() asserts "semantic_enabled" in PUT response
#   → AssertionError (primary gate). If that passes: the test would be green-by-design
#   (negation always misses) BUT the secondary assertion "semantic_enabled" in
#   GET /admin/cache enforces the contract via assert_semantic_enabled_in_get().
# ===========================================================================


async def test_negation_change_misses_semantic_cache(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Negation change must MISS the semantic cache (precision: "delete" != "don't delete").

    This is the precision contract: a single negation particle survives normalization
    (casefold does not remove it) so the two prompts produce distinct normalized keys.
    Upstream must be called TWICE — serving the negated prompt from a "delete" cache would
    be a correctness failure strictly worse than a miss.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="NegationCo", email="owner@negation.io"
    )
    key_info = await create_key(client, jwt, name="neg-key", cache_enabled=True)
    # TRUE-RED PRIMARY GATE: "semantic_enabled" absent from PUT response → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Warm cache with the base prompt
    base_prompt = completion_payload(active_model, content="delete my account")
    resp1 = await client.post(COMPLETIONS, json=base_prompt, headers=api_headers)
    assert resp1.status_code == 200
    assert upstream.calls == 1

    # Negation-injected variant — MUST miss (different token after normalization)
    negation_prompt = completion_payload(active_model, content="don't delete my account")
    resp2 = await client.post(COMPLETIONS, json=negation_prompt, headers=api_headers)
    assert resp2.status_code == 200

    # PRECISION CONTRACT: upstream must be called for the negation variant
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (negation must miss semantic cache), "
        f"got {upstream.calls}"
    )

    # X-Cache must NOT be semantic_hit (it must be miss — different key)
    assert resp2.headers.get("x-cache") != "semantic_hit", (
        f"PRECISION VIOLATION: negation-changed prompt got X-Cache: semantic_hit — "
        f"this is a FALSE HIT (correctness failure): {resp2.headers.get('x-cache')!r}"
    )
    assert resp2.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss for negation variant, "
        f"got: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC3 — Number-changed prompt MISSES (precision contract — number/year side)
#
# TRUE-RED REASON: same as SC2 — enable_semantic_cache() gate fires first.
#   Precision test: digits survive normalization → distinct keys.
# ===========================================================================


async def test_number_change_misses_semantic_cache(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Number change must MISS the semantic cache (precision: "in 1400" != "in 1300").

    Digits survive NFKC + casefold + whitespace collapse, so year changes produce
    distinct normalized keys — false hits on year changes would be correctness failures.
    """
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="NumberCo", email="owner@number.io"
    )
    key_info = await create_key(client, jwt, name="num-key", cache_enabled=True)
    # TRUE-RED PRIMARY GATE: "semantic_enabled" absent → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Warm cache with the 1400 prompt
    p1400 = completion_payload(
        active_model, content="what is the capital of France in 1400?"
    )
    resp1 = await client.post(COMPLETIONS, json=p1400, headers=api_headers)
    assert resp1.status_code == 200
    assert upstream.calls == 1

    # Number-changed variant — MUST miss
    p1300 = completion_payload(
        active_model, content="what is the capital of France in 1300?"
    )
    resp2 = await client.post(COMPLETIONS, json=p1300, headers=api_headers)
    assert resp2.status_code == 200

    # PRECISION CONTRACT: upstream must be called for the number-changed variant
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (number change must miss semantic cache), "
        f"got {upstream.calls}"
    )

    # X-Cache must NOT be semantic_hit
    assert resp2.headers.get("x-cache") != "semantic_hit", (
        f"PRECISION VIOLATION: number-changed prompt got X-Cache: semantic_hit — "
        f"this is a FALSE HIT: {resp2.headers.get('x-cache')!r}"
    )
    assert resp2.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss for number-changed variant, "
        f"got: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC4 — Semantic layer inactive when semantic_cache_enabled=false (default)
#
# TRUE-RED REASON: assert_semantic_enabled_in_get() asserts "semantic_enabled" in
#   GET /admin/cache body → AssertionError (field absent today).
# ===========================================================================


async def test_semantic_cache_inactive_when_disabled(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """With semantic_cache_enabled=false (default), variant prompt does NOT get semantic_hit."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="SemOffCo", email="owner@semoff.io"
    )
    key_info = await create_key(client, jwt, name="semoff-key", cache_enabled=True)

    # Confirm that GET /admin/cache returns semantic_enabled=false by default
    # TRUE-RED: "semantic_enabled" absent from GET response → AssertionError
    await assert_semantic_enabled_in_get(client, jwt, expected=False)

    # Do NOT enable semantic_cache (leave it false)
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    p1 = completion_payload(active_model, content="  What is Python?  ")
    p2 = completion_payload(active_model, content="what is python")

    resp1 = await client.post(COMPLETIONS, json=p1, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=p2, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Semantic layer inactive: both requests hit upstream
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (semantic layer inactive), got {upstream.calls}"
    )

    # Neither response should have X-Cache: semantic_hit
    assert resp1.headers.get("x-cache") != "semantic_hit", (
        f"unexpected semantic_hit when semantic layer is disabled: "
        f"{resp1.headers.get('x-cache')!r}"
    )
    assert resp2.headers.get("x-cache") != "semantic_hit", (
        f"unexpected semantic_hit when semantic layer is disabled: "
        f"{resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC5 — Semantic layer inactive when overall cache is disabled
#
# TRUE-RED REASON: assert_semantic_enabled_in_get() gate → AssertionError on missing field.
# ===========================================================================


async def test_semantic_cache_inactive_when_cache_disabled(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """With cache_enabled=false (overall cache off), semantic layer never activates."""
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="CacheOffSemCo", email="owner@cacheoffsem.io"
    )
    # Key with cache_enabled=false; semantic would be on at tenant level (irrelevant without base gate)
    key_info = await create_key(client, jwt, name="cacheoff-sem-key", cache_enabled=False)

    # Confirm default GET response includes semantic_enabled field
    # TRUE-RED: "semantic_enabled" absent → AssertionError
    await assert_semantic_enabled_in_get(client, jwt, expected=False)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    p1 = completion_payload(active_model, content="  What is Go?  ")
    p2 = completion_payload(active_model, content="what is go")

    resp1 = await client.post(COMPLETIONS, json=p1, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=p2, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Overall cache disabled: both requests hit upstream; no X-Cache header at all
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (overall cache disabled), got {upstream.calls}"
    )
    assert "x-cache" not in resp1.headers, (
        f"X-Cache should be absent when cache disabled: {resp1.headers.get('x-cache')!r}"
    )
    assert "x-cache" not in resp2.headers, (
        f"X-Cache should be absent when cache disabled: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC6 — Tenant isolation: same normalized prompt, different tenant → miss
#
# TRUE-RED REASON: enable_semantic_cache() for tenant A → "semantic_enabled" absent
#   from PUT response → AssertionError (primary gate).
# ===========================================================================


async def test_semantic_tenant_isolation(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Semantic cache is tenant-scoped: Tenant A warming cache does NOT serve Tenant B."""
    jwt_a, _tid_a = await signup_and_login(
        client, tenant_name="SemTenantAlpha", email="owner@semalpha.io"
    )
    jwt_b, _tid_b = await signup_and_login(
        client, tenant_name="SemTenantBeta", email="owner@sembeta.io"
    )

    key_a = await create_key(client, jwt_a, name="sem-key-a", cache_enabled=True)
    key_b = await create_key(client, jwt_b, name="sem-key-b", cache_enabled=True)

    # TRUE-RED: "semantic_enabled" absent from PUT response → AssertionError
    await enable_semantic_cache(client, jwt_a, semantic_enabled=True, cache_enabled=True)
    await enable_semantic_cache(client, jwt_b, semantic_enabled=True, cache_enabled=True)

    upstream_a = FakeCompletionUpstream()
    upstream_b = FakeCompletionUpstream()

    # Tenant A warms cache with a prompt
    app.state.completion_upstream = upstream_a
    p_warm = completion_payload(active_model, content="  Explain machine learning  ")
    resp_a = await client.post(
        COMPLETIONS, json=p_warm, headers=auth_key(key_a["key"])
    )
    assert resp_a.status_code == 200
    assert upstream_a.calls == 1

    # Tenant B sends a normalized variant of the same prompt
    app.state.completion_upstream = upstream_b
    p_variant = completion_payload(active_model, content="explain machine learning")
    resp_b = await client.post(
        COMPLETIONS, json=p_variant, headers=auth_key(key_b["key"])
    )
    assert resp_b.status_code == 200

    # TENANT ISOLATION: Tenant B must get a miss (different tenant_id in semantic key)
    assert upstream_b.calls == 1, (
        f"Tenant B upstream should be called (tenant isolation prevents semantic hit), "
        f"got {upstream_b.calls}"
    )
    assert resp_b.headers.get("x-cache") != "semantic_hit", (
        f"ISOLATION VIOLATION: Tenant B got X-Cache: semantic_hit from Tenant A's cache. "
        f"Semantic keys are not properly tenant-scoped."
    )
    assert resp_b.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss for Tenant B (tenant isolation), "
        f"got: {resp_b.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC7 — Model isolation: same normalized prompt, different model → miss
#
# TRUE-RED REASON: enable_semantic_cache() gate → AssertionError on "semantic_enabled".
# ===========================================================================


async def test_semantic_model_isolation(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """Semantic cache is model-scoped: same normalized prompt for different model → miss."""
    # Insert a second active model
    model_2 = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO UPDATE SET active = true"
        ),
        {"i": model_2, "n": "GPT-4o"},
    )
    await db_session.commit()

    jwt, _tid = await signup_and_login(
        client, tenant_name="SemModelCo", email="owner@semmodel.io"
    )
    key_info = await create_key(client, jwt, name="sem-model-key", cache_enabled=True)
    # TRUE-RED: "semantic_enabled" absent → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Warm cache with model_1 + prompt
    p_m1 = completion_payload(active_model, content="  Describe transformers  ")
    resp1 = await client.post(COMPLETIONS, json=p_m1, headers=api_headers)
    assert resp1.status_code == 200
    assert upstream.calls == 1

    # Same normalized prompt but different model — MUST miss (model is verbatim in key)
    p_m2: dict[str, Any] = {
        "model": model_2,
        "messages": [{"role": "user", "content": "describe transformers"}],
    }
    resp2 = await client.post(COMPLETIONS, json=p_m2, headers=api_headers)
    assert resp2.status_code == 200

    # MODEL ISOLATION: different model → different key → miss
    assert upstream.calls == 2, (
        f"expected upstream.calls==2 (model isolation prevents semantic hit), "
        f"got {upstream.calls}"
    )
    assert resp2.headers.get("x-cache") != "semantic_hit", (
        f"MODEL ISOLATION VIOLATION: different model got X-Cache: semantic_hit"
    )
    assert resp2.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss for different model, "
        f"got: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC8 — Exact-match cache regression: byte-identical prompt still gets X-Cache: hit
#
# TRUE-RED REASON: assert_semantic_enabled_in_get() gate → AssertionError.
#   Post-build: this test should remain GREEN (regression anchor for exact layer).
# ===========================================================================


async def test_exact_cache_regression_unchanged(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Byte-identical prompts still get X-Cache: hit (exact layer) not semantic_hit.

    This is a regression anchor: the exact-match cache must continue to work correctly
    even when the semantic layer is active. Exact hits must be labeled "hit", not "semantic_hit".
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="ExactRegressionCo", email="owner@exactregress.io"
    )
    key_info = await create_key(client, jwt, name="exact-reg-key", cache_enabled=True)
    # TRUE-RED: "semantic_enabled" absent from GET response → AssertionError
    await assert_semantic_enabled_in_get(client, jwt, expected=False)
    # Enable both cache flags (exact regression still holds with semantic enabled)
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Send byte-identical requests
    payload = completion_payload(active_model, content="what is 2+2?")
    resp1 = await client.post(COMPLETIONS, json=payload, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Exact layer regression: upstream called once, second response is "hit" not "semantic_hit"
    assert upstream.calls == 1, (
        f"expected upstream.calls==1 (exact cache hit on 2nd identical request), "
        f"got {upstream.calls}"
    )
    assert resp1.headers.get("x-cache") == "miss", (
        f"expected X-Cache: miss on first request, got: {resp1.headers.get('x-cache')!r}"
    )
    # REGRESSION ANCHOR: exact hit must be "hit" not "semantic_hit"
    assert resp2.headers.get("x-cache") == "hit", (
        f"expected X-Cache: hit (exact) on second identical request, "
        f"got: {resp2.headers.get('x-cache')!r}"
    )


# ===========================================================================
# SC9 — Ledger row for semantic hit carries cached=true, cost_usd=0, real token counts
#
# TRUE-RED REASON: semantic layer absent → second request (variant) goes to upstream,
#   usage row has non-zero cost → AssertionError: second_cost != 0.
# ===========================================================================


async def test_semantic_hit_ledger_cached_true_cost_zero(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    """Semantic hit usage row: cached=true in raw, cost_usd=0, token counts from warm response."""
    jwt, _tid = await signup_and_login(
        client, tenant_name="SemLedgerCo", email="owner@semledger.io"
    )
    key_info = await create_key(client, jwt, name="sem-ledger-key", cache_enabled=True)
    # TRUE-RED: "semantic_enabled" absent → AssertionError in enable_semantic_cache()
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Warm cache with P (exact miss)
    p_warm = completion_payload(active_model, content="Summarize the French Revolution.")
    resp1 = await client.post(COMPLETIONS, json=p_warm, headers=api_headers)
    assert resp1.status_code == 200

    # Normalized variant P' (should be semantic hit)
    p_variant = completion_payload(active_model, content="summarize the french revolution")
    resp2 = await client.post(COMPLETIONS, json=p_variant, headers=api_headers)
    assert resp2.status_code == 200

    # Allow fire-and-forget recording to propagate
    import asyncio
    await asyncio.sleep(0.1)

    rows = (
        await db_session.execute(
            text(
                "SELECT cost_usd, raw FROM usage_records"
                " WHERE key_id = :kid ORDER BY created_at ASC"
            ),
            {"kid": key_info["key_id"]},
        )
    ).fetchall()

    assert len(rows) == 2, f"expected 2 usage rows, found {len(rows)}"

    first_cost, _first_raw = rows[0]
    second_cost, second_raw = rows[1]

    # First row: normal cost (upstream call)
    assert first_cost > 0, (
        f"first usage row cost should be > 0, got {first_cost}"
    )

    # Second row: semantic hit → cost_usd=0, cached=true
    # TRUE-RED: without semantic layer, second row cost == first_cost (non-zero) → AssertionError
    assert second_cost == 0, (
        f"semantic hit usage row should have cost_usd=0, got {second_cost}"
    )
    assert second_raw.get("cached") is True, (
        f"semantic hit usage row raw should have cached=true, got: {second_raw}"
    )
    # Token counts from the cached warm response
    assert second_raw.get("usage", {}).get("prompt_tokens") == 10
    assert second_raw.get("usage", {}).get("completion_tokens") == 5


# ===========================================================================
# SC10 — gateway_cache_events_total{result="semantic_hit"} increments
#
# TRUE-RED REASON: _sample("semantic_hit") stays 0 (semantic layer absent; no label
#   "semantic_hit" is ever incremented). AssertionError: 0.0 + 1 != 0.0.
# ===========================================================================


async def test_semantic_hit_metric_increments(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """gateway_cache_events_total{result="semantic_hit"} increments on semantic hit."""
    jwt, _tid = await signup_and_login(
        client, tenant_name="SemMetricsCo", email="owner@semmetrics.io"
    )
    key_info = await create_key(client, jwt, name="sem-metrics-key", cache_enabled=True)
    # TRUE-RED: enable_semantic_cache() gate → AssertionError on semantic_enabled absent
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    metrics_reg = app.state.metrics_registry  # type: ignore[attr-defined]

    def _sample(label: str) -> float:
        for metric in metrics_reg.cache_events_total.collect():
            for sample in metric.samples:
                if sample.labels.get("result") == label:
                    return sample.value
        return 0.0

    sem_hit_before = _sample("semantic_hit")
    miss_before = _sample("miss")

    # First request: exact miss (also stores semantic key)
    p_warm = completion_payload(active_model, content="What is recursion?")
    r_miss = await client.post(COMPLETIONS, json=p_warm, headers=api_headers)
    assert r_miss.status_code == 200
    assert r_miss.headers.get("x-cache") == "miss"

    # Second request: normalized variant → semantic hit
    p_variant = completion_payload(active_model, content="what is recursion")
    r_sem = await client.post(COMPLETIONS, json=p_variant, headers=api_headers)
    assert r_sem.status_code == 200

    # TRUE-RED: semantic_hit counter does not increment without semantic layer
    assert _sample("semantic_hit") == sem_hit_before + 1, (
        f"gateway_cache_events_total{{result='semantic_hit'}} should increment by 1; "
        f"before={sem_hit_before}, after={_sample('semantic_hit')}"
    )
    assert _sample("miss") == miss_before + 1, (
        f"gateway_cache_events_total{{result='miss'}} should increment by 1 for first request"
    )


# ===========================================================================
# SC11 — PII re-masked on semantic hit body
#
# TRUE-RED REASON: semantic hit does not occur → test fails at upstream.calls==1
#   assertion (will be 2 without semantic layer). Also enable_semantic_cache() gate fires first.
# ===========================================================================


async def test_pii_remasked_on_semantic_hit(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """PII masking is applied to semantic-hit body (evaluate_post on cached body).

    The Redis-stored body is UNMASKED; PII masking is re-applied on every read —
    same contract as the v4 exact cache. This test verifies the semantic path follows
    the same PII contract.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="SemPIICo", email="owner@sempii.io"
    )
    key_info = await create_key(client, jwt, name="sem-pii-key", cache_enabled=True)
    # TRUE-RED PRIMARY GATE: enable_semantic_cache() → "semantic_enabled" absent → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    # Configure pii_mask guardrail for this tenant via PUT /admin/guardrails
    # (the guardrail evaluator is fake in tests — we use a fake upstream that returns PII body)
    upstream = FakeCompletionUpstream(body=UPSTREAM_BODY_PII)
    app.state.completion_upstream = upstream

    # Enable PII masking guardrail
    guardrail_resp = await client.put(
        "/admin/guardrails",
        json={"pii_mask": {"enabled": True, "mode": "mask"}},
        headers=auth_jwt(jwt),
    )
    assert guardrail_resp.status_code == 200, (
        f"PUT /admin/guardrails failed: {guardrail_resp.text}"
    )

    api_headers = auth_key(key_info["key"])

    # First request: warm cache (exact miss; body stored UNMASKED)
    p_warm = completion_payload(active_model, content="Tell me about John's email address.")
    resp1 = await client.post(COMPLETIONS, json=p_warm, headers=api_headers)
    assert resp1.status_code == 200
    assert resp1.headers.get("x-cache") == "miss"

    # Second request: normalized variant → should be semantic hit
    p_variant = completion_payload(active_model, content="tell me about john's email address")
    resp2 = await client.post(COMPLETIONS, json=p_variant, headers=api_headers)
    assert resp2.status_code == 200

    # TRUE-RED: semantic hit absent → upstream.calls==2 → AssertionError below
    assert upstream.calls == 1, (
        f"expected upstream.calls==1 (semantic hit), got {upstream.calls}"
    )
    assert resp2.headers.get("x-cache") == "semantic_hit", (
        f"expected X-Cache: semantic_hit, got: {resp2.headers.get('x-cache')!r}"
    )

    # PII contract: the returned body should have PII masked
    # (even though the Redis-stored body has raw "john@example.com")
    resp2_body = resp2.json()
    content = resp2_body.get("choices", [{}])[0].get("message", {}).get("content", "")
    # The fake guardrail evaluator in tests applies a simple placeholder
    # If pii masking is active, "john@example.com" should be replaced
    # Note: this assertion verifies the evaluate_post path was called on the cached body.
    # In tests with a real guardrail evaluator fake, PII masking replaces email patterns.
    # The exact placeholder depends on the guardrail implementation; we check it's not raw.
    assert "john@example.com" not in content, (
        f"PII should be masked on semantic hit body, "
        f"but raw PII found in response: {content!r}"
    )


# ===========================================================================
# SC12 — Admin toggle: PUT /admin/cache with semantic_enabled=true round-trip
#
# TRUE-RED REASON: PUT /admin/cache handler does not accept "semantic_enabled" field.
#   Either: 422 (field rejected) or silently ignored. GET does not return the field.
#   AssertionError: "semantic_enabled" not in GET response body.
# ===========================================================================


async def test_admin_toggle_semantic_enabled_roundtrip(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """PUT /admin/cache {"semantic_enabled": true} → 200; GET returns it; DB persisted."""
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="SemToggleCo", email="owner@semtoggle.io"
    )

    # Default: semantic_enabled should be false
    # TRUE-RED: "semantic_enabled" absent from GET response → AssertionError
    await assert_semantic_enabled_in_get(client, jwt, expected=False)

    # Toggle semantic_enabled on
    # TRUE-RED: "semantic_enabled" absent from PUT response → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    # GET should now reflect the updated state
    await assert_semantic_enabled_in_get(client, jwt, expected=True)

    # Verify DB persistence
    row = (
        await db_session.execute(
            text(
                "SELECT semantic_cache_enabled FROM tenants WHERE id = :tid"
            ),
            {"tid": tenant_id},
        )
    ).fetchone()
    assert row is not None, "tenant row not found"
    # TRUE-RED: column semantic_cache_enabled does not exist → DB error or None
    assert row[0] is True, (
        f"DB semantic_cache_enabled should be True, got: {row[0]!r}"
    )

    # Toggle back off
    put_off = await client.put(
        ADMIN_CACHE,
        json={"semantic_enabled": False},
        headers=auth_jwt(jwt),
    )
    assert put_off.status_code == 200
    resp_off_body = put_off.json()
    assert "semantic_enabled" in resp_off_body
    assert resp_off_body["semantic_enabled"] is False

    await assert_semantic_enabled_in_get(client, jwt, expected=False)


# ===========================================================================
# SC13 — Member role 403 on PUT /admin/cache (with semantic_enabled in body)
#
# TRUE-RED REASON: require_owner_or_admin fires BEFORE body parsing → this test is
#   GREEN-by-design today (the 403 auth check runs before semantic_enabled validation).
#   Included as a REGRESSION ANCHOR: if a future build changes the auth/body-parse order,
#   this test catches it. Explicitly marked with a note.
# ===========================================================================


async def test_member_role_forbidden_on_semantic_toggle(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
) -> None:
    """PUT /admin/cache with member-role JWT → 403 ERR_AUTH_FORBIDDEN.

    REGRESSION ANCHOR: require_owner_or_admin fires before body parsing, so this
    test is green-by-design. Included to ensure auth order is never broken by the build.
    """
    _owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="SemMemberForbidCo", email="owner@semmemberforbid.io"
    )

    member_user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', 'member')"
        ),
        {"id": member_user_id, "tid": tenant_id, "email": "member@semmemberforbid.io"},
    )
    await db_session.commit()

    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member@semmemberforbid.io",
    )

    resp = await client.put(
        ADMIN_CACHE,
        json={"semantic_enabled": True},
        headers=auth_jwt(member_jwt),
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# SC14 — Streaming bypasses semantic layer entirely
#
# TRUE-RED REASON: assert_semantic_enabled_in_get() gate → AssertionError.
#   Post-build: this should remain GREEN (regression anchor — streaming never cached).
# ===========================================================================


async def test_streaming_bypasses_semantic_layer(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Streaming requests bypass semantic layer; X-Cache: semantic_hit never appears."""
    jwt, _tid = await signup_and_login(
        client, tenant_name="SemStreamCo", email="owner@semstream.io"
    )
    key_info = await create_key(client, jwt, name="sem-stream-key", cache_enabled=True)
    # TRUE-RED: assert_semantic_enabled_in_get() gate → AssertionError (field absent)
    await assert_semantic_enabled_in_get(client, jwt, expected=False)
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])

    # Two streaming requests with normalized-variant prompts
    p_stream1 = completion_payload(
        active_model, content="  Explain deep learning.  ", stream=True
    )
    p_stream2 = completion_payload(
        active_model, content="explain deep learning", stream=True
    )

    resp1 = await client.post(COMPLETIONS, json=p_stream1, headers=api_headers)
    resp2 = await client.post(COMPLETIONS, json=p_stream2, headers=api_headers)

    assert resp1.status_code == 200
    assert resp2.status_code == 200

    # Streaming: both calls must hit upstream (semantic layer bypassed)
    assert upstream.calls == 2, (
        f"streaming requests should always call upstream, got calls={upstream.calls}"
    )

    # No X-Cache header on streaming responses
    assert "x-cache" not in resp1.headers, (
        f"X-Cache must not be set on streaming response: "
        f"{resp1.headers.get('x-cache')!r}"
    )
    assert "x-cache" not in resp2.headers, (
        f"X-Cache must not be set on streaming response: "
        f"{resp2.headers.get('x-cache')!r}"
    )
    assert resp2.headers.get("x-cache") != "semantic_hit", (
        f"streaming response must never get X-Cache: semantic_hit"
    )


# ===========================================================================
# SC15 — Cache-Control: no-cache bypasses semantic layer and re-stores both keys
#
# TRUE-RED REASON: enable_semantic_cache() gate → "semantic_enabled" absent → AssertionError.
#   If that passes: after bypass, semantic key is not stored (semantic layer absent) →
#   subsequent variant request misses → upstream.calls==3 not 2 → AssertionError.
# ===========================================================================


async def test_bypass_restores_semantic_key(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """Cache-Control: no-cache bypasses semantic layer; bypass re-stores both exact and semantic keys.

    After a bypass on a normalized variant P', a subsequent P' request (no bypass) must
    get X-Cache: semantic_hit — proving that the bypass stored the semantic key.
    """
    jwt, _tid = await signup_and_login(
        client, tenant_name="SemBypassCo", email="owner@sembypass.io"
    )
    key_info = await create_key(client, jwt, name="sem-bypass-key", cache_enabled=True)
    # TRUE-RED: enable_semantic_cache() → "semantic_enabled" absent → AssertionError
    await enable_semantic_cache(client, jwt, semantic_enabled=True, cache_enabled=True)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream
    api_headers = auth_key(key_info["key"])
    bypass_headers = {**api_headers, "Cache-Control": "no-cache"}

    # First: warm with exact prompt (miss)
    p_exact = completion_payload(active_model, content="What is entropy?")
    resp_warm = await client.post(COMPLETIONS, json=p_exact, headers=api_headers)
    assert resp_warm.status_code == 200
    assert resp_warm.headers.get("x-cache") == "miss"
    assert upstream.calls == 1

    # Bypass with normalized variant P'
    p_variant = completion_payload(active_model, content="what is entropy")
    resp_bypass = await client.post(COMPLETIONS, json=p_variant, headers=bypass_headers)
    assert resp_bypass.status_code == 200
    # Bypass header on response
    assert resp_bypass.headers.get("x-cache") == "bypass", (
        f"expected X-Cache: bypass, got: {resp_bypass.headers.get('x-cache')!r}"
    )
    assert upstream.calls == 2, (
        f"bypass should call upstream (calls==2), got {upstream.calls}"
    )

    # Subsequent P' request without bypass → semantic_hit (bypass re-stored semantic key)
    resp_after = await client.post(COMPLETIONS, json=p_variant, headers=api_headers)
    assert resp_after.status_code == 200
    # TRUE-RED: semantic layer absent → semantic key not stored after bypass → MISS
    # upstream.calls == 3 → AssertionError: 3 != 2
    assert upstream.calls == 2, (
        f"after bypass re-store, variant should hit semantic cache (no new upstream call), "
        f"got {upstream.calls}"
    )
    assert resp_after.headers.get("x-cache") == "semantic_hit", (
        f"expected X-Cache: semantic_hit after bypass re-store, "
        f"got: {resp_after.headers.get('x-cache')!r}"
    )

