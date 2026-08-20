"""Suite-local fixtures for upload-bounds-audio (TASK.md §CHECKS — SECURITY,
direction beat, red-first).

A second app with an artificially SMALL audio cap (max_audio_upload_bytes=1024) so the
boundary probes stay byte-cheap, sharing the primary test DB without re-running
create_all — the tests/auth_hardening/conftest.py `make_hardening_app` shape. Audio
plumbing (FakeAudioProvider, SpyRecorder, seed_stt_model) reused cross-suite from
tests/audio_endpoints/conftest.py, the same copies that suite already exercises.

RED at the current tree: the /v1/audio/ route cap carries NO multipart headroom (it
equals max_audio_upload_bytes), so every over/at-cap file is pre-empted at the edge with
ERR_REQUEST_BODY_TOO_LARGE; the per-file ERR_PAYLOAD_AUDIO_TOO_LARGE check,
`_audio_route_cap`, and the capped audio reader do not exist. DO NOT weaken these tests
to pass; that is Build's job.

NOTE: `Settings.model_config` has `extra="ignore"`, so passing an override for a knob
that already exists (max_audio_upload_bytes) is safe pre- and post-Build.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from gateway.core.config import Settings
from gateway.main import create_app
from tests import _redis_env
from tests.audio_endpoints.conftest import (
    FAKE_AUDIO_BYTES,
    STT_MODEL_ID,
    STT_RESPONSE_BODY,
    FakeAudioProvider,
    SpyRecorder,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

__all__ = [
    "FAKE_AUDIO_BYTES",
    "STT_MODEL_ID",
    "STT_RESPONSE_BODY",
    "FakeAudioProvider",
    "SpyRecorder",
    "inject_fake_openai_audio_provider",
    "seed_stt_model",
]

REDIS_URL = _redis_env.TEST_REDIS_URL

TRANSCRIPTIONS = "/v1/audio/transcriptions"
TRANSLATIONS = "/v1/audio/translations"

# Small cap so boundary bodies stay tiny; multipart framing for our fields is well under
# the 1 MiB headroom the contract requires the route cap to carry above this.
SMALL_AUDIO_CAP = 1024


def build_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": TEST_DATABASE_URL,
        "jwt_secret": TEST_JWT_SECRET,
        "redis_url": REDIS_URL,
        "max_audio_upload_bytes": SMALL_AUDIO_CAP,
        # fixture plumbing only (the api_key signup path) — mirrors tests/conftest.py's
        # primary app settings; nothing under test reads it.
        "public_signup_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
async def bounds_app(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """(app, client) with the small audio cap, faked openai audio provider, and a
    SpyRecorder on app.state.usage_recorder. Disposed on teardown."""
    settings = build_settings()
    application = create_app(settings)
    install_stub_resolver(application)
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application),  # type: ignore[arg-type]
        base_url="http://test",
    )
    async with client:
        yield application, client
    await application.state.engine.dispose()


@pytest.fixture
async def api_key(bounds_app: tuple[Any, httpx.AsyncClient]) -> str:
    """Signup → login → create key against the bounds app (unique email per run —
    the DB is shared with the primary suite session)."""
    import uuid as _uuid

    _, client = bounds_app
    email = f"bounds-{_uuid.uuid4().hex[:10]}@uploadbounds.io"
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "UploadBounds", "email": email, "password": "bounds battery test"},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login", json={"email": email, "password": "bounds battery test"}
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "bounds-ci"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return created.json()["key"]


def multipart_audio(file_bytes: bytes) -> dict[str, Any]:
    """The files= mapping for an STT request with a controlled file size."""
    return {"file": ("clip.wav", file_bytes, "audio/wav")}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"{resp.status_code} != {status}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"{body.get('code')} != {code}: {body}"
    return body
