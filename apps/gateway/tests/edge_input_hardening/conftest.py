"""Suite-local fixtures for edge-input-hardening (S2+S3+S4 TASK.md §4).

Overrides the root ``settings`` fixture with SMALL body-size caps (2 KiB JSON / 4 KiB
audio) so the S4 body-size-cap scenarios can be exercised with small, fast test payloads
instead of real multi-MiB bodies — the exact same comparison logic
(``running_total > cap`` / ``declared > cap``) is exercised either way; only the numeric
threshold differs. Everything else (app/client/db_session) is inherited unchanged from the
root conftest.py.

Reuses ``tests.audio_endpoints.conftest``'s ``api_key_info`` / audio-provider-fake helpers
for the one true end-to-end S4 scenario (a legitimate under-cap audio upload completing a
real transcription).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET

# Small, fast-to-construct caps for the S4 scenarios in this suite.
SMALL_MAX_JSON_BODY_BYTES = 2_000
SMALL_MAX_AUDIO_UPLOAD_BYTES = 4_000


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        max_json_body_bytes=SMALL_MAX_JSON_BODY_BYTES,
        max_audio_upload_bytes=SMALL_MAX_AUDIO_UPLOAD_BYTES,
    )


# Re-export the audio_endpoints helpers this suite's S4 audio scenarios need, so tests
# import from one local module instead of reaching into a sibling suite's conftest.
from tests.audio_endpoints.conftest import (  # noqa: E402  (after fixture def, re-export)
    FakeAudioProvider,
    inject_fake_openai_audio_provider,
    seed_stt_model,
)

__all__ = [
    "FakeAudioProvider",
    "inject_fake_openai_audio_provider",
    "seed_stt_model",
]


@pytest.fixture
async def api_key_info(client: object) -> AsyncIterator[dict[str, str]]:
    """Signup -> login -> create key; returns ids + plaintext key (mirrors audio_endpoints)."""
    signup = await client.post(  # type: ignore[attr-defined]
        "/admin/auth/signup",
        json={
            "tenant_name": "EdgeHardeningTest",
            "email": "edge-hardening-test@example.io",
            "password": "edge hardening battery test",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post(  # type: ignore[attr-defined]
            "/admin/auth/login",
            json={
                "email": "edge-hardening-test@example.io",
                "password": "edge hardening battery test",
            },
        )
    ).json()["access_token"]

    created = await client.post(  # type: ignore[attr-defined]
        "/admin/keys",
        json={"name": "edge-hardening-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    yield {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
