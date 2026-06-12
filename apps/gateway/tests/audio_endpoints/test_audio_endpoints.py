"""Failing-first (RED) suite for audio-endpoints (contract DRAFT, TASK.md §4).

One test per scenario in .add/tasks/audio-endpoints/TASK.md §2 (AU1–AU9b, AT1–AT6, regression).

Right-reason red targets:
  - AU1–AU9b (11 STT tests): POST /v1/audio/transcriptions route absent → 404 Not Found.
  - AT1–AT6  (6 TTS tests):  POST /v1/audio/speech route absent → 404 Not Found.
  - AU/AT regression: GREEN-BY-DESIGN — the chat path already works; this is a regression
    guard that must remain green after BUILD to prove chat was not touched.

Infrastructure:
  - Real Postgres at postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test
  - Real Redis at redis://localhost:6380 db 9 (shared; flushed per test where needed)
  - httpx.ASGITransport (no network)

Run only this suite:
  cd apps/gateway && uv run pytest tests/audio_endpoints/ -q --no-cov -p no:cacheprovider
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tests.audio_endpoints.conftest import (
    CHAT_MODEL_ID,
    CHAT_RESPONSE_BODY,
    FAKE_AUDIO_BYTES,
    STT_MODEL_ID,
    STT_RESPONSE_BODY,
    STT_RESPONSE_NO_DURATION,
    TTS_AUDIO_BYTES,
    TTS_MODEL_ID,
    FakeAudioProvider,
    FakeCompletionUpstream,
    SpyRecorder,
    inject_fake_openai_audio_provider,
    seed_chat_model,
    seed_stt_model,
    seed_tts_model,
)

# ---------------------------------------------------------------------------
# Route path constants
# ---------------------------------------------------------------------------

TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
SPEECH_PATH = "/v1/audio/speech"
COMPLETIONS_PATH = "/v1/chat/completions"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _multipart_files(
    filename: str = "audio.mp3",
    content: bytes = FAKE_AUDIO_BYTES,
    content_type: str = "audio/mpeg",
) -> dict[str, Any]:
    """Build the files dict for httpx multipart upload."""
    return {"file": (filename, content, content_type)}


# ---------------------------------------------------------------------------
# AU1 — STT happy path: valid key + active audio_stt model → 200, provider body
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au1_happy_path_200_post_multipart_called(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """POST /v1/audio/transcriptions with valid credentials and an active STT model.

    RED reason: POST /v1/audio/transcriptions route does not exist → 404 Not Found.
    """
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_BODY)

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert resp.json() == STT_RESPONSE_BODY

    assert len(fake_provider.post_multipart_calls) == 1
    call = fake_provider.post_multipart_calls[0]
    assert call["path"] == "/audio/transcriptions"
    assert "file" in call["files"]
    assert call["data"]["model"] == STT_MODEL_ID


# ---------------------------------------------------------------------------
# AU2 — STT billing: duration from body["duration"] → quantity=Decimal("12.5")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au2_single_record_per_second_quantity_from_duration(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Record fires once with pricing_unit=per_second and quantity from duration field.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_BODY)  # duration=12.5

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"

    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second", f"unexpected pricing_unit: {rec}"
    assert rec.get("quantity") == Decimal("12.5"), f"unexpected quantity: {rec}"
    assert rec.get("model") == STT_MODEL_ID
    assert rec.get("status") == 200


# ---------------------------------------------------------------------------
# AU2b — STT duration absent → quantity=0, no raise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au2b_duration_absent_quantity_zero_no_raise(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """When upstream body has no 'duration' key, quantity=0, response is still 200.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_multipart_response(200, STT_RESPONSE_NO_DURATION)  # no duration

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"

    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_second"
    assert rec.get("quantity") == Decimal("0"), (
        f"expected Decimal('0') when duration absent, got {rec.get('quantity')}"
    )


# ---------------------------------------------------------------------------
# AU3 — STT bad API key → 401 ERR_AUTH_INVALID_KEY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au3_bad_key_401(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """No Authorization header → 401 ERR_AUTH_INVALID_KEY.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        # No Authorization header
    )

    _assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# AU4 — STT model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au4_model_not_in_allowlist_403(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Model not in key allowlist → 403 ERR_MODEL_NOT_ALLOWED.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    # Restrict key to a different model
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"model_allowlist": ["other-model"]},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 403, "ERR_MODEL_NOT_ALLOWED")


# ---------------------------------------------------------------------------
# AU5 — STT unknown model → 400 ERR_MODEL_UNKNOWN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au5_unknown_model_400(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Unknown model → 400 ERR_MODEL_UNKNOWN.

    RED reason: route absent → 404.
    """
    inject_fake_openai_audio_provider(app)
    # No model row seeded for "nonexistent-stt-model"

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": "nonexistent-stt-model"},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_MODEL_UNKNOWN")


# ---------------------------------------------------------------------------
# AU6 — STT budget exceeded → 402 ERR_BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au6_budget_exceeded_402(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Key budget exhausted → 402 ERR_BUDGET_EXCEEDED.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    # Set a tiny monthly budget on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"monthly_budget_usd": "0.01"},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the Redis spend counter above the budget
    import datetime

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    spend_key = f"usage:spend:key:{api_key_info['key_id']}:{yyyymm}"

    redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        await redis.set(spend_key, "9999.99")

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 402, "ERR_BUDGET_EXCEEDED")


# ---------------------------------------------------------------------------
# AU7 — STT RPM exceeded → 429 ERR_RATE_LIMITED + Retry-After
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au7_rpm_exceeded_429_retry_after(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """RPM rate limit exceeded → 429 ERR_RATE_LIMITED with Retry-After header.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    # Set rpm_limit=1 on the key
    await client.patch(
        f"/admin/keys/{api_key_info['key_id']}",
        json={"rpm_limit": 1},
        headers={"Authorization": f"Bearer {api_key_info['jwt']}"},
    )

    # Seed the RPM zset to simulate the window being full
    import time

    now_ms = int(time.time() * 1000)
    rpm_key = f"ratelimit:rpm:{api_key_info['key_id']}"

    redis = getattr(getattr(app.state, "rate_limiter", None), "_redis", None)
    if redis is None:
        redis = getattr(getattr(app.state, "budget_guard", None), "_redis", None)
    if redis is not None:
        await redis.zadd(rpm_key, {str(now_ms - 1000).encode(): now_ms - 1000})

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 429, "ERR_RATE_LIMITED")
    assert "retry-after" in resp.headers or "Retry-After" in resp.headers, (
        "Expected Retry-After header on 429 response"
    )


# ---------------------------------------------------------------------------
# AU8 — STT provider absent → 503 ERR_PROVIDER_UNAVAILABLE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au8_provider_absent_503(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """No 'openai' in registry → 503 ERR_PROVIDER_UNAVAILABLE.

    RED reason: route absent → 404.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    await seed_stt_model(db_session)

    # Registry with no 'openai' entry
    existing = getattr(app.state, "provider_registry", None)
    openrouter_entry = None
    if existing is not None:
        openrouter_entry = existing.get("openrouter")

    providers: dict[str, Any] = {}
    if openrouter_entry is not None:
        providers["openrouter"] = openrouter_entry

    app.state.provider_registry = ProviderRegistry(providers)

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 503, "ERR_PROVIDER_UNAVAILABLE")


# ---------------------------------------------------------------------------
# AU9 — STT missing file field → 422 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au9_missing_file_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Multipart form with model but no file field → 422 ERR_PAYLOAD_INVALID.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    # Send multipart with only model field — no file
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        data={"model": STT_MODEL_ID},
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# AU9b — STT missing model field → 422 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au9b_missing_model_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Multipart form with file but no model field → 422 ERR_PAYLOAD_INVALID.

    RED reason: route absent → 404.
    """
    await seed_stt_model(db_session)
    inject_fake_openai_audio_provider(app)

    # Send file but no model
    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files=_multipart_files(),
        # No model field in data
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# AT1 — TTS happy path: valid key + active audio_tts model → 200 StreamingResponse
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at1_tts_happy_path_200_streaming(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """POST /v1/audio/speech → 200 with streaming audio bytes and audio/mpeg content-type.

    RED reason: POST /v1/audio/speech route does not exist → 404 Not Found.
    """
    await seed_tts_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_stream_chunks([TTS_AUDIO_BYTES])

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "input": "Hello world", "voice": "alloy"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"

    content_type = resp.headers.get("content-type", "")
    assert "audio/mpeg" in content_type, f"expected audio/mpeg content-type, got: {content_type}"

    assert TTS_AUDIO_BYTES in resp.content, (
        f"expected audio bytes in response body, got: {resp.content[:50]!r}"
    )

    assert len(fake_provider.stream_bytes_calls) == 1
    call = fake_provider.stream_bytes_calls[0]
    assert call["path"] == "/audio/speech"


# ---------------------------------------------------------------------------
# AT2 — TTS billing: per_character, quantity=Decimal(len(input)), billed before stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at2_tts_single_record_per_character_quantity_len_input(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Record fires once with pricing_unit=per_character and quantity=len(input).

    input="Hello world" → len=11 → quantity=Decimal(11).

    RED reason: route absent → 404.
    """
    await seed_tts_model(db_session)
    fake_provider = inject_fake_openai_audio_provider(app)
    fake_provider.set_stream_chunks([TTS_AUDIO_BYTES])

    spy = SpyRecorder()
    app.state.usage_recorder = spy

    input_text = "Hello world"  # len=11

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "input": input_text, "voice": "alloy"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200 got {resp.status_code}: {resp.text}"
    assert spy.call_count == 1, f"expected 1 record call, got {spy.call_count}"

    rec = spy.last_call
    assert rec.get("pricing_unit") == "per_character", f"unexpected pricing_unit: {rec}"
    assert rec.get("quantity") == Decimal(len(input_text)), (
        f"expected Decimal({len(input_text)}), got {rec.get('quantity')}"
    )
    assert rec.get("model") == TTS_MODEL_ID
    assert rec.get("status") == 200


# ---------------------------------------------------------------------------
# AT3 — TTS governance raises PRE-stream: bad key → 401 body is problem+json, not audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at3_governance_raises_pre_stream_bad_key_401(
    app: Any,
    client: Any,
    db_session: AsyncSession,
) -> None:
    """Governance error (bad key) raises before StreamingResponse; body is problem+json.

    Verifies that no audio bytes are streamed on governance failure — the response
    is plain JSON (RFC 9457 problem+json), not an audio stream.

    RED reason: route absent → 404.
    """
    await seed_tts_model(db_session)
    inject_fake_openai_audio_provider(app)

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "input": "hi", "voice": "alloy"},
        # No Authorization header → governance should raise AUTH_KEY_INVALID pre-stream
    )

    assert resp.status_code == 401, f"expected 401, got {resp.status_code}: {resp.text}"

    content_type = resp.headers.get("content-type", "")
    assert "audio" not in content_type, (
        f"response must not be audio on governance failure, got content-type: {content_type}"
    )

    body = resp.json()
    assert body.get("code") == "ERR_AUTH_INVALID_KEY", (
        f"expected ERR_AUTH_INVALID_KEY, got {body.get('code')!r}"
    )


# ---------------------------------------------------------------------------
# AT4 — TTS missing voice field → 422 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at4_missing_voice_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """JSON body missing 'voice' field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_VOICE_REQUIRED).

    RED reason: route absent → 404.
    """
    await seed_tts_model(db_session)
    inject_fake_openai_audio_provider(app)

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "input": "hi"},  # no voice
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# AT5 — TTS missing input field → 422 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at5_missing_input_422(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """JSON body missing 'input' field → 422 ERR_PAYLOAD_INVALID (PAYLOAD_INPUT_REQUIRED).

    RED reason: route absent → 404.
    """
    await seed_tts_model(db_session)
    inject_fake_openai_audio_provider(app)

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "voice": "alloy"},  # no input
        headers=_auth_key(api_key_info["key"]),
    )

    _assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# AT6 — TTS provider absent → 503 ERR_PROVIDER_UNAVAILABLE (pre-stream)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_at6_provider_absent_503_pre_stream(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """No 'openai' in registry → 503 ERR_PROVIDER_UNAVAILABLE; not a StreamingResponse.

    The 503 must be returned before any audio bytes are committed.

    RED reason: route absent → 404.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    await seed_tts_model(db_session)

    # Registry with no 'openai' entry
    existing = getattr(app.state, "provider_registry", None)
    openrouter_entry = None
    if existing is not None:
        openrouter_entry = existing.get("openrouter")

    providers: dict[str, Any] = {}
    if openrouter_entry is not None:
        providers["openrouter"] = openrouter_entry

    app.state.provider_registry = ProviderRegistry(providers)

    resp = await client.post(
        SPEECH_PATH,
        json={"model": TTS_MODEL_ID, "input": "hi", "voice": "alloy"},
        headers=_auth_key(api_key_info["key"]),
    )

    assert resp.status_code == 503, f"expected 503, got {resp.status_code}: {resp.text}"

    content_type = resp.headers.get("content-type", "")
    assert "audio" not in content_type, (
        f"response must not be audio on provider-absent, got content-type: {content_type}"
    )

    body = resp.json()
    assert body.get("code") == "ERR_PROVIDER_UNAVAILABLE", (
        f"expected ERR_PROVIDER_UNAVAILABLE, got {body.get('code')!r}"
    )


# ---------------------------------------------------------------------------
# AU/AT regression — chat path still 200s after audio-endpoints build
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_au_at_regression_chat_still_200(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Chat path (POST /v1/chat/completions) is unaffected by the audio-endpoints build.

    GREEN-BY-DESIGN: this test PASSES even in the red phase because the chat path
    is already working and audio-endpoints is purely additive. Serves as a regression
    guard after BUILD to prove proxy/api/router.py + use_cases.py were not touched.
    """
    await seed_chat_model(db_session)

    # Inject a FakeCompletionUpstream so the chat route responds without OpenRouter
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
        f"chat regression: expected 200, got {resp.status_code}: {resp.text}"
    )
    assert fake_chat.call_count == 1, (
        f"chat regression: expected 1 complete() call, got {fake_chat.call_count}"
    )
    assert resp.json() == CHAT_RESPONSE_BODY
