"""Shared fixtures for the responses-api-core red/green suite (PLAN.md §4).

Mirrors tests/anthropic_messages_ingress/conftest.py's self-contained-fixture
style; reuses the root conftest.py's app/client/db_session (real Postgres,
fresh schema per test) — no network anywhere. Fakes are injected via
app.state (completion_upstream, usage_recorder).
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
CHAT_COMPLETIONS = "/v1/chat/completions"
ADMIN_KEYS = "/admin/keys"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"


class FakeCompletionUpstream:
    """Captures the internal (chat-shaped) payload it receives and plays back
    a scripted non-stream body / stream chunk list — the M2 translation
    assertions read `last_payload`."""

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

TOOL_CALL_UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-tool1",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
    "created": 1721800000,
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "SF"}',
                        },
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
}

LENGTH_UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-len1",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
    "created": 1721800000,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "truncated outp"},
            "finish_reason": "length",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 16, "total_tokens": 21},
}

DETAILED_USAGE_UPSTREAM_BODY: dict[str, Any] = {
    "id": "chatcmpl-du1",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
    "created": 1721800000,
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "ok"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "prompt_tokens_details": {"cached_tokens": 60},
        "completion_tokens_details": {"reasoning_tokens": 8},
    },
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

# A native OpenAI-shaped mid-stream error frame (the SAME shape
# use_cases.py::_sse_error_frame already produces for an upstream failure).
ERROR_SSE_CHUNKS: list[bytes] = [
    b'data: {"id":"chatcmpl-e1","model":"openai/gpt-4o",'
    b'"choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"id":"chatcmpl-e1","choices":[{"delta":{"content":"par"}}]}\n\n',
    b'data: {"error":{"message":"upstream boom","type":"upstream_error",'
    b'"code":"ERR_UPSTREAM_UNAVAILABLE"}}\n\n',
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


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create key; returns ids + plaintext key."""
    jwt, tenant_id = await signup_and_login(client, tenant_name="Acme", email="ada@acme.io")
    created = await client.post(ADMIN_KEYS, json={"name": "ci"}, headers=auth_jwt(jwt))
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": jwt,
    }


async def _insert_model(
    db_session: AsyncSession, model_id: str, *, provider: str = "openrouter"
) -> str:
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, provider)"
            " VALUES (:i, :n, 128000, true, :p) ON CONFLICT (id) DO UPDATE SET provider = :p"
        ),
        {"i": model_id, "n": model_id, "p": provider},
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


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """A plain catalog model whose provider is the default (openrouter)."""
    return await _insert_model(db_session, "openai/gpt-4o", provider="openrouter")


def responses_payload(
    model: str,
    *,
    input_: Any = "hello",
    stream: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "input": input_}
    if stream is not None:
        body["stream"] = stream
    body.update(extra)
    return body


def parse_named_sse(raw: bytes) -> list[tuple[str | None, dict[str, Any]]]:
    """Parse `event: X\\ndata: {...}\\n\\n` frames into (event_name, data) pairs.

    Tolerates data-only frames (event_name None) so assertions can prove the
    Responses stream is named-event-framed rather than chat-style data-only.
    """
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
