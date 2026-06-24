"""Local fixtures for reasoning-passthrough tests (SEAM B support)."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "AcmeR", "email": "reason@acme.io", "password": "correct horse battery"},
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "reason@acme.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci-reason"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
        "jwt": token,
    }


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o-reason"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id
