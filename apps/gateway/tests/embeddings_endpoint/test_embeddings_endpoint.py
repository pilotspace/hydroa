"""Failing-first (RED) suite for embeddings-endpoint (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/embeddings-endpoint/TASK.md §2 (EM1–EM11).

Right-reason red targets:
  - EM1–EM9: POST /v1/embeddings route does not exist → 404 Not Found (or ImportError
    if the use-case / governance helper import is attempted inside the handler).
  - EM10: NonChatGovernance does not exist → ImportError at import time.
  - EM11: GREEN-BY-DESIGN — the chat path already works; this is a regression guard
    that must remain green after the BUILD turn to prove chat was not touched.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (shared; flushed per test where needed)
  - httpx.ASGITransport (no network)

Run only this suite:
  cd apps/gateway && uv run pytest tests/embeddings_endpoint/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.embeddings_endpoint.conftest import (
    CHAT_MODEL_ID,
    CHAT_RESPONSE_BODY,
    EMBED_MODEL_ID,
    EMBEDDING_RESPONSE_BODY,
    FakeCompletionUpstream,
    FakeUpstreamProvider,
    SpyRecorder,
    inject_fake_openai_provider,
    seed_chat_model,
    seed_embedding_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMBEDDINGS_PATH = "/v1/embeddings"
COMPLETIONS_PATH = "/v1/chat/completions"


def _auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; body: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


# ---------------------------------------------------------------------------
# EM1 — happy path: valid key + active embedding model → 200 with provider body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em1_happy_path_200_with_provider_body(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """POST /v1/embeddings with valid credentials and an active embedding model.

    RED reason: POST /v1/embeddings route does not exist → 404 Not Found.
    The test will fail with AssertionError: expected HTTP 200, got 404.
    """
    await seed_embedding_model(db_session)
    fake_provider = inject_fake_openai_provider(app)

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "hello world"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert resp.json() == EMBEDDING_RESPONSE_BODY

    assert len(fake_provider.post_json_calls) == 1
    call = fake_provider.post_json_calls[0]
    assert call["path"] == "/embeddings"
    assert call["payload"]["model"] == EMBED_MODEL_ID
    assert call["payload"]["input"] == "hello world"


# ---------------------------------------------------------------------------
# EM2 — single usage record per request, per_token, usage carried from provider
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em2_single_usage_record_per_token_usage_carried(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Exactly ONE usage record is fired; usage dict carries prompt_tokens from provider.

    RED reason: route absent → 404.
    Once the route exists, this test ensures the recorder is called exactly once
    and that it receives the upstream usage (per_token default, no pricing_unit extra).
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "embed me"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"

    # Allow the fire-and-forget task a chance to run in the test event loop.
    import asyncio
    await asyncio.sleep(0)

    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"

    last = spy.last_call
    assert last["model"] == EMBED_MODEL_ID
    usage = last.get("usage") or {}
    assert usage.get("prompt_tokens") == 5
    assert usage.get("total_tokens") == 5

    # No pricing_unit extra — per_token is the default and must NOT be passed explicitly.
    assert "pricing_unit" not in last, (
        "pricing_unit must NOT be passed for embeddings — per_token is the recorder default"
    )


# ---------------------------------------------------------------------------
# EM3 — missing API key → 401 ERR_AUTH_INVALID_KEY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em3_missing_api_key_401(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """No Authorization header → 401 ERR_AUTH_INVALID_KEY.

    RED reason: route absent → 404 instead of 401.
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "test"},
    )

    _assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# EM4 — model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em4_model_not_in_allowlist_403(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Model not in the key's allowlist → 403 ERR_MODEL_NOT_ALLOWED.

    RED reason: route absent → 404 instead of 403.
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    # Update the key to have a restrictive allowlist
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"model_allowlist": ["some-other-model-only"]},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")


# ---------------------------------------------------------------------------
# EM5 — unknown / inactive model → 400 ERR_MODEL_UNKNOWN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em5_unknown_model_400(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Model absent from catalog → 400 ERR_MODEL_UNKNOWN.

    RED reason: route absent → 404 instead of 400.
    """
    inject_fake_openai_provider(app)
    # Intentionally do NOT seed any model row for this id.
    unknown_model = "nonexistent-embedding-model-xyz"

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": unknown_model, "input": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_MODEL_UNKNOWN")


# ---------------------------------------------------------------------------
# EM6 — budget exceeded → 402 ERR_BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em6_budget_exceeded_402(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Key budget exhausted → 402 ERR_BUDGET_EXCEEDED.

    Seeds the per-key Redis spend counter above the key's monthly_budget_usd.
    Uses the RedisBudgetGuard already wired to app.state.budget_guard.

    RED reason: route absent → 404 instead of 402.
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    # Set a tiny monthly budget on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"monthly_budget_usd": "0.01"},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the Redis spend counter above the budget.
    import datetime
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:key:{api_key_info['key_id']}:{yyyymm}"

    redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        await redis.set(spend_key, "9999.99")

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "test"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")


# ---------------------------------------------------------------------------
# EM7 — RPM over limit → 429 ERR_RATE_LIMITED + Retry-After
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em7_rpm_exceeded_429_retry_after(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """RPM rate limit exceeded → 429 ERR_RATE_LIMITED with Retry-After header.

    Sets rpm_limit=1 on the key, then seeds the Redis RPM sliding window to
    indicate the limit is already consumed.

    RED reason: route absent → 404 instead of 429.
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    # Set rpm_limit=1 on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"rpm_limit": 1},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the RPM zset to simulate the window being full.
    # The RedisLuaRateLimiter uses key: ratelimit:rpm:{key_id}
    import time
    now_ms = int(time.time() * 1000)
    rpm_key = f"ratelimit:rpm:{api_key_info['key_id']}"

    redis = getattr(getattr(app.state, "rate_limiter", None), "_redis", None)
    if redis is None:
        redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        # Add an entry within the last 60 seconds to fill the window
        await redis.zadd(rpm_key, {str(now_ms - 1000).encode(): now_ms - 1000})

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 429, "ERR_RATE_LIMITED")
    assert "retry-after" in resp.headers or "Retry-After" in resp.headers, (
        "Expected Retry-After header on 429 response"
    )


# ---------------------------------------------------------------------------
# EM8 — provider absent → 503 ERR_PROVIDER_UNAVAILABLE; chat path unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em8_provider_absent_503_chat_unaffected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """OpenAI provider absent from registry → 503; subsequent chat request still works.

    The registry is rebuilt with ONLY 'openrouter' — no 'openai' entry.
    Part A: embeddings → 503 ERR_PROVIDER_UNAVAILABLE.
    Part B: chat → 200 (FakeCompletionUpstream still serves it).

    RED reason: route absent → 404 instead of 503.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    await seed_embedding_model(db_session)
    await seed_chat_model(db_session)

    # Registry with ONLY 'openrouter' — no 'openai'
    existing_openrouter = getattr(app.state, "provider_registry", None)
    openrouter_entry = None
    if existing_openrouter is not None:
        openrouter_entry = existing_openrouter.get("openrouter")

    providers: dict[str, Any] = {}
    if openrouter_entry is not None:
        providers["openrouter"] = openrouter_entry

    app.state.provider_registry = ProviderRegistry(providers)

    # Inject FakeCompletionUpstream for the chat path
    fake_chat = FakeCompletionUpstream()
    app.state.completion_upstream = fake_chat

    # Part A: embeddings must return 503
    resp_embed = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID, "input": "test"},
        headers=_auth_key(api_key_info["key"]),
    )
    _assert_problem(resp_embed, 503, "ERR_PROVIDER_UNAVAILABLE")

    # Part B: chat must still return 200
    resp_chat = await client.post(
        COMPLETIONS_PATH,
        json={
            "model": CHAT_MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=_auth_key(api_key_info["key"]),
    )
    assert resp_chat.status_code == 200, (
        f"chat path should be unaffected; got {resp_chat.status_code}: {resp_chat.text}"
    )
    assert fake_chat.call_count >= 1, "FakeCompletionUpstream.complete() was not called"


# ---------------------------------------------------------------------------
# EM9 — missing input field → 422 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em9_missing_input_field_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Body without 'input' field → 422 ERR_PAYLOAD_INVALID.

    RED reason: route absent → 404 instead of 422.
    """
    await seed_embedding_model(db_session)
    inject_fake_openai_provider(app)

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": EMBED_MODEL_ID},  # no 'input'
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# EM10 — NonChatGovernance helper unit test (import-level red)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em10_non_chat_governance_helper_unit_test() -> None:
    """Unit test for NonChatGovernance — tests ordered checks and exact error codes.

    RED reason: NonChatGovernance does not exist in proxy/application/governance.py
    → ImportError at the import statement below. This is the right-reason failure:
    the module must be created in the BUILD turn.

    Once the module exists, this test verifies:
      - authorize(good_key, model_id) → returns AuthzResult
      - authorize(None, model_id) → raises ProblemError 401 ERR_AUTH_INVALID_KEY
      - authorize(good_key, model_not_in_allowlist) → raises 403 ERR_MODEL_NOT_ALLOWED
      - authorize(good_key, model_id) with budget exceeded → raises 402 ERR_BUDGET_EXCEEDED
    """
    import uuid
    from datetime import datetime, UTC
    from decimal import Decimal

    # This import WILL FAIL (ImportError) because governance.py does not exist yet.
    # That is the correct RED reason for this test.
    from gateway.proxy.application.governance import NonChatGovernance  # type: ignore[import]

    from gateway.core.errors import ProblemError
    from gateway.keys.domain.entities import AuthzResult
    from gateway.keys.domain.errors import InvalidApiKeyError
    from gateway.proxy.domain.ports import ModelAccess
    from gateway.rate_limits.application.passthrough import PassthroughRateLimiter

    GOOD_KEY = "sk-em-test-good"
    BAD_KEY = "sk-em-test-bad"
    TENANT_ID = uuid.uuid4()
    KEY_ID = uuid.uuid4()
    MODEL_ID = "text-embedding-3-small"
    BLOCKED_MODEL = "blocked-model"

    GOOD_AUTHZ = AuthzResult(
        tenant_id=TENANT_ID,
        key_id=KEY_ID,
        expires_at=None,
        model_allowlist=None,  # None = all models allowed
        monthly_budget_usd=None,
        rpm_limit=None,
        tpm_limit=None,
    )

    RESTRICTED_AUTHZ = AuthzResult(
        tenant_id=TENANT_ID,
        key_id=KEY_ID,
        expires_at=None,
        model_allowlist=["some-other-model"],  # does not include BLOCKED_MODEL or MODEL_ID
        monthly_budget_usd=None,
        rpm_limit=None,
        tpm_limit=None,
    )

    class FakeAuthenticator:
        async def authenticate(self, raw_key: str) -> AuthzResult:
            if raw_key == GOOD_KEY:
                return GOOD_AUTHZ
            if raw_key == "sk-em-restricted":
                return RESTRICTED_AUTHZ
            raise InvalidApiKeyError("invalid key")

    class FakeModelChecker:
        async def is_active(self, model_id: str) -> bool:
            return True

        async def check_for_tenant(
            self, model_id: str, tenant_id: uuid.UUID
        ) -> ModelAccess:
            return ModelAccess.ACTIVE

    class FakeBudgetGuardOk:
        async def check(self, tenant_id: uuid.UUID) -> None:
            pass  # allow

    class FakeBudgetGuardExceeded:
        async def check(self, tenant_id: uuid.UUID) -> None:
            from gateway.core.error_catalog import BUDGET_EXCEEDED

            raise BUDGET_EXCEEDED.exc(detail="test budget exceeded")

    # --- Test 1: happy path returns AuthzResult ---
    governance_ok = NonChatGovernance(
        authenticator=FakeAuthenticator(),
        model_checker=FakeModelChecker(),
        budget_guard=FakeBudgetGuardOk(),
        rate_limiter=PassthroughRateLimiter(),
        redis_client=None,
    )
    result = await governance_ok.authorize(GOOD_KEY, MODEL_ID, estimated_tokens=1)
    assert isinstance(result, AuthzResult), f"expected AuthzResult, got {type(result)}"
    assert result.tenant_id == TENANT_ID

    # --- Test 2: missing/invalid key → 401 ERR_AUTH_INVALID_KEY ---
    with pytest.raises(ProblemError) as exc_info:
        await governance_ok.authorize(None, MODEL_ID)
    err = exc_info.value
    assert err.status == 401, f"expected 401, got {err.status}"
    assert err.code == "ERR_AUTH_INVALID_KEY", f"expected ERR_AUTH_INVALID_KEY, got {err.code}"

    with pytest.raises(ProblemError) as exc_info:
        await governance_ok.authorize(BAD_KEY, MODEL_ID)
    err = exc_info.value
    assert err.status == 401
    assert err.code == "ERR_AUTH_INVALID_KEY"

    # --- Test 3: model not in allowlist → 403 ERR_MODEL_NOT_ALLOWED ---
    governance_restricted = NonChatGovernance(
        authenticator=FakeAuthenticator(),
        model_checker=FakeModelChecker(),
        budget_guard=FakeBudgetGuardOk(),
        rate_limiter=PassthroughRateLimiter(),
        redis_client=None,
    )
    with pytest.raises(ProblemError) as exc_info:
        await governance_restricted.authorize("sk-em-restricted", MODEL_ID)
    err = exc_info.value
    assert err.status == 403, f"expected 403, got {err.status}"
    assert err.code == "ERR_MODEL_NOT_ALLOWED", (
        f"expected ERR_MODEL_NOT_ALLOWED, got {err.code}"
    )

    # --- Test 4: budget exceeded → 402 ERR_BUDGET_EXCEEDED ---
    governance_budget = NonChatGovernance(
        authenticator=FakeAuthenticator(),
        model_checker=FakeModelChecker(),
        budget_guard=FakeBudgetGuardExceeded(),
        rate_limiter=PassthroughRateLimiter(),
        redis_client=None,
    )
    with pytest.raises(ProblemError) as exc_info:
        await governance_budget.authorize(GOOD_KEY, MODEL_ID, estimated_tokens=1)
    err = exc_info.value
    assert err.status == 402, f"expected 402, got {err.status}"
    assert err.code == "ERR_BUDGET_EXCEEDED", f"expected ERR_BUDGET_EXCEEDED, got {err.code}"


# ---------------------------------------------------------------------------
# EM11 — regression: chat path 200s; governance extraction did not touch it
# GREEN-BY-DESIGN: this test passes even in the RED phase (chat already works).
# It is included here as a regression guard against the BUILD accidentally
# modifying proxy/api/router.py, proxy/api/deps.py, or proxy/application/use_cases.py.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_em11_regression_chat_path_200_untouched(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Chat path is unaffected by the embeddings task build.

    GREEN-BY-DESIGN: the chat route already exists and the FakeCompletionUpstream
    is already injectable via app.state.completion_upstream. This test must remain
    green before AND after the BUILD turn.

    If this test turns red after BUILD, it means use_cases.py, router.py, or deps.py
    was accidentally modified — a contract violation (INVIOLABLE constraint in §3).
    """
    await seed_chat_model(db_session)

    fake_chat = FakeCompletionUpstream()
    app.state.completion_upstream = fake_chat

    resp = await client.post(
        COMPLETIONS_PATH,
        json={
            "model": CHAT_MODEL_ID,
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, (
        f"chat path regression: expected 200, got {resp.status_code}: {resp.text}"
    )
    assert resp.json() == CHAT_RESPONSE_BODY, (
        f"chat response body mismatch: {resp.json()}"
    )
    assert fake_chat.call_count == 1, (
        f"FakeCompletionUpstream.complete() should have been called once, got {fake_chat.call_count}"
    )
