"""Suite-local fixtures for embeddings_endpoint tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: settings, app, client, db_session.

Key fakes defined here:
  - FakeUpstreamProvider  — records post_json calls; returns preset (status, body)
  - SpyRecorder           — counts record() invocations; satisfies UsageRecorder protocol
  - FakeCompletionUpstream — records complete() calls; used in EM11 chat regression

Seed helpers:
  - seed_embedding_model  — raw SQL insert for models + pricing_snapshots
  - seed_chat_model       — raw SQL insert for chat model (EM11)
  - api_key_info          — HTTP admin signup + login + key create; returns dict with key/ids

Pattern follows tests/pricing_units/conftest.py and tests/provider_seam/conftest.py.
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

EMBED_MODEL_ID = "text-embedding-3-small"
CHAT_MODEL_ID = "openai/gpt-4o"

EMBEDDING_RESPONSE_BODY: dict[str, Any] = {
    "object": "list",
    "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
    "model": EMBED_MODEL_ID,
    "usage": {"prompt_tokens": 5, "total_tokens": 5},
}

CHAT_RESPONSE_BODY: dict[str, Any] = {
    "id": "gen-em-chat-1",
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
        self._post_json_response: tuple[int, dict[str, Any]] = (200, EMBEDDING_RESPONSE_BODY)

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
# FakeCompletionUpstream (for EM11 chat regression)
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
            yield b'data: {"id":"gen-em1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def seed_embedding_model(
    session: AsyncSession,
    *,
    model_id: str = EMBED_MODEL_ID,
    active: bool = True,
    modality: str = "embedding",
    provider: str = "openai",
    prompt_usd_per_token: str = "0.00000002",
) -> str:
    """Insert an embedding model row + per_token pricing snapshot into the test DB.

    Uses ON CONFLICT DO NOTHING so the helper is idempotent within a test.
    Returns model_id for convenience.
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
            " VALUES (:id, :mid, :prompt, 0, 'per_token', NULL)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id, "prompt": prompt_usd_per_token},
    )
    await session.commit()
    return model_id


async def seed_chat_model(
    session: AsyncSession,
    *,
    model_id: str = CHAT_MODEL_ID,
) -> str:
    """Insert a minimal chat model row for EM11 regression."""
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
    Pattern mirrors tests/pricing_units/conftest.py::api_key.
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "EmbeddingsTest",
            "email": "em-test@example.io",
            "password": "embeddings battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "em-test@example.io", "password": "embeddings battery test"},
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "em-ci"},
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
    the red phase because the embeddings router/use-case do not exist yet, but the
    ProviderRegistry itself already exists (provider-seam BUILD is done).
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
