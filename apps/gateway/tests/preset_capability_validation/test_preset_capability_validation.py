"""Failing-first (RED) suite for preset-capability-validation (TASK.md §4, contract FROZEN @ v1).

One test per scenario in TASK.md §2 SCENARIOS / §4 TEST plan (12 tests):

  1. test_chat_preset_resolves_image_incapable_model_rejected      — regression, no new prod code
  2. test_stt_preset_resolves_audio_incapable_model_rejected       — regression, no new prod code
  3. test_images_preset_resolves_wrong_modality_model_rejected     — NEW ERR_MODEL_MODALITY_MISMATCH
  4. test_images_direct_model_wrong_modality_rejected              — proves the fix is general
  5. test_embeddings_wrong_modality_model_rejected
  6. test_tts_preset_resolves_wrong_modality_model_rejected
  7. test_images_matching_modality_passes_through_unchanged        — byte-identical baseline
  8. test_embeddings_matching_modality_passes_through_unchanged
  9. test_tts_matching_modality_passes_through_unchanged
  10. test_realtime_ws_chat_turn_rejects_incompatible_model        — proves _real_chat wiring fix
  11. test_realtime_ws_stt_turn_rejects_incompatible_model         — proves _real_stt wiring fix
  12. test_model_checker_activation_check_still_modality_agnostic  — no regression to ModelChecker

TRUE-RED RULE (at write time): imports `MODEL_MODALITY_MISMATCH` from `gateway.core.error_catalog`
(does not exist yet) -> ImportError at collection. Tests 10/11 fail (RED) for a SECOND, independent
reason even after the error spec exists: `_real_chat`/`_real_stt` do not yet wire
`input_modality_lookup=`/`input_modality_guard_enabled=` into their use-case constructors, so the
guard never fires and the turn completes normally instead of surfacing a `chat_failed`/`stt_failed`
error frame.

Infrastructure:
  - Tests 1-9, 12: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9)
    via the shared root conftest (`app`/`client`/`db_session`) + this suite's local `settings`
    fixture (input_modality_guard_enabled=True).
  - Tests 10-11 (realtime-WS): self-contained `starlette.testclient.TestClient`, mirroring
    `tests/realtime/test_realtime_ws.py`'s own bootstrap pattern (httpx/ASGITransport has no
    WebSocket support) — schema bootstrap + signup/login/key-creation run inside the SAME
    TestClient-managed anyio loop via `tc.portal.call(...)`.
"""

from __future__ import annotations

import io
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.testclient import TestClient

from gateway.core.config import Settings
from gateway.core.db import Base

# Modules under test — MODEL_MODALITY_MISMATCH does not exist yet; its absence IS the
# RED target (ImportError at collection).
from gateway.core.error_catalog import MODEL_MODALITY_MISMATCH  # type: ignore[attr-defined]
from gateway.main import create_app
from tests.preset_capability_validation.conftest import (
    FakeCompletionUpstream,
    FakeUpstreamProvider,
    SpyRecorder,
    inject_fake_completion_upstream,
    inject_fake_provider,
    seed_chat_model,
    seed_embedding_model,
    seed_image_model,
    seed_preset,
    seed_stt_model,
    seed_tts_model,
)

# ---------------------------------------------------------------------------
# Route constants
# ---------------------------------------------------------------------------

COMPLETIONS_PATH = "/v1/chat/completions"
TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"
IMAGES_PATH = "/v1/images/generations"
EMBEDDINGS_PATH = "/v1/embeddings"
SPEECH_PATH = "/v1/audio/speech"

FAKE_AUDIO_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt "

REALTIME_TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
REALTIME_TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _assert_problem(resp: Any, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; body={body}"
    )
    assert body.get("status") == status
    return body


# ===========================================================================
# 1 — chat preset -> image-incapable model + image_url part -> UNSUPPORTED_INPUT_MODALITY
#     (REGRESSION — no new production code; preset resolves body["model"] BEFORE the
#     guard runs, by call order already frozen in v56/v55)
# ===========================================================================


async def test_chat_preset_resolves_image_incapable_model_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Chat preset resolves to a text-only model; an image_url part is rejected with v55's
    existing ERR_UNSUPPORTED_INPUT_MODALITY. No upstream call, no usage row.
    """
    target_model = await seed_chat_model(
        db_session, model_id="chat-text-only-preset-target", input_modalities="text"
    )
    await seed_preset(
        db_session,
        tenant_id=api_key_info["tenant_id"],
        preset_name="cheap",
        alias_key="vision",
        target_model=target_model,
    )
    fake_upstream = inject_fake_completion_upstream(app)
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    body = {
        "model": "cheap:vision",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
                ],
            }
        ],
    }
    resp = await client.post(COMPLETIONS_PATH, json=body, headers=_auth(api_key_info["key"]))

    _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    assert fake_upstream.calls == 0, (
        "upstream MUST NOT be called on rejected preset-resolved request"
    )
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


# ===========================================================================
# 2 — STT preset -> model missing "audio" -> UNSUPPORTED_INPUT_MODALITY (REGRESSION)
# ===========================================================================


async def test_stt_preset_resolves_audio_incapable_model_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """STT preset resolves to a model whose input_modalities lacks "audio" — regression only."""
    target_model = await seed_stt_model(
        db_session, model_id="stt-text-only-preset-target", input_modalities="text"
    )
    await seed_preset(
        db_session,
        tenant_id=api_key_info["tenant_id"],
        preset_name="cheap",
        alias_key="stt",
        target_model=target_model,
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        TRANSCRIPTIONS_PATH,
        files={"file": ("audio.mp3", io.BytesIO(FAKE_AUDIO_BYTES), "audio/mpeg")},
        data={"model": "cheap:stt"},
        headers=_auth(api_key_info["key"]),
    )

    _assert_problem(resp, 400, "ERR_UNSUPPORTED_INPUT_MODALITY")
    assert len(fake_provider.post_multipart_calls) == 0, "upstream MUST NOT be called on STT reject"
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


# ===========================================================================
# 3 — images preset -> modality="chat" model -> NEW ERR_MODEL_MODALITY_MISMATCH
# ===========================================================================


async def test_images_preset_resolves_wrong_modality_model_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """Images preset resolves to an active model with modality="chat" -> 400
    ERR_MODEL_MODALITY_MISMATCH, before select_provider/upstream/billing.
    """
    target_model = await seed_image_model(
        db_session, model_id="chat-model-for-images-preset", modality="chat"
    )
    await seed_preset(
        db_session,
        tenant_id=api_key_info["tenant_id"],
        preset_name="cheap",
        alias_key="img",
        target_model=target_model,
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        IMAGES_PATH,
        json={"model": "cheap:img", "prompt": "a white cat"},
        headers=_auth(api_key_info["key"]),
    )

    body = _assert_problem(resp, 400, MODEL_MODALITY_MISMATCH.code)
    assert "chat-model-for-images-preset" in body.get("detail", "")
    assert len(fake_provider.post_json_calls) == 0, (
        "upstream MUST NOT be called on rejected request"
    )
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


# ===========================================================================
# 4 — DIRECT (no preset) images model with modality="chat" -> proves the fix is general
# ===========================================================================


async def test_images_direct_model_wrong_modality_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """A directly-named (non-preset) chat model used for images is ALSO rejected — proves
    the coarse guard is a general fix, not preset-specific.
    """
    model_id = await seed_image_model(
        db_session, model_id="chat-model-direct-images", modality="chat"
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        IMAGES_PATH,
        json={"model": model_id, "prompt": "a white cat"},
        headers=_auth(api_key_info["key"]),
    )

    _assert_problem(resp, 400, MODEL_MODALITY_MISMATCH.code)
    assert len(fake_provider.post_json_calls) == 0
    assert spy.call_count == 0


# ===========================================================================
# 5 — DIRECT embeddings model with modality="chat" -> NEW ERR_MODEL_MODALITY_MISMATCH
# ===========================================================================


async def test_embeddings_wrong_modality_model_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """A directly-named chat model used for /v1/embeddings is rejected with the NEW error."""
    model_id = await seed_embedding_model(
        db_session, model_id="chat-model-direct-embed", modality="chat"
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": model_id, "input": "hello world"},
        headers=_auth(api_key_info["key"]),
    )

    _assert_problem(resp, 400, MODEL_MODALITY_MISMATCH.code)
    assert len(fake_provider.post_json_calls) == 0
    assert spy.call_count == 0


# ===========================================================================
# 6 — TTS preset -> modality="embedding" model -> NEW ERR_MODEL_MODALITY_MISMATCH
# ===========================================================================


async def test_tts_preset_resolves_wrong_modality_model_rejected(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """TTS preset resolves to an active model with modality="embedding" -> rejected."""
    target_model = await seed_tts_model(
        db_session, model_id="embed-model-for-tts-preset", modality="embedding"
    )
    await seed_preset(
        db_session,
        tenant_id=api_key_info["tenant_id"],
        preset_name="cheap",
        alias_key="tts",
        target_model=target_model,
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        SPEECH_PATH,
        json={"model": "cheap:tts", "input": "hello world", "voice": "alloy"},
        headers=_auth(api_key_info["key"]),
    )

    _assert_problem(resp, 400, MODEL_MODALITY_MISMATCH.code)
    assert len(fake_provider.stream_bytes_calls) == 0, "upstream MUST NOT be reached on reject"
    assert spy.call_count == 0, "usage row MUST NOT be recorded on rejected request"


# ===========================================================================
# 7 — images: matching modality -> byte-identical pass-through
# ===========================================================================


async def test_images_matching_modality_passes_through_unchanged(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """An active model with modality="image" proceeds to select_provider/upstream/billing
    exactly as before this task — zero new I/O, zero behavior change.
    """
    model_id = await seed_image_model(db_session, model_id="dall-e-3-pcv-ok", modality="image")
    fake_provider = inject_fake_provider(app, provider_name="openai")
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        IMAGES_PATH,
        json={"model": model_id, "prompt": "a white cat"},
        headers=_auth(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert len(fake_provider.post_json_calls) == 1
    assert spy.call_count == 1
    assert spy.last_call.get("pricing_unit") == "per_image"
    assert spy.last_call.get("quantity") == Decimal(1)


# ===========================================================================
# 8 — embeddings: matching modality -> byte-identical pass-through
# ===========================================================================


async def test_embeddings_matching_modality_passes_through_unchanged(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """An active model with modality="embedding" proceeds exactly as before this task."""
    model_id = await seed_embedding_model(
        db_session, model_id="text-embedding-pcv-ok", modality="embedding"
    )
    fake_provider = inject_fake_provider(app, provider_name="openai")
    fake_provider.set_post_json_response(
        200,
        {
            "object": "list",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
            "model": model_id,
            "usage": {"prompt_tokens": 5, "total_tokens": 5},
        },
    )
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        EMBEDDINGS_PATH,
        json={"model": model_id, "input": "hello world"},
        headers=_auth(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert len(fake_provider.post_json_calls) == 1
    assert spy.call_count == 1


# ===========================================================================
# 9 — TTS: matching modality -> byte-identical pass-through
# ===========================================================================


async def test_tts_matching_modality_passes_through_unchanged(
    app: Any,
    client: Any,
    db_session: AsyncSession,
    api_key_info: dict[str, str],
) -> None:
    """An active model with modality="audio_tts" proceeds exactly as before this task
    (streamed 200 response, single usage record).
    """
    model_id = await seed_tts_model(db_session, model_id="tts-1-pcv-ok", modality="audio_tts")
    fake_provider = inject_fake_provider(app, provider_name="openai")
    fake_provider.set_stream_chunks([b"audio-chunk-ok"])
    spy = SpyRecorder()
    app.state.usage_recorder = spy

    resp = await client.post(
        SPEECH_PATH,
        json={"model": model_id, "input": "hello world", "voice": "alloy"},
        headers=_auth(api_key_info["key"]),
    )

    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
    assert b"audio-chunk-ok" in resp.content
    assert len(fake_provider.stream_bytes_calls) == 1
    assert spy.call_count == 1
    assert spy.last_call.get("pricing_unit") == "per_character"


# ===========================================================================
# Realtime-WS helpers — self-contained TestClient bootstrap
# (httpx/ASGITransport used by the async `client` fixture has no WebSocket support;
#  mirrors tests/realtime/test_realtime_ws.py's own bootstrap pattern exactly.)
# ===========================================================================


def _make_realtime_settings() -> Settings:
    return Settings(
        database_url=REALTIME_TEST_DATABASE_URL,
        jwt_secret=REALTIME_TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        input_modality_guard_enabled=True,
    )


def _bootstrap_and_signup(tc: TestClient, app: Any) -> tuple[str, uuid.UUID]:
    """Bootstrap schema + signup/login/create key inside the TestClient's live anyio loop.

    Returns (sk_key, tenant_id).
    """

    async def _bootstrap() -> None:
        engine = app.state.engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

    signup = tc.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RealtimeCapValTenant",
            "email": "realtime-cap-val@example.io",
            "password": "realtime capability test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id = uuid.UUID(signup.json()["tenant_id"])

    login = tc.post(
        "/admin/auth/login",
        json={"email": "realtime-cap-val@example.io", "password": "realtime capability test"},
    )
    token = login.json()["access_token"]

    created = tc.post(
        "/admin/keys",
        json={"name": "realtime-cap-val-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    sk_key: str = created.json()["key"]

    return sk_key, tenant_id


def _seed_via_portal(tc: TestClient, app: Any, seed_coro: Any) -> None:
    """Run an async `seed_coro(session)` inside the TestClient's live anyio loop."""

    async def _run() -> None:
        async with app.state.sessionmaker() as session:
            await seed_coro(session)

    tc.portal.call(_run)  # type: ignore[attr-defined]


