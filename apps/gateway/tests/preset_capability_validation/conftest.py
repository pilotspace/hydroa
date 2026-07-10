"""Suite-local fixtures for preset_capability_validation tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: app, client, db_session (built from the local settings
fixture below, which enables input_modality_guard_enabled=True — the chat/STT regression tests
need it ON; the images/embeddings/TTS coarse guard is unconditional and unaffected by the flag).

Fakes:
  - FakeCompletionUpstream — records complete()/stream() calls; used for chat regression + realtime
  - FakeUpstreamProvider   — records post_json/post_multipart/stream_bytes calls; one fake
                             satisfies images/embeddings (post_json), STT (post_multipart),
                             and TTS (stream_bytes) — mirrors the shape already used across
                             images_endpoint/embeddings_endpoint/audio_endpoints/input_modality_guard.
  - SpyRecorder            — counts record() invocations; satisfies UsageRecorder protocol

Seed helpers (raw SQL, mirroring sibling suites):
  - seed_chat_model / seed_stt_model   — accept input_modalities (chat/STT regression tests)
  - seed_image_model / seed_embedding_model / seed_tts_model — accept a `modality` override so
    a test can seed a WRONG-modality model directly at any of these three endpoints
  - seed_preset                       — raw INSERT into tenant_model_presets (bypasses the
    admin API's active-model validation — tests seed the target model themselves beforehand)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings

# ---------------------------------------------------------------------------
# Test DB / Redis constants (mirror the root conftest)
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test"
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# Settings fixture — overrides root conftest with the guard enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Local override: input-modality guard enabled.

    The chat/STT regression scenarios require GATEWAY_INPUT_MODALITY_GUARD_ENABLED=true
    (TASK.md §2). The NEW images/embeddings/TTS coarse guard is unconditional — this flag
    is irrelevant to it, so enabling it here has no effect on those tests.
    """
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        input_modality_guard_enabled=True,
    )


# ---------------------------------------------------------------------------
# Seed helpers — models
# ---------------------------------------------------------------------------


async def seed_chat_model(
    session: AsyncSession,
    *,
    model_id: str,
    input_modalities: str = "text",
    provider: str = "openrouter",
    active: bool = True,
) -> str:
    """Insert a chat model row with the given input_modalities CSV into the test DB."""
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider, input_modalities)"
            " VALUES (:id, :name, 4096, :active, 'chat', :provider, :im)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "provider": provider,
            "im": input_modalities,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token)"
            " VALUES (:id, :mid, 0.0000025, 0.00001)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_stt_model(
    session: AsyncSession,
    *,
    model_id: str,
    input_modalities: str = "audio",
    provider: str = "openai",
    active: bool = True,
) -> str:
    """Insert an audio_stt model row with the given input_modalities CSV."""
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider, input_modalities)"
            " VALUES (:id, :name, 0, :active, 'audio_stt', :provider, :im)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "provider": provider,
            "im": input_modalities,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_second', '0.000006')"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_image_model(
    session: AsyncSession,
    *,
    model_id: str,
    modality: str = "image",
    provider: str = "openai",
    active: bool = True,
) -> str:
    """Insert a models row (default modality="image") + per_image pricing snapshot.

    `modality` is overridable so a test can seed a WRONG-modality model (e.g. "chat")
    directly at the images endpoint, to exercise the new coarse guard.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 0, :active, :modality, :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "modality": modality,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_image', '0.04')"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_embedding_model(
    session: AsyncSession,
    *,
    model_id: str,
    modality: str = "embedding",
    provider: str = "openai",
    active: bool = True,
) -> str:
    """Insert a models row (default modality="embedding") + per_token pricing snapshot.

    `modality` is overridable so a test can seed a WRONG-modality model (e.g. "chat")
    directly at the embeddings endpoint, to exercise the new coarse guard.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 8192, :active, :modality, :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "modality": modality,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0.00000002, 0, 'per_token', NULL)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_tts_model(
    session: AsyncSession,
    *,
    model_id: str,
    modality: str = "audio_tts",
    provider: str = "openai",
    active: bool = True,
) -> str:
    """Insert a models row (default modality="audio_tts") + per_character pricing snapshot.

    `modality` is overridable so a test can seed a WRONG-modality model (e.g. "embedding")
    directly at the TTS endpoint, to exercise the new coarse guard.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 0, :active, :modality, :provider)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "modality": modality,
            "provider": provider,
        },
    )
    snap_id = str(uuid.uuid4())
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token,"
            "  pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_character', '0.000015')"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


# ---------------------------------------------------------------------------
# Seed helper — tenant model presets
# ---------------------------------------------------------------------------


async def seed_preset(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    preset_name: str,
    alias_key: str,
    target_model: str,
) -> None:
    """Raw INSERT into tenant_model_presets — bypasses the admin API's active-model
    validation (tests seed the target model row themselves beforehand via the helpers
    above). Mirrors the ``tenant_model_presets`` schema (tenant_id, preset_name, alias_key,
    target_model — see gateway.proxy.infrastructure.orm.TenantModelPresetRow).
    """
    await session.execute(
        text(
            "INSERT INTO tenant_model_presets (tenant_id, preset_name, alias_key, target_model)"
            " VALUES (:tenant_id, :preset_name, :alias_key, :target_model)"
            " ON CONFLICT (tenant_id, preset_name, alias_key)"
            " DO UPDATE SET target_model = EXCLUDED.target_model"
        ),
        {
            "tenant_id": str(tenant_id),
            "preset_name": preset_name,
            "alias_key": alias_key,
            "target_model": target_model,
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# FakeCompletionUpstream — records complete()/stream() calls (chat)
# ---------------------------------------------------------------------------

CHAT_UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-pcv-1",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
}


class FakeCompletionUpstream:
    """Minimal CompletionUpstream fake — records calls, returns preset 200."""

    def __init__(self) -> None:
        self.calls: int = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return 200, CHAT_UPSTREAM_BODY

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-pcv1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


def inject_fake_completion_upstream(app: Any) -> FakeCompletionUpstream:
    """Install a FakeCompletionUpstream at app.state.completion_upstream."""
    fake = FakeCompletionUpstream()
    app.state.completion_upstream = fake
    return fake


# ---------------------------------------------------------------------------
# FakeUpstreamProvider — one fake covers post_json/post_multipart/stream_bytes
# ---------------------------------------------------------------------------


class FakeUpstreamProvider:
    """Generic UpstreamProvider fake — records every post_json/post_multipart/stream_bytes call.

    A single implementation satisfies images (post_json), embeddings (post_json),
    STT (post_multipart), and TTS (stream_bytes) — mirrors the UpstreamProvider Protocol
    shape already duplicated per-suite across images_endpoint/embeddings_endpoint/
    audio_endpoints/input_modality_guard.
    """

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_json_calls: list[dict[str, Any]] = []
        self.post_multipart_calls: list[dict[str, Any]] = []
        self.stream_bytes_calls: list[dict[str, Any]] = []
        self._post_json_response: tuple[int, dict[str, Any]] = (
            200,
            {"data": [{"url": "https://example.com/x.png"}]},
        )
        self._multipart_response: tuple[int, dict[str, Any]] = (
            200,
            {"task": "transcribe", "duration": 5.0, "text": "hello world"},
        )
        self._stream_chunks: list[bytes] = [b"audio-bytes"]

    def set_post_json_response(self, status: int, body: dict[str, Any]) -> None:
        self._post_json_response = (status, body)

    def set_multipart_response(self, status: int, body: dict[str, Any]) -> None:
        self._multipart_response = (status, body)

    def set_stream_chunks(self, chunks: list[bytes]) -> None:
        self._stream_chunks = chunks

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.post_json_calls.append({"path": path, "payload": dict(payload)})
        return self._post_json_response

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


def inject_fake_provider(
    app: Any,
    fake_provider: FakeUpstreamProvider | None = None,
    provider_name: str = "openai",
) -> FakeUpstreamProvider:
    """Mount a FakeUpstreamProvider as `provider_name` in app.state.provider_registry.

    Preserves any existing 'openrouter' entry (mirrors every sibling suite's
    inject_fake_*_provider helper).
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    if fake_provider is None:
        fake_provider = FakeUpstreamProvider(name=provider_name)

    existing = getattr(app.state, "provider_registry", None)
    existing_openrouter = None
    if existing is not None:
        existing_openrouter = existing.get("openrouter")

    providers: dict[str, Any] = {provider_name: fake_provider}
    if existing_openrouter is not None:
        providers["openrouter"] = existing_openrouter

    app.state.provider_registry = ProviderRegistry(providers)
    return fake_provider


# ---------------------------------------------------------------------------
# SpyRecorder — tracks record() invocations
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Usage recorder that records every record() call for assertion."""

    supported_extras: frozenset[str] = frozenset(
        {
            "team_id",
            "cached",
            "guardrail_blocked",
            "blocked_by",
            "pii_masked",
            "pricing_unit",
            "quantity",
            "usage_source",
            "provider_generation_id",
            "disconnect_estimate",
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
# api_key_info fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "PresetCapValTenant",
            "email": "preset-cap-val@example.io",
            "password": "preset capability test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "preset-cap-val@example.io", "password": "preset capability test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "preset-cap-val-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
