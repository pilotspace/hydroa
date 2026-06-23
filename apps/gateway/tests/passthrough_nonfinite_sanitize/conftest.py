"""Suite-local fixtures for passthrough_nonfinite_sanitize tests (v33).

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis. The shared
root conftest.py provides: settings, app, client, db_session.

Verifies the three sibling passthrough render sites (images / embeddings / chat-non-stream)
null-replace a non-finite upstream float instead of 500ing on JSONResponse serialization.

Fakes mirror tests/images_endpoint + tests/embeddings_endpoint + tests/budgets exactly:
  - FakeUpstreamProvider   — provider-registry seam (images, embeddings)
  - FakeCompletionUpstream — app.state.completion_upstream seam (chat)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

IMAGE_MODEL_ID = "dall-e-3"
EMBED_MODEL_ID = "text-embedding-3-small"
CHAT_MODEL_ID = "openai/gpt-4o"


class FakeUpstreamProvider:
    """Minimal UpstreamProvider that returns a preset (status, body) from post_json."""

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_json_calls: list[dict[str, Any]] = []
        self._post_json_response: tuple[int, dict[str, Any]] = (200, {})

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


class FakeCompletionUpstream:
    """Minimal non-streaming chat fake — returns a preset (status, body) from complete()."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else {"id": "gen-1", "choices": []}
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b"data: [DONE]\n\n"

        return _gen()


def inject_fake_openai_provider(
    app: Any, fake_provider: FakeUpstreamProvider | None = None
) -> FakeUpstreamProvider:
    """Mount a FakeUpstreamProvider as 'openai' in app.state.provider_registry."""
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    if fake_provider is None:
        fake_provider = FakeUpstreamProvider(name="openai")
    existing = getattr(app.state, "provider_registry", None)
    providers: dict[str, Any] = {"openai": fake_provider}
    if existing is not None and existing.get("openrouter") is not None:
        providers["openrouter"] = existing.get("openrouter")
    app.state.provider_registry = ProviderRegistry(providers)
    return fake_provider


async def seed_image_model(session: AsyncSession, model_id: str = IMAGE_MODEL_ID) -> str:
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 0, true, 'image', 'openai') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": model_id},
    )
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token,"
            " completion_usd_per_token, pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, 0, 0, 'per_image', '0.04') ON CONFLICT DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_embedding_model(session: AsyncSession, model_id: str = EMBED_MODEL_ID) -> str:
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 8192, true, 'embedding', 'openai') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": model_id},
    )
    await session.execute(
        text(
            "INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token,"
            " completion_usd_per_token, pricing_unit, unit_usd_per_unit)"
            " VALUES (:id, :mid, '0.00000002', 0, 'per_token', NULL) ON CONFLICT DO NOTHING"
        ),
        {"id": str(uuid.uuid4()), "mid": model_id},
    )
    await session.commit()
    return model_id


async def seed_chat_model(session: AsyncSession, model_id: str = CHAT_MODEL_ID) -> str:
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 128000, true, 'chat', 'openrouter') ON CONFLICT (id) DO NOTHING"
        ),
        {"id": model_id, "name": "GPT-4o"},
    )
    await session.commit()
    return model_id


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    """Signup → login → create API key; returns ids + plaintext key (owner role)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "NonFiniteCo",
            "email": "owner@nonfinite.io",
            "password": "correct horse battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "owner@nonfinite.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "nf-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
