"""Suite-local fixtures for audio_endpoints tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: settings, app, client, db_session.

Key fakes defined here:
  - FakeAudioProvider  — records post_multipart + stream_bytes calls; returns preset responses
  - SpyRecorder        — counts record() invocations; satisfies UsageRecorder protocol
  - FakeCompletionUpstream — records complete() calls; used in AU/AT regression test

Seed helpers:
  - seed_stt_model   — raw SQL insert for audio_stt model + per_second pricing snapshot
  - seed_tts_model   — raw SQL insert for audio_tts model + per_character pricing snapshot
  - seed_chat_model  — raw SQL insert for chat model (AU/AT regression)
  - api_key_info     — HTTP admin signup + login + key create; returns dict with key/ids

Pattern mirrors tests/embeddings_endpoint/conftest.py.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Model / response constants
# ---------------------------------------------------------------------------

STT_MODEL_ID = "whisper-1"
TTS_MODEL_ID = "tts-1"
CHAT_MODEL_ID = "openai/gpt-4o"

STT_RESPONSE_BODY: dict[str, Any] = {
    "task": "transcribe",
    "language": "english",
    "duration": 12.5,
    "text": "Hello world",
}

STT_RESPONSE_NO_DURATION: dict[str, Any] = {
    "text": "Hello world",
}

TTS_AUDIO_BYTES = b"audio-bytes-chunk"

CHAT_RESPONSE_BODY: dict[str, Any] = {
    "id": "gen-au-chat-1",
    "object": "chat.completion",
    "model": CHAT_MODEL_ID,
    "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}

# Minimal audio file bytes (not a real audio file — provider is faked)
FAKE_AUDIO_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt "


# ---------------------------------------------------------------------------
# FakeAudioProvider
# ---------------------------------------------------------------------------


class FakeAudioProvider:
    """Minimal UpstreamProvider for audio tests.

    Implements the UpstreamProvider protocol (post_json / post_multipart / stream_bytes).
    Tests control responses via set_multipart_response() and set_stream_bytes().
    """

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_multipart_calls: list[dict[str, Any]] = []
        self.stream_bytes_calls: list[dict[str, Any]] = []
        self._multipart_response: tuple[int, dict[str, Any]] = (200, STT_RESPONSE_BODY)
        self._stream_chunks: list[bytes] = [TTS_AUDIO_BYTES]

    def set_multipart_response(self, status: int, body: dict[str, Any]) -> None:
        self._multipart_response = (status, body)

    def set_stream_chunks(self, chunks: list[bytes]) -> None:
        self._stream_chunks = chunks

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        # Not used for audio but required by UpstreamProvider protocol
        return (200, {})

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        self.post_multipart_calls.append({"path": path, "files": dict(files), "data": dict(data)})
        return self._multipart_response

    def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.stream_bytes_calls.append({"path": path, "payload": dict(payload)})
        chunks = list(self._stream_chunks)

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return _gen()


# ---------------------------------------------------------------------------
# SpyRecorder
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Minimal UsageRecorder-compatible spy.

    Records the kwargs from every record() call.
    Satisfies the typed-extras seam: supported_extras includes all known keys
    so filtering does not strip any kwarg we intend to verify.
    """

    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
        }
    )

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_call(self) -> dict[str, Any]:
        assert self.calls, "SpyRecorder: no record() calls captured"
        return self.calls[-1]

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------------
# FakeCompletionUpstream (for AU/AT regression test)
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Records complete() calls; used to verify chat path is unaffected."""

    def __init__(self) -> None:
        self.complete_calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.complete_calls)

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.complete_calls.append(dict(payload))
        return (200, CHAT_RESPONSE_BODY)

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-au1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def seed_stt_model(
    session: AsyncSession,
    *,
    model_id: str = STT_MODEL_ID,
    active: bool = True,
    provider: str = "openai",
    unit_usd_per_unit: str = "0.000006",
) -> str:
    """Insert an audio_stt model row + per_second pricing snapshot into the test DB.

    Uses ON CONFLICT DO NOTHING so the helper is idempotent within a test run.
    Returns model_id for convenience.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 0, :active, 'audio_stt', :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_second', :upu)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id, "upu": unit_usd_per_unit},
    )
    await session.commit()
    return model_id


async def seed_tts_model(
    session: AsyncSession,
    *,
    model_id: str = TTS_MODEL_ID,
    active: bool = True,
    provider: str = "openai",
    unit_usd_per_unit: str = "0.000015",
) -> str:
    """Insert an audio_tts model row + per_character pricing snapshot into the test DB.

    Uses ON CONFLICT DO NOTHING so the helper is idempotent within a test run.
    Returns model_id for convenience.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 0, :active, 'audio_tts', :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_character', :upu)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id, "upu": unit_usd_per_unit},
    )
    await session.commit()
    return model_id


async def seed_chat_model(
    session: AsyncSession,
    *,
    model_id: str = CHAT_MODEL_ID,
) -> str:
    """Insert a minimal chat model row for the regression test."""
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 128000, true, 'chat', 'openrouter')"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": "GPT-4o"},
    )
    await session.commit()
    return model_id


# ---------------------------------------------------------------------------
# api_key_info fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key.

    Uses the HTTP admin API so the key is properly hashed in the DB.
    Pattern mirrors tests/embeddings_endpoint/conftest.py::api_key_info.
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "AudioTest",
            "email": "audio-test@example.io",
            "password": "audio battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "audio-test@example.io", "password": "audio battery test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "audio-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


# ---------------------------------------------------------------------------
# Convenience: inject FakeAudioProvider into app.state.provider_registry
# ---------------------------------------------------------------------------


def inject_fake_openai_audio_provider(
    app: Any,
    fake_provider: FakeAudioProvider | None = None,
) -> FakeAudioProvider:
    """Mount a FakeAudioProvider as 'openai' in app.state.provider_registry.

    Creates a new FakeAudioProvider if none is supplied.
    Preserves any existing 'openrouter' entry in the registry by reconstructing it.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    if fake_provider is None:
        fake_provider = FakeAudioProvider(name="openai")

    existing_registry = getattr(app.state, "provider_registry", None)
    existing_openrouter = None
    if existing_registry is not None:
        existing_openrouter = existing_registry.get("openrouter")

    providers: dict[str, Any] = {"openai": fake_provider}
    if existing_openrouter is not None:
        providers["openrouter"] = existing_openrouter

    app.state.provider_registry = ProviderRegistry(providers)
    return fake_provider