# ===========================================================================
# 10 — realtime-WS chat turn honors the guard (proves _real_chat wiring fix)
# ===========================================================================


def test_realtime_ws_chat_turn_rejects_incompatible_model() -> None:
    """_real_chat must wire input_modality_lookup=/input_modality_guard_enabled= into the
    CompletionUseCase it constructs, mirroring deps.py:228-229.

    _real_chat's outgoing chat body always carries a PLAIN STRING transcript (never an
    image_url content part — see realtime_ws.py:280-284), so the required input modality
    is always exactly {"text"}. To exercise the SAME enforcement mechanism the HTTP chat
    path uses (unchanged from v55), this test seeds a chat model whose input_modalities is
    "image" (excludes "text") — the transcript's required "text" is then missing from the
    model's allowed set, firing the identical ERR_UNSUPPORTED_INPUT_MODALITY guard. This
    proves the WIRING (the only new code here), not a new enforcement rule.

    STT is left STUBBED (always returns a fixed transcript) so only the chat leg is under
    test; before the fix, input_modality_guard_enabled defaults to False on this
    construction site, so the turn would complete normally (no chat_failed frame) — RED.
    """
    settings = _make_realtime_settings()
    app = create_app(settings)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.__enter__()
    fake_upstream: FakeCompletionUpstream
    try:
        sk_key, _tenant_id = _bootstrap_and_signup(tc, app)

        chat_model_id = "chat-image-only-realtime-pcv"

        async def _seed(session: AsyncSession) -> None:
            await seed_chat_model(session, model_id=chat_model_id, input_modalities="image")

        _seed_via_portal(tc, app, _seed)

        async def _stub_stt(audio_bytes: bytes, model: str, raw_key: str) -> str:
            return "hello there"

        app.state.realtime_stt = _stub_stt
        fake_upstream = inject_fake_completion_upstream(app)

        commit_frame = {
            "type": "commit",
            "model_stt": "unused-stt-model",
            "model_chat": chat_model_id,
            "model_tts": "unused-tts-model",
            "voice": "alloy",
        }

        with tc.websocket_connect("/v1/realtime") as ws:
            ws.send_json({"type": "auth", "token": sk_key})
            assert ws.receive_json() == {"type": "ready"}

            ws.send_bytes(b"audio-data")
            ws.send_json(commit_frame)

            transcript_msg = ws.receive_json()
            assert transcript_msg["type"] == "transcript"

            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "chat_failed"
            assert "ERR_UNSUPPORTED_INPUT_MODALITY" in err["message"]

            # Socket stays open — a second turn hits the same rejection again (no disconnect).
            ws.send_bytes(b"audio-data-2")
            ws.send_json(commit_frame)

            transcript_msg_2 = ws.receive_json()
            assert transcript_msg_2["type"] == "transcript"

            err2 = ws.receive_json()
            assert err2["type"] == "error"
            assert err2["code"] == "chat_failed"
    finally:
        tc.__exit__(None, None, None)

    assert fake_upstream.calls == 0, "chat upstream MUST NOT be called on rejected turn"


