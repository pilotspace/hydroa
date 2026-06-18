"""Suite-local fixtures for stt-duration-cap tests (TASK.md §4).

Reuses the audio_endpoints harness (FakeAudioProvider / SpyRecorder / seed_stt_model /
inject_fake_openai_audio_provider) for HTTP-level STT billing, plus a synthetic-WAV
builder so a PRECISE derived duration drives the clamp. The cap is injected per-test on
`app.state.settings.stt_max_duration_seconds` as a SMALL value, so an over-cap case needs
only a few seconds of audio (not the 4h production default).

Infra: real Postgres (localhost:5433) + Redis (localhost:6380 db 9) via the root conftest.
"""

from __future__ import annotations

import struct
from typing import Any

import pytest

TRANSCRIPTIONS_PATH = "/v1/audio/transcriptions"


def make_wav_bytes(*, seconds: float = 3.0, sample_rate: int = 8000) -> bytes:
    """Build a minimal valid mono 8-bit PCM WAV of the given duration.

    A real RIFF/WAVE header + fmt chunk + data chunk so tinytag reports an exact
    duration (data_size / byte_rate == seconds). At 8000 B/s a few seconds of audio
    is a few KB — small enough to exceed a 2s test cap without a giant payload.
    """
    channels = 1
    bits = 8
    byte_rate = sample_rate * channels * bits // 8
    data = b"\x80" * round(byte_rate * seconds)
    fmt_chunk = struct.pack(
        "<IHHIIHH",
        16,  # PCM fmt chunk size
        1,  # audio format = PCM
        channels,
        sample_rate,
        byte_rate,
        channels * bits // 8,  # block align
        bits,
    )
    return (
        b"RIFF"
        + struct.pack("<I", 36 + len(data))
        + b"WAVE"
        + b"fmt "
        + fmt_chunk
        + b"data"
        + struct.pack("<I", len(data))
        + data
    )


def multipart_files(
    *,
    filename: str = "audio.mp3",  # deliberately a LIE — derivation must sniff the bytes
    content: bytes | None = None,
    content_type: str = "audio/mpeg",
) -> dict[str, Any]:
    """Build the httpx multipart files dict; defaults to a valid 3s WAV under an mp3 name."""
    if content is None:
        content = make_wav_bytes(seconds=3.0)
    return {"file": (filename, content, content_type)}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Signup → login → create key (distinct tenant/email from the other audio suites)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "SttDurationCapTest",
            "email": "stt-cap-test@example.io",
            "password": "stt cap battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "stt-cap-test@example.io", "password": "stt cap battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "stt-cap-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }
