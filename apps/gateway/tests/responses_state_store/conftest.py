"""Shared fixtures for the responses-state-store red/green suite (PLAN.md §4).

Mirrors tests/responses_api_core/conftest.py: reuses the root conftest.py's
app/client/db_session (real Postgres, fresh schema per test); fakes are
injected via app.state (completion_upstream, usage_recorder). No network.

Anti-vacuity rule (PLAN.md §4): every 404 assertion checks the problem+json
``code`` — FastAPI's route-absent default ``{"detail": "Not Found"}`` must
NEVER satisfy a test. That is what keeps this suite red today.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RESPONSES = "/v1/responses"
ADMIN_KEYS = "/admin/keys"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"

UNKNOWN_RESPONSE_ID = "resp_ffffffffffffffffffffffff"


class FakeCompletionUpstream:
    """Captures the internal (chat-shaped) payload it receives and plays back
    a scripted non-stream body / stream chunk list — chaining assertions read
    `last_payload` to prove the materialized context order."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
        stream_chunks: list[bytes] | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else dict(TEXT_UPSTREAM_BODY)
        self.stream_chunks = stream_chunks if stream_chunks is not None else list(TEXT_SSE_CHUNKS)
        self.calls = 0
        self.last_payload: dict[str, Any] | None = None

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        self.last_payload = payload
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1
        self.last_payload = payload
        chunks = self.stream_chunks

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return _gen()


class FakeUsageRecorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        usage: dict[str, Any] | None,
        status: int,
        **_kwargs: Any,
    ) -> None:
        self.records.append(
            {
                "tenant_id": tenant_id,
                "key_id": key_id,
                "model": model,
                "usage": usage,
                "status": status,
            }
        )


TEXT_UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-abc123",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
    "created": 1721800000,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello there!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}

TEXT_SSE_CHUNKS: list[bytes] = [
    b'data: {"id":"chatcmpl-s1","model":"openai/gpt-4o",'
    b'"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"id":"chatcmpl-s1","choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"id":"chatcmpl-s1","choices":[{"delta":{"content":"lo"}}]}\n\n',
    b'data: {"id":"chatcmpl-s1","choices":[{"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n',
    b"data: [DONE]\n\n",
]


def auth_bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    sr = await client.post(
        SIGNUP, json={"tenant_name": tenant_name, "email": email, "password": password}
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def _make_key(client: httpx.AsyncClient, *, tenant_name: str, email: str) -> dict[str, str]:
    jwt, tenant_id = await signup_and_login(client, tenant_name=tenant_name, email=email)
    created = await client.post(ADMIN_KEYS, json={"name": "ci"}, headers=auth_jwt(jwt))
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": jwt,
    }


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Tenant A: signup -> login -> create key."""
    return await _make_key(client, tenant_name="Acme", email="ada@acme.io")


@pytest.fixture
async def api_key_b(client: httpx.AsyncClient) -> dict[str, str]:
    """Tenant B — the adversary in every cross-tenant probe."""
    return await _make_key(client, tenant_name="Mallory Corp", email="mal@mallory.io")


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, provider)"
            " VALUES (:i, :n, 128000, true, 'openrouter')"
            " ON CONFLICT (id) DO UPDATE SET provider = 'openrouter'"
        ),
        {"i": model_id, "n": model_id},
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


def responses_payload(model: str, *, input_: Any = "hello", **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "input": input_}
    body.update(extra)
    return body


def parse_named_sse(raw: bytes) -> list[tuple[str | None, dict[str, Any]]]:
    frames: list[tuple[str | None, dict[str, Any]]] = []
    for frame in raw.split(b"\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        event_name: str | None = None
        data: dict[str, Any] | None = None
        for line in frame.split(b"\n"):
            if line.startswith(b"event:"):
                event_name = line[len(b"event:") :].strip().decode()
            elif line.startswith(b"data:"):
                payload = line[len(b"data:") :].strip()
                if payload == b"[DONE]":
                    data = {"__done_sentinel__": True}
                else:
                    data = json.loads(payload)
        if data is not None:
            frames.append((event_name, data))
    return frames


# ---------------------------------------------------------------------------
# DB helpers (stored_responses — the table this task adds; queries here are
# reached only after the HTTP act, so pre-build reds fail on the wire first)
# ---------------------------------------------------------------------------


async def count_stored_rows(db_session: AsyncSession) -> int:
    return int(
        (await db_session.execute(text("SELECT count(*) FROM stored_responses"))).scalar_one()
    )


async def fetch_stored_row(db_session: AsyncSession, response_id: str) -> Any:
    return (
        await db_session.execute(
            text(
                "SELECT id, tenant_id, key_id, model, status, previous_response_id,"
                " chain_depth, context_messages, response_body, usage"
                " FROM stored_responses WHERE id = :rid"
            ),
            {"rid": response_id},
        )
    ).first()


async def set_zdr(db_session: AsyncSession, tenant_id: str, enabled: bool) -> None:
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = :en WHERE id = :tid"),
        {"en": enabled, "tid": tenant_id},
    )
    await db_session.commit()


async def seed_stored_row(
    db_session: AsyncSession,
    *,
    response_id: str,
    tenant_id: str,
    key_id: str,
    chain_depth: int = 0,
    context_messages: list[dict[str, Any]] | None = None,
    response_body: dict[str, Any] | None = None,
    age_days: int = 0,
) -> None:
    """Direct INSERT arrange for cap/sweeper tests (wire-level act still gates)."""
    await db_session.execute(
        text(
            "INSERT INTO stored_responses"
            " (id, tenant_id, key_id, model, status, previous_response_id, chain_depth,"
            "  context_messages, response_body, usage, created_at)"
            " VALUES (:id, :tid, :kid, 'openai/gpt-4o', 'completed', NULL, :depth,"
            "  :ctx, :body, :usage, now() - (:age * interval '1 day'))"
        ),
        {
            "id": response_id,
            "tid": tenant_id,
            "kid": key_id,
            "depth": chain_depth,
            "ctx": json.dumps(context_messages) if context_messages is not None else None,
            "body": json.dumps(response_body) if response_body is not None else None,
            "usage": json.dumps({"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            "age": age_days,
        },
    )
    await db_session.commit()