# ===========================================================================
# 11 — realtime-WS STT turn honors the guard (proves _real_stt wiring fix)
# ===========================================================================


def test_realtime_ws_stt_turn_rejects_incompatible_model() -> None:
    """_real_stt must wire input_modality_guard_enabled= into the TranscriptionUseCase it
    constructs, mirroring audio_deps.py:106 (TranscriptionUseCase has no lookup param — it
    runs its own inline catalog query when the flag is on).

    Before the fix, input_modality_guard_enabled defaults to False on this construction
    site, so an STT model missing "audio" would be silently accepted — RED.
    """
    settings = _make_realtime_settings()
    app = create_app(settings)
    tc = TestClient(app, raise_server_exceptions=True)
    tc.__enter__()
    fake_provider: FakeUpstreamProvider
    try:
        sk_key, _tenant_id = _bootstrap_and_signup(tc, app)

        stt_model_id = "stt-text-only-realtime-pcv"

        async def _seed(session: AsyncSession) -> None:
            await seed_stt_model(session, model_id=stt_model_id, input_modalities="text")

        _seed_via_portal(tc, app, _seed)

        fake_provider = inject_fake_provider(app, provider_name="openai")

        commit_frame = {
            "type": "commit",
            "model_stt": stt_model_id,
            "model_chat": "unused-chat-model",
            "model_tts": "unused-tts-model",
            "voice": "alloy",
        }

        # _real_stt is left UNSTUBBED (no app.state.realtime_stt) so the real
        # TranscriptionUseCase — and the newly-wired guard — actually runs.
        with tc.websocket_connect("/v1/realtime") as ws:
            ws.send_json({"type": "auth", "token": sk_key})
            assert ws.receive_json() == {"type": "ready"}

            ws.send_bytes(FAKE_AUDIO_BYTES)
            ws.send_json(commit_frame)

            err = ws.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "stt_failed"
            assert "ERR_UNSUPPORTED_INPUT_MODALITY" in err["message"]

            # Socket stays open — a second turn hits the same rejection again (no disconnect).
            ws.send_bytes(FAKE_AUDIO_BYTES)
            ws.send_json(commit_frame)

            err2 = ws.receive_json()
            assert err2["type"] == "error"
            assert err2["code"] == "stt_failed"
    finally:
        tc.__exit__(None, None, None)

    assert len(fake_provider.post_multipart_calls) == 0, "STT upstream MUST NOT be called"


