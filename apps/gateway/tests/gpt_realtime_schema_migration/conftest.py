"""Suite-local fixtures for gpt-realtime-schema-migration tests (TASK.md §4).

Mirrors tests/tiered_token_billing/conftest.py's api_key fixture: usage_records.tenant_id
carries a NOT NULL FK to tenants.id, so a raw uuid4() would violate the constraint —
GSM2/GSM3 need a real tenant+key via the signup -> login -> key flow.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
async def api_key(client: Any) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={
            "tenant_name": "GptRealtimeSchemaTest",
            "email": "gpt-realtime-schema-test@example.io",
            "password": "gpt realtime schema battery",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "gpt-realtime-schema-test@example.io",
                "password": "gpt realtime schema battery",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "gpt-realtime-schema-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }
