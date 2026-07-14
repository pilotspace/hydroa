"""Shared fixtures for the anthropic-messages-ingress red/green suite (TASK.md §4).

Mirrors tests/proxy/test_proxy_completions.py's self-contained-fixture style;
reuses the root conftest.py's app/client/db_session (real Postgres, fresh
schema per test) — no network anywhere.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests import _redis_env

ADMIN_KEYS = "/admin/keys"
ADMIN_GUARDRAILS = "/admin/guardrails"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"


class FakeCompletionUpstream:
    """Captures the payload it receives (M7/M8 assertions) and plays back a
    scripted non-stream body / stream chunk list. Optionally raises mid-stream
    to exercise the SAME real UpstreamUnavailableError handling
    `/v1/chat/completions` streaming already relies on (R6)."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
        stream_chunks: list[bytes] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self.status = status
        self.body = body if body is not None else dict(TEXT_UPSTREAM_BODY)
        self.stream_chunks = stream_chunks if stream_chunks is not None else list(TEXT_SSE_CHUNKS)
        self.raise_exc = raise_exc
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
        raise_exc = self.raise_exc

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk
            if raise_exc is not None:
                raise raise_exc

        return _gen()


TEXT_UPSTREAM_BODY: dict[str, Any] = {
    "id": "msg-abc123",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
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
    "id": "msg-tool1",
    "object": "chat.completion",
    "model": "openai/gpt-4o",
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

TEXT_SSE_CHUNKS: list[bytes] = [
    b'data: {"id":"msg-s1","model":"openai/gpt-4o","choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"id":"msg-s1","choices":[{"delta":{"content":"Hel"}}]}\n\n',
    b'data: {"id":"msg-s1","choices":[{"delta":{"content":"lo"}}]}\n\n',
    b'data: {"id":"msg-s1","choices":[{"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n',
    b"data: [DONE]\n\n",
]

TOOL_SSE_CHUNKS: list[bytes] = [
    b'data: {"id":"msg-s2","model":"openai/gpt-4o","choices":[{"delta":{"role":"assistant"}}]}\n\n',
    b'data: {"id":"msg-s2","choices":[{"delta":{"tool_calls":'
    b'[{"index":0,"id":"call_xyz","type":"function",'
    b'"function":{"name":"get_weather","arguments":""}}]}}]}\n\n',
    b'data: {"id":"msg-s2","choices":[{"delta":{"tool_calls":'
    b'[{"index":0,"function":{"arguments":"{\\"city\\""}}]}}]}\n\n',
    b'data: {"id":"msg-s2","choices":[{"delta":{"tool_calls":'
    b'[{"index":0,"function":{"arguments":": \\"SF\\"}"}}]}}]}\n\n',
    b'data: {"id":"msg-s2","choices":[{"delta":{},"finish_reason":"tool_calls"}],'
    b'"usage":{"prompt_tokens":10,"completion_tokens":6,"total_tokens":16}}\n\n',
    b"data: [DONE]\n\n",
]

# A native OpenAI-shaped mid-stream error frame (the SAME shape
# use_cases.py::_sse_error_frame / anthropic_upstream.py's own
# _anthropic_error_to_openai already produce for an upstream failure).
ERROR_SSE_FRAME: bytes = (
    b'data: {"error":{"message":"upstream boom","type":"upstream_error",'
    b'"code":"ERR_UPSTREAM_UNAVAILABLE"}}\n\n'
)


def auth_bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def auth_x_api_key(key: str) -> dict[str, str]:
    return {"x-api-key": key}


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


@pytest.fixture
async def anthropic_model(db_session: AsyncSession) -> str:
    """A catalog model resolved to the direct Anthropic provider (M7/M8)."""
    return await _insert_model(db_session, "anthropic/claude-opus-4", provider="anthropic")


@pytest.fixture
async def bedrock_model(db_session: AsyncSession) -> str:
    """A catalog model resolved to a NON-Anthropic provider (M6/M7 drop case).

    Deliberately colon-free: a bare `model` field containing a colon is parsed
    as a `<preset>:<alias>` selector by the shared, unchanged
    `CompletionUseCase._resolve_preset` (model_presets.py::parse_preset_selector)
    BEFORE the catalog/provider check ever runs — a real AWS Bedrock model id
    like "us.anthropic.claude-3-5-sonnet-20241022-v2:0" would trip that
    pre-existing, out-of-scope behavior (ERR_PRESET_NOT_FOUND) and is unrelated
    to what these M6/M7 scenarios are testing (non-Anthropic-provider passthrough).
    """
    return await _insert_model(
        db_session, "bedrock/anthropic.claude-3-5-sonnet-20241022-v2", provider="bedrock"
    )


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis  # type: ignore[import-untyped]

    client: Any = aioredis.from_url(_redis_env.TEST_REDIS_URL, decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


def wire_rate_limiter(app: Any, redis: Any) -> None:
    from gateway.rate_limits.infrastructure.redis_lua_limiter import RedisLuaRateLimiter

    app.state.rate_limiter = RedisLuaRateLimiter(redis=redis)


def wire_budget_guard(app: Any, redis: Any) -> None:
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    app.state.budget_guard = RedisBudgetGuard(redis=redis, session_factory=app.state.sessionmaker)


def spend_key_for_key(key_id: str) -> str:
    import datetime

    yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
    return f"usage:spend:key:{key_id}:{yyyymm}"


async def create_key_with_budget(
    client: httpx.AsyncClient, jwt: str, *, monthly_budget_usd: str, name: str = "budget-key"
) -> dict[str, Any]:
    resp = await client.post(
        ADMIN_KEYS,
        json={"name": name, "monthly_budget_usd": monthly_budget_usd},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_key_with_rpm(
    client: httpx.AsyncClient, jwt: str, *, rpm_limit: int, name: str = "rpm-key"
) -> dict[str, Any]:
    resp = await client.post(
        ADMIN_KEYS, json={"name": name, "rpm_limit": rpm_limit}, headers=auth_jwt(jwt)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def anthropic_payload(
    model: str,
    *,
    max_tokens: int | None = 256,
    stream: bool | None = None,
    messages: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages if messages is not None else [{"role": "user", "content": "hello"}],
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if stream is not None:
        body["stream"] = stream
    body.update(extra)
    return body


def parse_anthropic_sse(raw: bytes) -> list[dict[str, Any]]:
    """Parse `event: X\\ndata: {...}\\n\\n` frames into a list of data dicts."""
    import json

    events: list[dict[str, Any]] = []
    for frame in raw.split(b"\n\n"):
        frame = frame.strip()
        if not frame:
            continue
        for line in frame.split(b"\n"):
            if line.startswith(b"data:"):
                events.append(json.loads(line[len(b"data:") :].strip()))
    return events
