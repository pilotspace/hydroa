"""Suite-local fixtures for openrouter-recovery-sweep (v30 t6.3) — TASK.md §4.

Reuses the global app/db_session/client fixtures (tests/conftest.py); adds a
DB-backed api_key (distinct tenant/email) so the sweep can read real ledger rows
and call the (spied) recovery service per unrecovered client_disconnect row.
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
            "tenant_name": "RecoverySweepTest",
            "email": "recovery-sweep-test@example.io",
            "password": "recovery sweep battery pack",
        },
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login",
            json={
                "email": "recovery-sweep-test@example.io",
                "password": "recovery sweep battery pack",
            },
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": "recovery-sweep-ci"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }
