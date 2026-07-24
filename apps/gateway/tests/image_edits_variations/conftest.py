"""Suite-local fixtures for image_edits_variations tests.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9).
The shared root conftest.py provides: settings, app, client, db_session.

Key fakes defined here:
  - FakeMultipartProvider — records post_multipart calls; returns preset (status, body);
    can be configured to raise UpstreamUnavailableError (AU10/IE10 upstream-unavailable case).
  - SpyRecorder           — counts record() invocations; satisfies UsageRecorder protocol

Seed helpers:
  - seed_edit_capable_model — raw SQL insert for an edit/variation-CAPABLE image model
    (input_modalities="text,image") + per_image pricing_snapshot. Mirrors the future
    dall-e-2 migration row this task's §3 Contract seeds for real traffic; tests never
    depend on the migration itself (test-local, idempotent).
  - seed_image_model    — raw SQL insert for a generations-ONLY image model
    (input_modalities="text", e.g. dall-e-3) — used to prove the capability-gate rejects it.
  - seed_chat_model     — raw SQL insert for a chat model (modality mismatch case).

Pattern mirrors tests/images_endpoint/conftest.py and tests/audio_endpoints/conftest.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Response / payload constants
# ---------------------------------------------------------------------------

EDIT_CAPABLE_MODEL_ID = "dall-e-2"
GENERATIONS_ONLY_MODEL_ID = "dall-e-3"
CHAT_MODEL_ID = "openai/gpt-4o"

FAKE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # tiny fake PNG payload, well under any cap
FAKE_LARGE_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 4096

EDIT_RESPONSE_BODY: dict[str, Any] = {
    "created": 1234567890,
    "data": [{"url": "https://example.com/edited1.png"}],
}

EDIT_RESPONSE_BODY_2: dict[str, Any] = {
    "created": 1234567890,
    "data": [
        {"url": "https://example.com/edited1.png"},
        {"url": "https://example.com/edited2.png"},
    ],
}

EDIT_RESPONSE_BODY_EMPTY: dict[str, Any] = {
    "created": 1234567890,
    "data": [],
}

VARIATION_RESPONSE_BODY: dict[str, Any] = {
    "created": 1234567890,
    "data": [{"url": "https://example.com/variation1.png"}],
}

VARIATION_RESPONSE_BODY_2: dict[str, Any] = {
    "created": 1234567890,
    "data": [
        {"url": "https://example.com/variation1.png"},
        {"url": "https://example.com/variation2.png"},
    ],
}


# ---------------------------------------------------------------------------
# FakeMultipartProvider
# ---------------------------------------------------------------------------


class FakeMultipartProvider:
    """Minimal UpstreamProvider that records post_multipart calls.

    Implements the UpstreamProvider protocol (post_json / post_multipart / stream_bytes).
    Tests set the desired response via set_multipart_response(), or force an
    UpstreamUnavailableError via raise_unavailable().
    """

    def __init__(self, name: str = "openai") -> None:
        self.name = name
        self.post_multipart_calls: list[dict[str, Any]] = []
        self.post_json_calls: list[dict[str, Any]] = []
        self._multipart_response: tuple[int, dict[str, Any]] = (200, EDIT_RESPONSE_BODY)
        self._raise_unavailable = False

    def set_multipart_response(self, status: int, body: dict[str, Any]) -> None:
        self._multipart_response = (status, body)

    def raise_unavailable(self) -> None:
        self._raise_unavailable = True

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.post_json_calls.append({"path": path, "payload": dict(payload)})
        return (200, {"created": 1234567890, "data": []})

    async def post_multipart(
        self, path: str, files: dict[str, Any], data: dict[str, Any]
    ) -> tuple[int, dict[str, Any]]:
        self.post_multipart_calls.append(
            {"path": path, "files": dict(files), "data": dict(data)}
        )
        if self._raise_unavailable:
            from gateway.proxy.domain.errors import UpstreamUnavailableError

            raise UpstreamUnavailableError("upstream unavailable (forced by test)")
        return self._multipart_response

    def stream_bytes(self, path: str, payload: dict[str, Any]) -> Any:
        async def _gen() -> Any:
            yield b""

        return _gen()


# ---------------------------------------------------------------------------
# SpyRecorder
# ---------------------------------------------------------------------------


class SpyRecorder:
    """Minimal UsageRecorder-compatible spy.

    Records the kwargs from every record() call.
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
# Seed helpers
# ---------------------------------------------------------------------------


async def _seed_model(
    session: AsyncSession,
    *,
    model_id: str,
    active: bool,
    modality: str,
    provider: str,
    input_modalities: str,
    unit_usd_per_unit: str | None,
) -> str:
    await session.execute(
        text(
            "INSERT INTO models"
            " (id, name, context_length, active, modality, provider, input_modalities)"
            " VALUES (:id, :name, 0, :active, :modality, :provider, :input_modalities)"
            " ON CONFLICT (id) DO UPDATE SET input_modalities = EXCLUDED.input_modalities,"
            " modality = EXCLUDED.modality, provider = EXCLUDED.provider,"
            " active = EXCLUDED.active"
        ),
        {
            "id": model_id,
            "name": model_id,
            "active": active,
            "modality": modality,
            "provider": provider,
            "input_modalities": input_modalities,
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


async def seed_edit_capable_model(
    session: AsyncSession,
    *,
    model_id: str = EDIT_CAPABLE_MODEL_ID,
    active: bool = True,
    provider: str = "openai",
    unit_usd_per_unit: str | None = None,
) -> str:
    """Insert an edit/variation-CAPABLE image model row (input_modalities="text,image").

    Test-local stand-in for this task's §3 Contract migration (dall-e-2, SCOPE-CUT
    unit_usd_per_unit=NULL) — the RED suite does not depend on the real migration.
    """
    return await _seed_model(
        session,
        model_id=model_id,
        active=active,
        modality="image",
        provider=provider,
        input_modalities="text,image",
        unit_usd_per_unit=unit_usd_per_unit,
    )


async def seed_image_model(
    session: AsyncSession,
    *,
    model_id: str = GENERATIONS_ONLY_MODEL_ID,
    active: bool = True,
    provider: str = "openai",
    unit_usd_per_unit: str | None = None,
) -> str:
    """Insert a generations-ONLY image model row (input_modalities="text", e.g. dall-e-3).

    Used to prove the edit/variation capability gate rejects a model that can only
    do /v1/images/generations.
    """
    return await _seed_model(
        session,
        model_id=model_id,
        active=active,
        modality="image",
        provider=provider,
        input_modalities="text",
        unit_usd_per_unit=unit_usd_per_unit,
    )


async def seed_chat_model(
    session: AsyncSession,
    *,
    model_id: str = CHAT_MODEL_ID,
) -> str:
    """Insert a minimal chat model row (modality mismatch case)."""
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
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "ImageEditsTest",
            "email": "iev-test@example.io",
            "password": "image edits variations battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "iev-test@example.io",
                "password": "image edits variations battery test",
            },
        )
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": "iev-ci"},
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
# Convenience: inject FakeMultipartProvider into app.state.provider_registry
# ---------------------------------------------------------------------------


def inject_fake_openai_provider(
    app: Any,
    fake_provider: FakeMultipartProvider | None = None,
) -> FakeMultipartProvider:
    """Mount a FakeMultipartProvider as 'openai' in app.state.provider_registry.

    Preserves any existing 'openrouter' entry in the registry by reconstructing it.
    """
    from gateway.proxy.infrastructure.provider_registry import ProviderRegistry  # type: ignore[import]

    if fake_provider is None:
        fake_provider = FakeMultipartProvider(name="openai")

    existing_registry = getattr(app.state, "provider_registry", None)
    existing_openrouter = None
    if existing_registry is not None:
        existing_openrouter = existing_registry.get("openrouter")

    providers: dict[str, Any] = {"openai": fake_provider}
    if existing_openrouter is not None:
        providers["openrouter"] = existing_openrouter

    app.state.provider_registry = ProviderRegistry(providers)
    return fake_provider
