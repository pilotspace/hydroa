"""Suite-local fixtures for openrouter-cost-recovery (v30 t6.2b) — TASK.md §4.

Reuses the global app/db_session/client fixtures (tests/conftest.py); adds a
DB-backed api_key (distinct tenant/email) so the recovery service can read the
ledger and append a correction row through the real recorder→flusher pipeline.
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
            "tenant_name": "CostRecoveryTest",
            "email": "cost-recovery-test@example.io",
            "password": "cost recovery battery pack",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "cost-recovery-test@example.io",
                "password": "cost recovery battery pack",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "cost-recovery-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }
