"""Suite-local fixtures for provider-generation-id-capture (v30 t6.2) — TASK.md §4.

Reuses the global app/db_session/client fixtures (tests/conftest.py); adds a
DB-backed api_key (distinct tenant/email) for the flusher round-trip test, mirroring
the stream_usage_completeness SU6 pattern.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    """Signup -> login -> create key (DB-backed; distinct tenant/email)."""
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "GenIdCaptureTest",
            "email": "gen-id-capture-test@example.io",
            "password": "gen id capture battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "gen-id-capture-test@example.io", "password": "gen id capture battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "gen-id-capture-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }
