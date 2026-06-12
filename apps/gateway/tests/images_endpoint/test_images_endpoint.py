"""Failing-first (RED) suite for images-endpoint (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/images-endpoint/TASK.md §2 (IM1–IM10).

Right-reason red targets:
  - IM1–IM9b: POST /v1/images/generations route does not exist → 404 Not Found.
  - IM10: GREEN-BY-DESIGN — the chat path already works; this is a regression guard
    that must remain green after the BUILD turn to prove chat was not touched.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (shared; flushed per test where needed)
  - httpx.ASGITransport (no network)

Run only this suite:
  cd apps/gateway && uv run pytest tests/images_endpoint/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

import asyncio
import datetime
import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.images_endpoint.conftest import (
    CHAT_MODEL_ID,
    CHAT_RESPONSE_BODY,
    IMAGE_MODEL_ID,
    IMAGE_RESPONSE_BODY,
    IMAGE_RESPONSE_BODY_2,
    FakeCompletionUpstream,
    FakeUpstreamProvider,
    SpyRecorder,
    inject_fake_openai_provider,
    seed_chat_model,
    seed_image_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IMAGES_PATH = "/v1/images/generations"
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
# IM1 — happy path: valid key + active image model → 200 with provider body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im1_happy_path_200_with_provider_body(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """POST /v1/images/generations with valid credentials and an active image model.

    RED reason: POST /v1/images/generations route does not exist → 404 Not Found.
    The test will fail with AssertionError: expected HTTP 200, got 404.
    """
    await seed_image_model(db_session)
    fake_provider = inject_fake_openai_provider(app)

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "a white cat"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert resp.json() == IMAGE_RESPONSE_BODY

    assert len(fake_provider.post_json_calls) == 1
    call = fake_provider.post_json_calls[0]
    assert call["path"] == "/images/generations"
    assert call["payload"]["model"] == IMAGE_MODEL_ID
    assert call["payload"]["prompt"] == "a white cat"


# ---------------------------------------------------------------------------
# IM2 — single usage record, per_image pricing, quantity == actual returned images
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im2_single_usage_record_per_image_quantity_actual_returned(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Exactly ONE usage record; pricing_unit='per_image'; quantity==Decimal(2) actual returned.

    The FakeUpstreamProvider is configured to return IMAGE_RESPONSE_BODY_2 (data with 2 entries).
    The billed quantity MUST equal len(data) == 2, not the requested n.

    RED reason: route absent → 404.
    Once the route exists, this test verifies the billed-quantity resolution formula
    (resolved at freeze — no requested-n fallback; bill exactly what was returned):
      n_images = len(resp_body.get("data", []))
    """
    await seed_image_model(db_session)

    fake_provider = FakeUpstreamProvider()
    fake_provider.set_post_json_response(200, IMAGE_RESPONSE_BODY_2)
    inject_fake_openai_provider(app, fake_provider)

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "two cats", "n": 2},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"

    # Allow the fire-and-forget task a chance to run in the test event loop.
    await asyncio.sleep(0)

    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"

    last = spy.last_call
    assert last["model"] == IMAGE_MODEL_ID
    assert last.get("usage") is None, "usage must be None for images (no token dimension)"
    assert last.get("pricing_unit") == "per_image", (
        f"expected pricing_unit='per_image', got {last.get('pricing_unit')!r}"
    )
    assert last.get("quantity") == Decimal(2), (
        f"expected quantity=Decimal(2) (2 images returned), got {last.get('quantity')!r}"
    )


# ---------------------------------------------------------------------------
# IM3 — missing API key → 401 ERR_AUTH_INVALID_KEY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im3_missing_api_key_401(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """No Authorization header → 401 ERR_AUTH_INVALID_KEY.

    RED reason: route absent → 404 instead of 401.
    """
    await seed_image_model(db_session)
    inject_fake_openai_provider(app)

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "test"},
    )

    _assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# IM4 — model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im4_model_not_in_allowlist_403(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Model not in the key's allowlist → 403 ERR_MODEL_NOT_ALLOWED.

    RED reason: route absent → 404 instead of 403.
    """
    await seed_image_model(db_session)
    inject_fake_openai_provider(app)

    # Update the key to have a restrictive allowlist
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"model_allowlist": ["some-other-model-only"]},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")


# ---------------------------------------------------------------------------
# IM5 — unknown / inactive model → 400 ERR_MODEL_UNKNOWN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im5_unknown_model_400(
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
    unknown_model = "nonexistent-image-model-xyz"

    resp = await client.post(
        IMAGES_PATH,
        json={"model": unknown_model, "prompt": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_MODEL_UNKNOWN")


# ---------------------------------------------------------------------------
# IM6 — budget exceeded → 402 ERR_BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im6_budget_exceeded_402(
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
    await seed_image_model(db_session)
    inject_fake_openai_provider(app)

    # Set a tiny monthly budget on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"monthly_budget_usd": "0.01"},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the Redis spend counter above the budget.
    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:key:{api_key_info['key_id']}:{yyyymm}"

    redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        await redis.set(spend_key, "9999.99")

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "test"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")


# ---------------------------------------------------------------------------
# IM7 — RPM over limit → 429 ERR_RATE_LIMITED + Retry-After; TPM NOT consulted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im7_rpm_exceeded_429_retry_after(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """RPM rate limit exceeded → 429 ERR_RATE_LIMITED with Retry-After header.

    Sets rpm_limit=1 on the key, seeds the Redis RPM window to indicate the limit
    is already consumed. TPM is NOT consulted because estimated_tokens=None is passed
    to NonChatGovernance.authorize — this is the correct behavior for images.

    RED reason: route absent → 404 instead of 429.
    """
    await seed_image_model(db_session)
    inject_fake_openai_provider(app)

    # Set rpm_limit=1 on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"rpm_limit": 1},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the RPM zset to simulate the window being full.
    # The RedisLuaRateLimiter uses key: ratelimit:rpm:{key_id}
    now_ms = int(time.time() * 1000)
    rpm_key = f"ratelimit:rpm:{api_key_info['key_id']}"

    redis = getattr(getattr(app.state, "rate_limiter", None), "_redis", None)
    if redis is None:
        redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        # Add an entry within the last 60 seconds to fill the window
        await redis.zadd(rpm_key, {str(now_ms - 1000).encode(): now_ms - 1000})

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "hi"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 429, "ERR_RATE_LIMITED")
    assert "retry-after" in resp.headers or "Retry-After" in resp.headers, (
        "Expected Retry-After header on 429 response"
    )


# ---------------------------------------------------------------------------
# IM8 — provider absent → 503 ERR_PROVIDER_UNAVAILABLE; chat path unaffected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im8_provider_absent_503_chat_unaffected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """OpenAI provider absent from registry → 503; subsequent chat request still works.

    The registry is rebuilt with ONLY 'openrouter' — no 'openai' entry.
    Part A: images → 503 ERR_PROVIDER_UNAVAILABLE.
    Part B: chat → 200 (FakeCompletionUpstream still serves it).

    RED reason: route absent → 404 instead of 503.
    """
    from gateway.proxy.infrastructure.provider_registry import (  # type: ignore[import]
        ProviderRegistry,
    )

    await seed_image_model(db_session)
    await seed_chat_model(db_session)

    # Registry with ONLY 'openrouter' — no 'openai'
    existing_registry = getattr(app.state, "provider_registry", None)
    openrouter_entry = None
    if existing_registry is not None:
        openrouter_entry = existing_registry.get("openrouter")

    providers: dict[str, Any] = {}
    if openrouter_entry is not None:
        providers["openrouter"] = openrouter_entry

    app.state.provider_registry = ProviderRegistry(providers)

    # Inject FakeCompletionUpstream for the chat path
    fake_chat = FakeCompletionUpstream()
    app.state.completion_upstream = fake_chat

    # Part A: images must return 503
    resp_image = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID, "prompt": "test"},
        headers=_auth_key(api_key_info["key"]),
    )
    _assert_problem(resp_image, 503, "ERR_PROVIDER_UNAVAILABLE")

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
# IM9 — missing prompt field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_PROMPT_REQUIRED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im9_missing_prompt_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Body without 'prompt' field → 422 ERR_PAYLOAD_INVALID.

    RED reason: route absent → 404 instead of 422.
    """
    await seed_image_model(db_session)
    inject_fake_openai_provider(app)

    resp = await client.post(
        IMAGES_PATH,
        json={"model": IMAGE_MODEL_ID},  # no 'prompt'
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# IM9b — missing model field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_MODEL_REQUIRED)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im9b_missing_model_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Body without 'model' field → 422 ERR_PAYLOAD_INVALID.

    RED reason: route absent → 404 instead of 422.
    """
    inject_fake_openai_provider(app)

    resp = await client.post(
        IMAGES_PATH,
        json={"prompt": "a cat"},  # no 'model'
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# IM10 — regression: chat path 200s; images task did not touch chat
# GREEN-BY-DESIGN: this test passes even in the RED phase (chat already works).
# It is included here as a regression guard against the BUILD accidentally
# modifying proxy/api/router.py, proxy/api/deps.py, proxy/application/use_cases.py,
# proxy/application/governance.py, or any existing embeddings file.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_im10_regression_chat_path_200_untouched(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Chat path is unaffected by the images task build.

    GREEN-BY-DESIGN: the chat route already exists and the FakeCompletionUpstream
    is already injectable via app.state.completion_upstream. This test must remain
    green before AND after the BUILD turn.

    If this test turns red after BUILD, it means use_cases.py, router.py, deps.py,
    or governance.py was accidentally modified — a contract violation (INVIOLABLE
    constraint in §3).
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
    assert resp.json() == CHAT_RESPONSE_BODY, f"chat response body mismatch: {resp.json()}"
    assert fake_chat.call_count == 1, (
        f"FakeCompletionUpstream.complete() should have been called once,"
        f" got {fake_chat.call_count}"
    )
