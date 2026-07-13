"""Tests for the /v1/realtime WebSocket endpoint.

Protocol summary:
  1. First message MUST be {"type":"auth","token":"sk-..."}
     → {"type":"ready"} on success or close(4401/4408)
  2. After auth, turns: binary audio frames + {"type":"commit",...}
     → {"type":"transcript"} → {"type":"reply"} → <binary audio> → {"type":"turn_done"}
  3. Non-fatal errors: {"type":"error","code":...,"message":...}; socket stays open.
  4. WebSocketDisconnect → clean return (no leak, no 500).

All tests use app.state stubs (realtime_stt / realtime_chat / realtime_tts) so no live
provider is needed. The Starlette TestClient runs the ASGI app in a thread-embedded anyio
event loop; the schema bootstrap and key seeding are done inside the SAME TestClient
context so asyncpg uses the same loop throughout.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests import _redis_env

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STT_TRANSCRIPT = "hello world"
CHAT_REPLY = "hi there"
TTS_AUDIO_CHUNK = b"\x00\x01\x02\x03"

MODEL_STT = "whisper-1"
MODEL_CHAT = "openai/gpt-4o-mini"
MODEL_TTS = "tts-1"
VOICE = "alloy"

TEST_DATABASE_URL = _redis_env.TEST_DATABASE_URL
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"

# ---------------------------------------------------------------------------
# Stub callables for app.state injection
# ---------------------------------------------------------------------------


async def _stub_stt(audio_bytes: bytes, model: str, raw_key: str) -> str:
    return STT_TRANSCRIPT


async def _stub_chat(transcript: str, model: str, raw_key: str) -> str:
    return CHAT_REPLY


async def _stub_tts(text_: str, model: str, voice: str, raw_key: str) -> AsyncIterator[bytes]:
    async def _gen() -> AsyncIterator[bytes]:
        yield TTS_AUDIO_CHUNK

    return _gen()


class _RaiseSTT:
    """STT stub that raises to test stt_failed error frame."""

    async def __call__(self, audio_bytes: bytes, model: str, raw_key: str) -> str:
        raise RuntimeError("upstream STT failure")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_redis_env.TEST_REDIS_URL,
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
    )


@pytest.fixture
def app_with_stubs(settings: Settings) -> Any:
    """Create app and install stubs on app.state. Schema bootstrapped inside TestClient context."""
    application = create_app(settings)
    application.state.realtime_stt = _stub_stt
    application.state.realtime_chat = _stub_chat
    application.state.realtime_tts = _stub_tts
    return application


@pytest.fixture
def client_and_key(app_with_stubs: Any) -> tuple[TestClient, str]:
    """Return a (TestClient, sk_key) pair bootstrapped in a single TestClient context.

    The schema is dropped/created and the API key is seeded inside the SAME
    TestClient context manager so all asyncpg operations share the same anyio event loop.
    The TestClient is yielded still open (inside its __enter__) — tests use it directly.
    """
    app = app_with_stubs
    # Use TestClient as a context manager so the anyio loop is live for all DB operations.
    # We do NOT use `with` here because we want to return the open client to the test.
    # Instead we manage entry/exit manually via __enter__/__exit__.
    tc = TestClient(app, raise_server_exceptions=True)
    tc.__enter__()  # starts the anyio loop + app lifespan

    # Bootstrap schema (inside the live anyio loop)
    import asyncio

    async def _bootstrap() -> None:
        engine = app.state.engine
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    # Run bootstrap via the TestClient's portal (anyio from_thread)
    # TestClient exposes its anyio portal via .portal attribute (set by __enter__)
    tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

    # Seed: signup → login → create key
    signup = tc.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "RealtimeTest",
            "email": "realtime-test@example.io",
            "password": "realtime battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"

    login = tc.post(
        "/admin/auth/login",
        json={"email": "realtime-test@example.io", "password": "realtime battery test"},
    )
    token = login.json()["access_token"]

    created = tc.post(
        "/admin/keys",
        json={"name": "realtime-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    sk_key: str = created.json()["key"]

    yield tc, sk_key

    tc.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Helper: build a commit frame
# ---------------------------------------------------------------------------


def _commit_frame(
    model_stt: str = MODEL_STT,
    model_chat: str = MODEL_CHAT,
    model_tts: str = MODEL_TTS,
    voice: str = VOICE,
) -> dict[str, Any]:
    return {
        "type": "commit",
        "model_stt": model_stt,
        "model_chat": model_chat,
        "model_tts": model_tts,
        "voice": voice,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_auth_then_one_turn(client_and_key: tuple[TestClient, str]) -> None:
    """Happy path: auth + one full turn (STT → chat → TTS → turn_done)."""
    sync_client, sk_key = client_and_key
    with sync_client.websocket_connect("/v1/realtime") as ws:
        # Auth handshake
        ws.send_json({"type": "auth", "token": sk_key})
        ready = ws.receive_json()
        assert ready == {"type": "ready"}

        # Send audio bytes
        ws.send_bytes(b"\x00\x01\x02\x03audio")

        # Commit
        ws.send_json(_commit_frame())

        # Transcript
        transcript_msg = ws.receive_json()
        assert transcript_msg["type"] == "transcript"
        assert transcript_msg["text"] == STT_TRANSCRIPT

        # Reply
        reply_msg = ws.receive_json()
        assert reply_msg["type"] == "reply"
        assert reply_msg["text"] == CHAT_REPLY

        # TTS binary audio
        audio = ws.receive_bytes()
        assert audio == TTS_AUDIO_CHUNK

        # Turn done
        done = ws.receive_json()
        assert done == {"type": "turn_done"}


def test_two_turns(client_and_key: tuple[TestClient, str]) -> None:
    """Two consecutive turns on the same socket — buffer resets between turns."""
    sync_client, sk_key = client_and_key
    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}

        for turn in range(2):
            ws.send_bytes(b"audio-turn-data")
            ws.send_json(_commit_frame())

            transcript_msg = ws.receive_json()
            assert transcript_msg["type"] == "transcript"

            reply_msg = ws.receive_json()
            assert reply_msg["type"] == "reply"

            ws.receive_bytes()  # TTS audio

            done = ws.receive_json()
            assert done == {"type": "turn_done"}, f"turn {turn} did not complete"


def test_bad_auth_closes_4401(app_with_stubs: Any) -> None:
    """An invalid/missing token in the auth frame → server closes with 4401."""
    app = app_with_stubs
    with TestClient(app, raise_server_exceptions=True) as tc:
        # Bootstrap schema
        import asyncio

        async def _bootstrap() -> None:
            engine = app.state.engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)

        tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/v1/realtime") as ws:
                ws.send_json({"type": "auth", "token": "sk-invalid-bad-token"})
                ws.receive_json()  # should never arrive — server closes
    assert exc_info.value.code == 4401


def test_first_frame_not_auth_closes_4401(app_with_stubs: Any) -> None:
    """First frame is binary (not auth JSON) → server closes with 4401."""
    app = app_with_stubs
    with TestClient(app, raise_server_exceptions=True) as tc:
        import asyncio

        async def _bootstrap() -> None:
            engine = app.state.engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)

        tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/v1/realtime") as ws:
                ws.send_bytes(b"audio-before-auth")
                ws.receive_json()  # should never arrive
    assert exc_info.value.code == 4401


def test_first_frame_commit_not_auth_closes_4401(app_with_stubs: Any) -> None:
    """First frame is a commit frame (wrong type, not 'auth') → 4401."""
    app = app_with_stubs
    with TestClient(app, raise_server_exceptions=True) as tc:
        import asyncio

        async def _bootstrap() -> None:
            engine = app.state.engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)

        tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/v1/realtime") as ws:
                ws.send_json({"type": "commit", "model_stt": MODEL_STT})
                ws.receive_json()  # should never arrive
    assert exc_info.value.code == 4401


def test_auth_timeout_closes_4408(app_with_stubs: Any) -> None:
    """No auth frame within timeout → server closes with 4408."""
    app = app_with_stubs
    # Override timeout to near-zero so the test is fast
    app.state.settings.realtime_auth_timeout_seconds = 0.05

    with TestClient(app, raise_server_exceptions=True) as tc:
        import asyncio

        async def _bootstrap() -> None:
            engine = app.state.engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.run_sync(Base.metadata.create_all)

        tc.portal.call(_bootstrap)  # type: ignore[attr-defined]

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/v1/realtime") as ws:
                time.sleep(0.3)  # outlast the timeout
                ws.receive_json()  # server should close before this
    assert exc_info.value.code == 4408


def test_over_cap_utterance(client_and_key: tuple[TestClient, str]) -> None:
    """Buffer exceeds realtime_max_utterance_bytes → error utterance_too_large, no STT call."""
    sync_client, sk_key = client_and_key
    app = sync_client.app  # type: ignore[attr-defined]

    # Set a tiny cap (10 bytes)
    app.state.settings.realtime_max_utterance_bytes = 10

    stt_called: list[bool] = []

    async def _tracking_stt(audio_bytes: bytes, model: str, raw_key: str) -> str:
        stt_called.append(True)
        return STT_TRANSCRIPT

    app.state.realtime_stt = _tracking_stt

    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}

        # Send audio that exceeds cap
        ws.send_bytes(b"x" * 20)  # 20 bytes > 10 cap
        ws.send_json(_commit_frame())

        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "utterance_too_large"

        # Socket stays open; STT was NOT called
        assert len(stt_called) == 0

        # Restore good stub and verify socket still usable
        app.state.realtime_stt = _stub_stt
        app.state.settings.realtime_max_utterance_bytes = 0  # unlimited

        ws.send_bytes(b"ok-audio")
        ws.send_json(_commit_frame())

        transcript_msg = ws.receive_json()
        assert transcript_msg["type"] == "transcript"
        ws.receive_json()  # reply
        ws.receive_bytes()  # TTS audio
        ws.receive_json()  # turn_done


def test_over_cap_streamed_across_many_frames(
    client_and_key: tuple[TestClient, str],
) -> None:
    """Design-for-failure: audio streamed across MANY frames is bounded in memory.

    A client that streams far more than the cap (across many frames, never
    committing) must not exhaust memory; the accumulation is bounded, and the
    eventual commit still returns utterance_too_large with no STT call.
    """
    sync_client, sk_key = client_and_key
    app = sync_client.app  # type: ignore[attr-defined]

    app.state.settings.realtime_max_utterance_bytes = 10

    stt_called: list[bool] = []

    async def _tracking_stt(audio_bytes: bytes, model: str, raw_key: str) -> str:
        stt_called.append(True)
        return STT_TRANSCRIPT

    app.state.realtime_stt = _tracking_stt

    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}

        # Stream 200 frames × 50 bytes = 10_000 bytes, far over the 10-byte cap,
        # across many separate binary frames (exercises the accumulation bound).
        for _ in range(200):
            ws.send_bytes(b"x" * 50)
        ws.send_json(_commit_frame())

        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "utterance_too_large"
        assert len(stt_called) == 0

        # Socket remains usable after the bounded over-cap utterance.
        app.state.realtime_stt = _stub_stt
        app.state.settings.realtime_max_utterance_bytes = 0  # unlimited
        ws.send_bytes(b"ok-audio")
        ws.send_json(_commit_frame())
        assert ws.receive_json()["type"] == "transcript"
        ws.receive_json()  # reply
        ws.receive_bytes()  # TTS audio
        ws.receive_json()  # turn_done


def test_provider_error_frame(client_and_key: tuple[TestClient, str]) -> None:
    """STT stub raises → error stt_failed, socket remains usable for next turn."""
    sync_client, sk_key = client_and_key
    app = sync_client.app  # type: ignore[attr-defined]

    app.state.realtime_stt = _RaiseSTT()

    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}

        ws.send_bytes(b"audio-data")
        ws.send_json(_commit_frame())

        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "stt_failed"

        # Socket is still open — restore good stub for second turn
        app.state.realtime_stt = _stub_stt

        ws.send_bytes(b"audio-data-2")
        ws.send_json(_commit_frame())

        transcript_msg = ws.receive_json()
        assert transcript_msg["type"] == "transcript"
        ws.receive_json()  # reply
        ws.receive_bytes()  # TTS audio
        ws.receive_json()  # turn_done


def test_commit_no_audio(client_and_key: tuple[TestClient, str]) -> None:
    """Commit with empty buffer → error no_audio, socket stays open."""
    sync_client, sk_key = client_and_key
    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}

        # No audio bytes sent, directly commit
        ws.send_json(_commit_frame())

        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "no_audio"

        # Socket is still open — can do a proper turn
        ws.send_bytes(b"audio-data")
        ws.send_json(_commit_frame())
        assert ws.receive_json()["type"] == "transcript"
        ws.receive_json()  # reply
        ws.receive_bytes()  # TTS audio
        ws.receive_json()  # turn_done


def test_clean_disconnect(client_and_key: tuple[TestClient, str]) -> None:
    """Client closes mid-stream → server returns cleanly (no 500, no leaked tasks)."""
    sync_client, sk_key = client_and_key
    with sync_client.websocket_connect("/v1/realtime") as ws:
        ws.send_json({"type": "auth", "token": sk_key})
        assert ws.receive_json() == {"type": "ready"}
        # Close without sending any turn
    # No exception means clean server return


def test_token_only_from_auth_frame(client_and_key: tuple[TestClient, str]) -> None:
    """Token must come from auth frame — URL query params are NOT honored."""
    sync_client, sk_key = client_and_key
    # Connect with token in URL (must be ignored / treated as unauthed)
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with sync_client.websocket_connect(f"/v1/realtime?token={sk_key}") as ws:
            # Send a non-auth first frame to trigger 4401
            ws.send_json({"type": "commit", "model_stt": MODEL_STT})
            ws.receive_json()
    # The 4401 proves the URL token was NOT used for auth
    assert exc_info.value.code == 4401
