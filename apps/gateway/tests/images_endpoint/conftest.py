"""Suite-local fixtures for images_endpoint tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: settings, app, client, db_session.

Key fakes defined here:
  - FakeUpstreamProvider  — records post_json calls; returns preset (status, body)
  - SpyRecorder           — counts record() invocations; satisfies UsageRecorder protocol
  - FakeCompletionUpstream — records complete() calls; used in IM10 chat regression

Seed helpers:
  - seed_image_model   — raw SQL insert for models + per_image pricing_snapshots
  - seed_chat_model    — raw SQL insert for chat model (IM10)

Pattern mirrors tests/embeddings_endpoint/conftest.py exactly.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ---------------------------------------------------------------------------
# Response / payload constants
# ---------------------------------------------------------------------------

IMAGE_MODEL_ID = "dall-e-3"
CHAT_MODEL_ID = "openai/gpt-4o"

IMAGE_RESPONSE_BODY: dict[str, Any] = {
    "created": 1234567890,
    "data": [{"url": "https://example.com/image1.png", "revised_prompt": "a white cat"}],
}

IMAGE_RESPONSE_BODY_2: dict[str, Any] = {
    "created": 1234567890,
    "data": [
        {"url": "https://example.com/image1.png", "revised_prompt": "two cats"},
        {"url": "https://example.com/image2.png", "revised_prompt": "two cats again"},
    ],
}

CHAT_RESPONSE_BODY: dict[str, Any] = {
    "id": "gen-im-chat-1",
    "object": "chat.completion",
    "model": CHAT_MODEL_ID,
    "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
}


# ---------------------------------------------------------------------------
# FakeUpstreamProvider
# ---------------------------------------------------------------------------


class FakeUpstreamProvider:
    """Minimal UpstreamProvider that records post_json calls.

    Implements the UpstreamProvider protocol (post_json / post_multipart / stream_bytes).
    Tests set the desired response via set_post_json_response().
    """

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_json_calls: list[dict[str, Any]] = []
        self._post_json_response: tuple[int, dict[str, Any]] = (200, IMAGE_RESPONSE_BODY)

    def set_post_json_response(self, status: int, body: dict[str, Any]) -> None:
        self._post_json_response = (status, body)

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.post_json_calls.append({"path": path, "payload": dict(payload)})
        return self._post_json_response

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        return (200, {"text": "transcription"})

    def stream_bytes(self, path: str, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        async def _gen() -> AsyncIterator[bytes]:
            yield b"audio-bytes"

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
# FakeCompletionUpstream (for IM10 chat regression)
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
            yield b'data: {"id":"gen-im1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def seed_image_model(
    session: AsyncSession,
    *,
    model_id: str = IMAGE_MODEL_ID,
    active: bool = True,
    modality: str = "image",
    provider: str = "openai",
    unit_usd_per_unit: str = "0.04",
) -> str:
    """Insert an image model row + per_image pricing snapshot into the test DB.

    Uses ON CONFLICT DO NOTHING so the helper is idempotent within a test.
    Returns model_id for convenience.
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
            " VALUES (:id, :mid, 0, 0, 'per_image', :upu)"
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
    """Insert a minimal chat model row for IM10 regression."""
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
            "tenant_name": "ImagesTest",
            "email": "im-test@example.io",
            "password": "images battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "im-test@example.io", "password": "images battery test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "im-ci"},
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
# Convenience: inject FakeUpstreamProvider into app.state.provider_registry
# ---------------------------------------------------------------------------


def inject_fake_openai_provider(
    app: Any,
    fake_provider: FakeUpstreamProvider | None = None,
) -> FakeUpstreamProvider:
    """Mount a FakeUpstreamProvider as 'openai' in app.state.provider_registry.

    Creates a new FakeUpstreamProvider if none is supplied.
    Preserves any existing 'openrouter' entry in the registry by reconstructing it.

    NOTE: This function imports ProviderRegistry — it will raise ImportError in
    the red phase only if ProviderRegistry itself is absent. ProviderRegistry already
    exists (provider-seam BUILD is done), so this import succeeds in the red phase.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    if fake_provider is None:
        fake_provider = FakeUpstreamProvider(name="openai")

    existing_registry = getattr(app.state, "provider_registry", None)
    existing_openrouter = None
    if existing_registry is not None:
        existing_openrouter = existing_registry.get("openrouter")

    providers: dict[str, Any] = {"openai": fake_provider}
    if existing_openrouter is not None:
        providers["openrouter"] = existing_openrouter

    app.state.provider_registry = ProviderRegistry(providers)
    return fake_provider
