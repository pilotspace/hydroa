"""Suite-local fixtures for chat_modality_guard tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: app, client, db_session, settings (no local override needed
— the coarse chat modality guard is unconditional, mirroring images/embeddings/TTS's precedent).

Fakes:
  - FakeCompletionUpstream — records complete()/stream() calls; used for chat regression
  - SpyRecorder            — counts record() invocations; satisfies UsageRecorder protocol

Seed helper:
  - seed_chat_model — accepts a `modality` override (default "chat") so a test can seed a
    WRONG-modality model at the chat endpoint to exercise the new coarse guard, mirroring
    preset_capability_validation's seed_image_model/seed_embedding_model/seed_tts_model.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Seed helper — models
# ---------------------------------------------------------------------------


async def seed_chat_model(
    session: AsyncSession,
    *,
    model_id: str,
    modality: str = "chat",
    provider: str = "openrouter",
    active: bool = True,
) -> str:
    """Insert a models row (default modality="chat") + per_token pricing snapshot.

    `modality` is overridable so a test can seed a WRONG-modality model (e.g. "embedding")
    directly at the chat endpoint, to exercise the new coarse guard.
    """
    await session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, modality, provider)"
            " VALUES (:id, :name, 4096, :active, :modality, :provider)"
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
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token)"
            " VALUES (:id, :mid, 0.0000025, 0.00001)"
            " ON CONFLICT DO NOTHING"
        ),
        {"id": snap_id, "mid": model_id},
    )
    await session.commit()
    return model_id


# ---------------------------------------------------------------------------
# FakeCompletionUpstream — records complete()/stream() calls (chat)
# ---------------------------------------------------------------------------

CHAT_UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-cmg-1",
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
            yield b'data: {"id":"gen-cmg1","choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()


def inject_fake_completion_upstream(app: Any) -> FakeCompletionUpstream:
    """Install a FakeCompletionUpstream at app.state.completion_upstream."""
    fake = FakeCompletionUpstream()
    app.state.completion_upstream = fake
    return fake


# ---------------------------------------------------------------------------
# SpyRecorder — usage recorder spy
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
# api_key_info fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup -> login -> create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ChatModalityGuardTenant",
            "email": "chat-modality-guard@example.io",
            "password": "chat modality guard test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "chat-modality-guard@example.io",
                "password": "chat modality guard test",
            },
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "chat-modality-guard-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
