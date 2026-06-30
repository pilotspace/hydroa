"""Suite-local fixtures for stt-nonfinite-passthrough tests (TASK.md §4).

Reuses the audio_endpoints harness (FakeAudioProvider / SpyRecorder / seed_stt_model /
inject_fake_openai_audio_provider). The upstream BODY is set per-test via
FakeAudioProvider.set_multipart_response to inject non-finite floats (nan/inf/-inf);
the uploaded file is the truncated FAKE_AUDIO_BYTES (its derived duration is irrelevant —
these tests assert response-body sanitization, not billing).

Infra: real Postgres (localhost:5433) + Redis (localhost:6380 db 9) via the root conftest.
"""

from __future__ import annotations

from typing import Any

import pytest

# Truncated WAV — decodes to None (the $0 billing fixture); body is what matters here.
from tests.audio_endpoints.conftest import FAKE_AUDIO_BYTES

TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


def multipart_files(
    *, filename: str = "audio.mp3", content_type: str = "audio/mpeg"
) -> dict[str, Any]:
    """httpx multipart dict; the file content is irrelevant to body sanitization."""
    return {"file": (filename, FAKE_AUDIO_BYTES, content_type)}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key (distinct tenant/email from the other audio suites)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "SttNonfiniteTest",
            "email": "stt-nonfinite-test@example.io",
            "password": "stt nonfinite battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "stt-nonfinite-test@example.io", "password": "stt nonfinite battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "stt-nonfinite-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
