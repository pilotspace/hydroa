"""Suite-local fixtures for input_modality_guard tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: app, client, db_session (built from the local settings fixture).

Local settings fixture overrides the root one to set input_modality_guard_enabled=True.
The flag-OFF parity test overrides app.state.settings.input_modality_guard_enabled = False inline.

Fakes:
  - FakeCompletionUpstream  — records complete() calls; returns preset 200
  - SpyRecorder             — counts record() invocations; satisfies UsageRecorder protocol
  - FakeAudioProvider       — records post_multipart calls; satisfies UpstreamProvider protocol
"""

from __future__ import annotations

import os
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

TEST_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
TEST_JWT_SECRET = "test-secret-not-for-production-0123456789"


# ---------------------------------------------------------------------------
# Settings fixture — overrides root conftest with guard enabled
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    """Local override: guard enabled.  Flag-OFF parity test mutates inline."""
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        public_signup_enabled=True,  # signup-and-routing-authz S1: this suite bootstraps via signup
        input_modality_guard_enabled=True,
    )


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

STT_AUDIO_MODEL_ID = "whisper-1"
STT_TEXT_ONLY_MODEL_ID = "text-only-stt"
CHAT_TEXT_ONLY_MODEL_ID = "text-only-x"
CHAT_VISION_MODEL_ID = "vision-y"

FAKE_AUDIO_BYTES = b"RIFF\x00\x00\x00\x00WAVEfmt "

STT_RESPONSE_BODY: dict[str, Any] = {
    "task": "transcribe",
    "language": "english",
    "duration": 5.0,
    "text": "Hello world",
}


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
    model_id: str = STT_AUDIO_MODEL_ID,
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


# ---------------------------------------------------------------------------
# FakeCompletionUpstream — records complete() calls
# ---------------------------------------------------------------------------

CHAT_UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-guard-1",
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
            yield b'data: {"id":"gen-g1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


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

    async def record(self, **kwargs: Any) -> None:
        self.calls.append(dict(kwargs))


# ---------------------------------------------------------------------------
# FakeAudioProvider — records post_multipart calls
# ---------------------------------------------------------------------------


class FakeAudioProvider:
    """Minimal UpstreamProvider fake for STT tests."""

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_multipart_calls: int = 0

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return 200, {}

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        self.post_multipart_calls += 1
        return 200, STT_RESPONSE_BODY

    def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b""

        return _gen()


# ---------------------------------------------------------------------------
# Helpers to inject fakes into app.state
# ---------------------------------------------------------------------------


def inject_fake_completion_upstream(app: Any) -> FakeCompletionUpstream:
    """Install a FakeCompletionUpstream at app.state.completion_upstream."""
    fake = FakeCompletionUpstream()
    app.state.completion_upstream = fake
    return fake


def inject_fake_audio_provider(app: Any, provider_name: str = "openai") -> FakeAudioProvider:
    """Install a FakeAudioProvider into app.state.provider_registry under provider_name."""
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    fake = FakeAudioProvider(name=provider_name)
    existing = getattr(app.state, "provider_registry", None)
    existing_openrouter = None
    if existing is not None:
        existing_openrouter = existing.get("openrouter")

    providers: dict[str, Any] = {provider_name: fake}
    if existing_openrouter is not None:
        providers["openrouter"] = existing_openrouter

    app.state.provider_registry = ProviderRegistry(providers)
    return fake


# ---------------------------------------------------------------------------
# api_key_info fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "GuardTestTenant",
            "email": "guard-test@example.io",
            "password": "guard battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "guard-test@example.io", "password": "guard battery test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "guard-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