# ===========================================================================
# 12 — ModelChecker activation check remains modality-agnostic (no regression)
# ===========================================================================


async def test_model_checker_activation_check_still_modality_agnostic(
    db_session: AsyncSession,
) -> None:
    """This task's new gate is a SEPARATE, additive check — it does not touch ModelChecker.

    Mirrors tests/provider_seam/test_provider_seam.py::test_ps7_non_chat_active_model_passes_catalog_check
    (constructed fresh here, per TASK.md §0 GROUND, rather than editing the frozen file) —
    proves ModelChecker.is_active/check_for_tenant remain modality-agnostic for ACTIVATION
    purposes even though a SEPARATE operation-type gate now exists downstream.
    """
    from gateway.catalog.infrastructure.orm import ModelRow
    from gateway.proxy.domain.ports import ModelAccess
    from gateway.proxy.infrastructure.model_checker import SqlAlchemyModelChecker

    tenant_id = uuid.uuid4()

    row = ModelRow(
        id="embedding-model-pcv-regression",
        name="embedding-model-pcv-regression",
        context_length=None,
        active=True,
        modality="embedding",  # type: ignore[call-arg]
        provider="openai",  # type: ignore[call-arg]
    )
    db_session.add(row)
    await db_session.commit()

    checker = SqlAlchemyModelChecker(db_session)
    active = await checker.is_active("embedding-model-pcv-regression")
    access = await checker.check_for_tenant("embedding-model-pcv-regression", tenant_id)

    assert active is True, f"is_active must return True for active non-chat model; got {active}"
    assert access == ModelAccess.ACTIVE, (
        f"check_for_tenant must return ACTIVE for active non-chat model; got {access}"
    )
