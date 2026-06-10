"""Red suite for proxy-completions (contract FROZEN @ v1).

One test per scenario in .add/tasks/proxy-completions/TASK.md §2.
Fakes are injected via app.state (completion_upstream, usage_recorder); model
rows are inserted directly into the catalog tables — no network anywhere.
"""

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

COMPLETIONS = "/v1/chat/completions"

UPSTREAM_BODY = {
    "id": "gen-123",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
}
SSE_CHUNKS = [
    b'data: {"id":"gen-123","choices":[{"delta":{"content":"h"}}]}\n\n',
    b'data: {"id":"gen-123","choices":[{"delta":{"content":"i"}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":7,"completion_tokens":2}}\n\n',
    b"data: [DONE]\n\n",
]


class FakeCompletionUpstream:
    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in SSE_CHUNKS:
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


@pytest.fixture
async def api_key(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup → login → create key; returns ids + plaintext key."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "Acme", "email": "ada@acme.io", "password": "correct horse battery"},
    )
    assert signup.status_code == 201
    token = (
        await client.post(
            "/admin/auth/login",
            json={"email": "ada@acme.io", "password": "correct horse battery"},
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci"}, headers={"Authorization": f"Bearer {token}"}
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
        {"i": model_id, "n": "GPT-4o"},
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


def install_upstream(app: Any, upstream: FakeCompletionUpstream) -> None:
    app.state.completion_upstream = upstream


def payload(model: str, stream: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
    if stream is not None:
        body["stream"] = stream
    return body


def auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status
    assert resp.json()["code"] == code


async def test_non_streaming_completion_forwarded_verbatim(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert resp.status_code == 200
    assert resp.json() == UPSTREAM_BODY
    assert resp.json()["usage"]["total_tokens"] == 9
    assert upstream.calls == 1


async def test_streaming_completion_sse_byte_identical(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(
        COMPLETIONS, json=payload(active_model, stream=True), headers=auth(api_key["key"])
    )

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.content == b"".join(SSE_CHUNKS)
    assert resp.content.endswith(b"data: [DONE]\n\n")


async def test_usage_recorder_invoked_after_completion(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    install_upstream(app, FakeCompletionUpstream())
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder

    resp = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert resp.status_code == 200
    assert len(recorder.records) == 1
    rec = recorder.records[0]
    assert str(rec["tenant_id"]) == api_key["tenant_id"]
    assert str(rec["key_id"]) == api_key["key_id"]
    assert rec["model"] == active_model
    assert rec["status"] == 200


async def test_missing_auth_rejected_no_upstream_call(
    client: httpx.AsyncClient, app: Any, active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(COMPLETIONS, json=payload(active_model))

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0


async def test_revoked_key_rejected_no_upstream_call(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)
    revoked = await client.delete(
        f"/admin/keys/{api_key['key_id']}", headers={"Authorization": f"Bearer {api_key['jwt']}"}
    )
    assert revoked.status_code == 204

    resp = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
    assert upstream.calls == 0


async def test_malformed_payload_rejected_no_upstream_call(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(
        COMPLETIONS,
        json={"model": active_model, "messages": []},
        headers=auth(api_key["key"]),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert upstream.calls == 0


async def test_unknown_model_rejected_no_upstream_call(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str]
) -> None:
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(
        COMPLETIONS, json=payload("ghost/model-x"), headers=auth(api_key["key"])
    )

    assert_problem(resp, 400, "ERR_MODEL_UNKNOWN")
    assert upstream.calls == 0


async def test_upstream_4xx_passed_through_verbatim(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    install_upstream(app, FakeCompletionUpstream(status=429, body={"error": "rate_limited"}))

    resp = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert resp.status_code == 429
    assert resp.json() == {"error": "rate_limited"}


async def test_upstream_5xx_returns_502(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    install_upstream(app, FakeCompletionUpstream(status=500, body={"error": "upstream_fail"}))

    resp = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")
    assert "upstream_fail" not in resp.text


async def test_circuit_breaker_opens_after_5_failures(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    upstream = FakeCompletionUpstream(status=500, body={"error": "boom"})
    install_upstream(app, upstream)

    for i in range(1, 6):
        resp = await client.post(
            COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"])
        )
        assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")
        assert upstream.calls == i, f"call {i} must reach upstream"

    sixth = await client.post(COMPLETIONS, json=payload(active_model), headers=auth(api_key["key"]))

    assert_problem(sixth, 502, "ERR_UPSTREAM_UNAVAILABLE")
    assert upstream.calls == 5, "breaker open: 6th call must NOT reach upstream"


async def test_streaming_chunks_match_exactly_what_upstream_sent(
    client: httpx.AsyncClient, app: Any, api_key: dict[str, str], active_model: str
) -> None:
    """Anti-tamper guard: the gateway must not parse/re-serialize SSE frames."""
    upstream = FakeCompletionUpstream()
    install_upstream(app, upstream)

    resp = await client.post(
        COMPLETIONS, json=payload(active_model, stream=True), headers=auth(api_key["key"])
    )

    raw = resp.content
    for chunk in SSE_CHUNKS:
        assert chunk in raw
    assert raw == b"".join(SSE_CHUNKS)
    assert json.loads(SSE_CHUNKS[2].removeprefix(b"data: "))["usage"]["prompt_tokens"] == 7
