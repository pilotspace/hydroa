"""Failing-first (RED) suite for cost-attribution-tags (TASK.md, contract FROZEN @ v1).

One test per scenario in §2 SCENARIOS.

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason —
primarily because `usage_records` has no `tags` column yet (ORM/migration absent), the
`X-Gateway-Tags` header is never parsed (`use_cases.py` has no `_parse_tags_header`), and
`GET /admin/usage/cost-by-tag` does not exist (404) before BUILD lands.

Infrastructure (mirrors tests/team_attribution/test_team_attribution.py and
tests/usage/test_usage_metering.py's B4 durability tests):
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (schema rebuilt per test via conftest.py)
  - Real Redis at redis://localhost:6380/9 (flushed per test via redis_client fixture)
  - httpx.ASGITransport (no network)
  - FakeCompletionUpstream injected via app.state.completion_upstream
  - flush_once() called explicitly (deterministic; no background flusher timing needed)
  - Some read-path (aggregation-only) scenarios arrange fixture rows via a direct SQL
    INSERT helper (insert_direct_usage_row) rather than driving the full proxy pipeline —
    a legitimate black-box arrange step for GET /admin/usage/cost-by-tag's SQL behavior;
    every write-path/validation scenario (M1-M4, R1-R5) still drives the REAL HTTP
    POST /v1/chat/completions -> recorder -> flusher -> ledger pipeline end to end.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Route constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
COMPLETIONS = "/v1/chat/completions"
COST_BY_TAG = "/admin/usage/cost-by-tag"

TAGS_HEADER = "X-Gateway-Tags"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",
) -> tuple[str, str]:
    """Sign up a new tenant+owner; return (jwt_token, tenant_id_str)."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
) -> dict[str, Any]:
    """POST /admin/keys; assert 201; return body (body["key"] = plaintext key)."""
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


def member_token_from_owner(owner_token: str, *, email: str) -> str:
    """Mint a role="member" JWT for the SAME tenant (mirrors key_governance's
    test_member_cannot_rotate idiom — no invite-acceptance flow needed for a
    pure authz-gate test)."""
    import jwt as pyjwt

    from tests.conftest import TEST_JWT_SECRET

    owner_claims = pyjwt.decode(
        owner_token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": owner_claims["tenant_id"],
        "email": email,
        "role": "member",
        "iss": "ai-proxy",
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    return pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"Expected {status}, got {resp.status_code}: {resp.text}"
    assert resp.json()["code"] == code, f"Expected code={code}, got {resp.json()}"


def chat_payload(model: str, *, stream: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": "hi"}]}
    if stream:
        payload["stream"] = True
    return payload


async def insert_direct_usage_row(
    db_session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    key_id: uuid.UUID | None = None,
    model_id: str = "direct-insert-model",
    cost_usd: str,
    tags: dict[str, str],
    status: int = 200,
    created_at: datetime.datetime | None = None,
) -> uuid.UUID:
    """Arrange a usage_records row directly via SQL (bypassing the proxy pipeline) —
    used only by read-path (pure aggregation) scenarios that need EXACT cost_usd
    fixture values; every write-path scenario drives the real HTTP pipeline instead.
    """
    row_id = uuid.uuid4()
    cols = "id, tenant_id, key_id, model_id, status, cost_usd, raw, tags"
    vals = ":id, :tenant_id, :key_id, :model_id, :status, :cost_usd, :raw, :tags"
    params: dict[str, Any] = {
        "id": row_id,
        "tenant_id": str(tenant_id),
        "key_id": key_id or uuid.uuid4(),
        "model_id": model_id,
        "status": status,
        "cost_usd": cost_usd,
        "raw": "{}",
        "tags": json.dumps(tags),
    }
    if created_at is not None:
        cols += ", created_at"
        vals += ", :created_at"
        params["created_at"] = created_at
    await db_session.execute(text(f"INSERT INTO usage_records ({cols}) VALUES ({vals})"), params)
    await db_session.commit()
    return row_id


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeCompletionUpstream:
    """Non-streaming + streaming fake upstream (mirrors team_attribution's pattern),
    with a streaming usage frame so extract_usage_from_sse finds a complete frame."""

    def __init__(
        self,
        status: int = 200,
        body: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.body = (
            body
            if body is not None
            else {
                "id": "gen-cat-1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "total_tokens": 1500,
                },
            }
        )
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            yield b'data: {"id":"gen-cat-s1","choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield (
                b'data: {"id":"gen-cat-s1","choices":[{"delta":{}}],'
                b'"usage":{"prompt_tokens":10,"completion_tokens":5,"total_tokens":15}}\n\n'
            )
            yield b"data: [DONE]\n\n"

        return _gen()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test."""
    import redis.asyncio as aioredis

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def fake_upstream(app: object) -> FakeCompletionUpstream:
    """Inject FakeCompletionUpstream into app.state; return for inspection."""
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    return upstream


@pytest.fixture
async def wired_recorder(app: object, redis_client: Any) -> Any:
    """Wire a REAL RecordingUsageRecorder (write-behind, Redis-stream) into app.state —
    the write-path scenarios need the real recorder/flusher pipeline, not the
    in-process _HarnessUsageRecorder (which never persists usage_records rows)."""
    from gateway.usage.application.recorder import RecordingUsageRecorder

    recorder = RecordingUsageRecorder(redis=redis_client, session_factory=app.state.sessionmaker)
    app.state.usage_recorder = recorder  # type: ignore[attr-defined]
    return recorder


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    """Insert a minimal active model + pricing snapshot so recorder computes non-zero cost."""
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true)"
            " ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now())"
            " ON CONFLICT DO NOTHING"
        ),
        {
            "sid": str(snap_id),
            "mid": model_id,
            "p": "0.000001",
            "c": "0.000002",
        },
    )
    await db_session.commit()
    return model_id


async def _settle_dispatch(redis_client: Any, *, quiet_checks: int = 3, timeout_s: float = 2.0) -> None:
    """Drain the fire-and-forget usage-record dispatch race before flushing.

    `_dispatch_record` (proxy/application/use_cases.py) fires usage recording via
    `asyncio.ensure_future(...)` and returns the HTTP response WITHOUT awaiting it
    (by design — billing must never add latency to the client-facing response).
    Immediately after `await client.post(...)` (or `asyncio.gather` of several)
    returns, those ensure_future tasks are merely SCHEDULED, not necessarily run —
    calling flush_once() before they land races a stream write that hasn't
    happened yet (a test-harness race, not a product bug — and left unresolved,
    a still-pending task can leak PAST this test's teardown and corrupt the NEXT
    test's freshly-rebuilt DB with a stale tenant_id once it finally executes).
    Poll the stream length until it stops growing (quiescent for `quiet_checks`
    consecutive samples) instead of a fixed sleep, so this scales to any number
    of in-flight dispatches without hardcoding a count.
    """
    from gateway.usage.infrastructure.redis_stream import STREAM_KEY

    deadline = asyncio.get_event_loop().time() + timeout_s
    last_len = -1
    stable = 0
    while asyncio.get_event_loop().time() < deadline:
        length = await redis_client.xlen(STREAM_KEY)
        if length == last_len:
            stable += 1
            if stable >= quiet_checks:
                return
        else:
            stable = 0
            last_len = length
        await asyncio.sleep(0.01)


async def _flush(redis_client: Any, app: object) -> None:
    from gateway.usage.application.flusher import UsageLedgerFlusher

    await _settle_dispatch(redis_client)
    flusher = UsageLedgerFlusher(redis=redis_client, session_factory=app.state.sessionmaker)
    await flusher.flush_once()


async def _tags_for(db_session: AsyncSession, *, model_id: str) -> list[dict[str, Any]]:
    rows = (
        await db_session.execute(
            text("SELECT tags FROM usage_records WHERE model_id = :m ORDER BY created_at ASC"),
            {"m": model_id},
        )
    ).fetchall()
    return [dict(r[0]) for r in rows]


# ===========================================================================
# S1 — untagged request is byte-identical to pre-task behavior (M3)
# TRUE-RED REASON: usage_records has no tags column -> SELECT tags fails
# (ProgrammingError) before the byte-identical assertion can even run.
# ===========================================================================


async def test_untagged_request_byte_identical(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo1", email="owner1@cat.io")
    key_body = await create_key(client, jwt, name="untagged-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS, json=chat_payload(active_model), headers=auth_key(key)
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"
    assert resp.json() == fake_upstream.body, "body must be byte-identical to upstream's"

    await _flush(redis_client, app)

    rows = (
        await db_session.execute(
            text("SELECT tags FROM usage_records WHERE model_id = :m"), {"m": active_model}
        )
    ).fetchall()
    assert len(rows) == 1, f"expected 1 usage_record row, got {len(rows)}"
    assert dict(rows[0][0]) == {}, f"expected tags={{}}, got {rows[0][0]!r}"


# ===========================================================================
# S2 — a valid tags header is persisted on a non-streaming request (M1, M4)
# ===========================================================================


async def test_valid_tags_persisted_non_streaming(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo2", email="owner2@cat.io")
    key_body = await create_key(client, jwt, name="tagged-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: json.dumps({"team": "platform", "project": "gw"})},
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"
    assert resp.json() == fake_upstream.body, "tags header must not affect the proxied response"

    await _flush(redis_client, app)
    tags_rows = await _tags_for(db_session, model_id=active_model)
    assert len(tags_rows) == 1
    assert tags_rows[0] == {"team": "platform", "project": "gw"}


# ===========================================================================
# S3 — a valid tags header is persisted on a streaming request (parity) (M1, M2)
# ===========================================================================


async def test_valid_tags_persisted_streaming_parity(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo3", email="owner3@cat.io")
    key_body = await create_key(client, jwt, name="tagged-stream-key")
    key = key_body["key"]

    async with client.stream(
        "POST",
        COMPLETIONS,
        json=chat_payload(active_model, stream=True),
        headers={**auth_key(key), TAGS_HEADER: json.dumps({"team": "platform"})},
    ) as resp:
        assert resp.status_code == 200, f"stream failed: {resp.status_code}"
        async for _ in resp.aiter_bytes():
            pass

    await _flush(redis_client, app)
    tags_rows = await _tags_for(db_session, model_id=active_model)
    assert len(tags_rows) == 1, "stream() must reach the SAME tags-persistence seam as complete()"
    assert tags_rows[0] == {"team": "platform"}


# ===========================================================================
# S4 — an explicit empty object is accepted and stored as empty (M1, M3 edge)
# ===========================================================================


async def test_explicit_empty_object_accepted(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo4", email="owner4@cat.io")
    key_body = await create_key(client, jwt, name="empty-tags-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: "{}"},
    )
    assert resp.status_code == 200, f"an explicit empty object must not be rejected: {resp.text}"

    await _flush(redis_client, app)
    tags_rows = await _tags_for(db_session, model_id=active_model)
    assert tags_rows == [{}]


# ===========================================================================
# S5 — case-sensitive keys are stored as sent, never normalized (M1 edge)
# ===========================================================================


async def test_case_sensitive_keys_preserved(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo5", email="owner5@cat.io")
    key_body = await create_key(client, jwt, name="case-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: json.dumps({"Team": "a", "team": "b"})},
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    await _flush(redis_client, app)
    tags_rows = await _tags_for(db_session, model_id=active_model)
    assert tags_rows == [{"Team": "a", "team": "b"}], "case must never be folded/merged"


# ===========================================================================
# S6 — malformed JSON in the tags header is rejected before billing (R1)
# ===========================================================================


async def test_malformed_json_rejected_before_billing(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo6", email="owner6@cat.io")
    key_body = await create_key(client, jwt, name="malformed-json-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: "{not valid json"},
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert fake_upstream.calls == 0, "upstream must never be called for a malformed header"

    await _flush(redis_client, app)
    rows = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE model_id = :m"), {"m": active_model}
        )
    ).fetchone()
    assert rows[0] == 0, "no usage_records row for a rejected (never-billed) request"


# ===========================================================================
# S7 — a nested/non-string tag value is rejected (R1)
# ===========================================================================


async def test_nested_value_rejected(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo7", email="owner7@cat.io")
    key_body = await create_key(client, jwt, name="nested-value-key")
    key = key_body["key"]

    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={
            **auth_key(key),
            TAGS_HEADER: json.dumps({"team": {"nested": "object"}}),
        },
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert fake_upstream.calls == 0

    await _flush(redis_client, app)
    rows = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM usage_records WHERE model_id = :m"), {"m": active_model}
        )
    ).fetchone()
    assert rows[0] == 0


# ===========================================================================
# S8 — too many tags is rejected (R2)
# ===========================================================================


async def test_too_many_tags_rejected(
    client: httpx.AsyncClient,
    app: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo8", email="owner8@cat.io")
    key_body = await create_key(client, jwt, name="too-many-key")
    key = key_body["key"]

    nine_tags = {f"k{i}": f"v{i}" for i in range(9)}
    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: json.dumps(nine_tags)},
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert "too many tags" in resp.json().get("detail", "")
    assert fake_upstream.calls == 0


# ===========================================================================
# S9 — an over-length tag key is rejected (R3)
# ===========================================================================


async def test_overlength_key_rejected(
    client: httpx.AsyncClient,
    app: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(client, tenant_name="CatCo9", email="owner9@cat.io")
    key_body = await create_key(client, jwt, name="overlength-key-key")
    key = key_body["key"]

    long_key = "a" * 33
    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: json.dumps({long_key: "v"})},
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert fake_upstream.calls == 0


# ===========================================================================
# S10 — an over-length tag value is rejected (R4)
# ===========================================================================


async def test_overlength_value_rejected(
    client: httpx.AsyncClient,
    app: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo10", email="owner10@cat.io"
    )
    key_body = await create_key(client, jwt, name="overlength-value-key")
    key = key_body["key"]

    long_value = "a" * 257
    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: json.dumps({"team": long_value})},
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert fake_upstream.calls == 0


# ===========================================================================
# S11 — an oversized header is rejected without attempting to parse it (R5)
# ===========================================================================


async def test_oversized_header_rejected(
    client: httpx.AsyncClient,
    app: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo11", email="owner11@cat.io"
    )
    key_body = await create_key(client, jwt, name="oversized-header-key")
    key = key_body["key"]

    oversized = json.dumps({"team": "a" * 3000})
    assert len(oversized.encode("utf-8")) > 2048
    resp = await client.post(
        COMPLETIONS,
        json=chat_payload(active_model),
        headers={**auth_key(key), TAGS_HEADER: oversized},
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert "too large" in resp.json().get("detail", "")
    assert fake_upstream.calls == 0, "json.loads must never run on an oversized header"


# ===========================================================================
# S12 — a member-role identity is refused the breakdown endpoint (R6)
# ===========================================================================


async def test_member_role_refused_breakdown(
    client: httpx.AsyncClient,
) -> None:
    owner_token, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo12", email="owner12@cat.io"
    )
    member_token = member_token_from_owner(owner_token, email="member12@cat.io")

    resp = await client.get(COST_BY_TAG, headers=auth_jwt(member_token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    body_text = resp.text.lower()
    assert "usage_records" not in body_text
    assert "breakdown" not in body_text


# ===========================================================================
# S13 — an invalid window value is rejected on the breakdown endpoint (R7)
# ===========================================================================


async def test_invalid_window_rejected(
    client: httpx.AsyncClient,
) -> None:
    owner_token, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo13", email="owner13@cat.io"
    )
    resp = await client.get(
        COST_BY_TAG, params={"window": "bogus"}, headers=auth_jwt(owner_token)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S14 — a malformed tag_key filter is rejected (R8)
# ===========================================================================


async def test_malformed_tag_key_filter_rejected(
    client: httpx.AsyncClient,
) -> None:
    owner_token, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo14", email="owner14@cat.io"
    )
    resp = await client.get(
        COST_BY_TAG, params={"tag_key": "***invalid***"}, headers=auth_jwt(owner_token)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# S15 — a well-formed but unused tag_key returns an empty breakdown (M8)
# ===========================================================================


async def test_unused_tag_key_returns_empty_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_token, tenant_id = await signup_and_login(
        client, tenant_name="CatCo15", email="owner15@cat.io"
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_id, cost_usd="1.00", tags={"team": "a"}
    )

    resp = await client.get(
        COST_BY_TAG, params={"tag_key": "nonexistent-key"}, headers=auth_jwt(owner_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["breakdown"] == []
    assert body["truncated"] is False
    # Totals reflect the FULL window, unfiltered by tag_key.
    assert body["total_cost_usd"] == "1.00" or Decimal(body["total_cost_usd"]) == Decimal("1.00")
    assert body["total_requests"] == 1


# ===========================================================================
# S16 — default-window breakdown reconciles tagged/untagged/total cost (M5, M6)
# ===========================================================================


async def test_default_window_breakdown_reconciles(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_token, tenant_id = await signup_and_login(
        client, tenant_name="CatCo16", email="owner16@cat.io"
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_id, cost_usd="1.00", tags={"team": "a"}
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_id, cost_usd="1.00", tags={"team": "a"}
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_id, cost_usd="2.00", tags={"team": "a", "project": "x"}
    )
    await insert_direct_usage_row(db_session, tenant_id=tenant_id, cost_usd="0.50", tags={})

    resp = await client.get(COST_BY_TAG, headers=auth_jwt(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert Decimal(body["total_cost_usd"]) == Decimal("4.50")
    assert body["total_requests"] == 4
    assert Decimal(body["untagged_cost_usd"]) == Decimal("0.50")
    assert body["untagged_requests"] == 1

    by_pair = {(item["tag_key"], item["tag_value"]): item for item in body["breakdown"]}
    team_a = by_pair[("team", "a")]
    assert Decimal(team_a["cost_usd"]) == Decimal("4.00")
    assert team_a["request_count"] == 3
    project_x = by_pair[("project", "x")]
    assert Decimal(project_x["cost_usd"]) == Decimal("2.00")
    assert project_x["request_count"] == 1

    # Breakdown rows are overlapping cost SLICES — their sum ("6.00") intentionally
    # exceeds total_cost_usd ("4.50") because the multi-tagged row counts twice.
    breakdown_sum = sum(Decimal(item["cost_usd"]) for item in body["breakdown"])
    assert breakdown_sum == Decimal("6.00")
    assert breakdown_sum > Decimal(body["total_cost_usd"])


# ===========================================================================
# S17 — an empty window returns explicit zeros, never 404 (M9)
# ===========================================================================


async def test_empty_window_returns_zeros(
    client: httpx.AsyncClient,
) -> None:
    owner_token, _tenant_id = await signup_and_login(
        client, tenant_name="CatCo17", email="owner17@cat.io"
    )
    resp = await client.get(COST_BY_TAG, params={"window": "day"}, headers=auth_jwt(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["total_cost_usd"]) == Decimal("0")
    assert body["total_requests"] == 0
    assert Decimal(body["untagged_cost_usd"]) == Decimal("0")
    assert body["breakdown"] == []
    assert body["truncated"] is False


# ===========================================================================
# S18 — high-cardinality tag values are truncated, not unbounded (M7)
# ===========================================================================


async def test_high_cardinality_truncated(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_token, tenant_id = await signup_and_login(
        client, tenant_name="CatCo18", email="owner18@cat.io"
    )
    for i in range(150):
        await insert_direct_usage_row(
            db_session,
            tenant_id=tenant_id,
            cost_usd=str(Decimal("0.01") * (i + 1)),
            tags={"k": f"v{i}"},
        )

    resp = await client.get(COST_BY_TAG, headers=auth_jwt(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["breakdown"]) == 100
    assert body["truncated"] is True
    costs = [Decimal(item["cost_usd"]) for item in body["breakdown"]]
    assert costs == sorted(costs, reverse=True), "breakdown must be ordered cost_usd DESC"


# ===========================================================================
# S19 — tenant isolation holds on both write and read (M5, R6 cross-cutting)
# ===========================================================================


async def test_tenant_isolation_on_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    owner_a, tenant_a = await signup_and_login(
        client, tenant_name="CatCoA19", email="ownerA19@cat.io"
    )
    _owner_b, tenant_b = await signup_and_login(
        client, tenant_name="CatCoB19", email="ownerB19@cat.io"
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_a, cost_usd="3.00", tags={"team": "platform"}
    )
    await insert_direct_usage_row(
        db_session, tenant_id=tenant_b, cost_usd="99.00", tags={"team": "platform"}
    )

    resp = await client.get(COST_BY_TAG, headers=auth_jwt(owner_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total_requests"] == 1
    assert Decimal(body["total_cost_usd"]) == Decimal("3.00")
    by_pair = {(item["tag_key"], item["tag_value"]): item for item in body["breakdown"]}
    assert Decimal(by_pair[("team", "platform")]["cost_usd"]) == Decimal("3.00")
    assert by_pair[("team", "platform")]["request_count"] == 1


# ===========================================================================
# S20 — a durable-fallback (Redis XADD failure) row still carries its tags
# (R9, durability parity)
# ===========================================================================


class _XaddFailsRedis:
    """Redis stand-in whose XADD always fails (mirrors tests/usage's B4 fake)."""

    async def xadd(self, *args: Any, **kwargs: Any) -> None:
        raise ConnectionError("XADD unavailable (_XaddFailsRedis fake)")

    async def incrbyfloat(self, *args: Any, **kwargs: Any) -> float:
        raise ConnectionError("incrbyfloat unavailable (_XaddFailsRedis fake)")


async def test_durable_fallback_carries_tags(
    app: Any,
    db_session: AsyncSession,
    active_model: str,
) -> None:
    from gateway.usage.application.recorder import RecordingUsageRecorder

    recorder = RecordingUsageRecorder(
        redis=_XaddFailsRedis(),  # type: ignore[arg-type]
        session_factory=app.state.sessionmaker,
    )
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"
        ),
        {"id": tenant_id, "name": "FallbackTagsTenant"},
    )
    await db_session.commit()

    result = await recorder.record(
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model=active_model,
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        status=200,
        tags={"team": "platform"},
    )
    assert result is None, "record() must never raise into the proxy path"

    rows = (
        await db_session.execute(
            text("SELECT tags FROM usage_records WHERE tenant_id = :tid"), {"tid": tenant_id}
        )
    ).fetchall()
    assert len(rows) == 1, "XADD failure must leave a durable row via the fallback"
    assert dict(rows[0][0]) == {"team": "platform"}, (
        "the fallback path is not a second, tag-blind code path"
    )


# ===========================================================================
# S21 — a pre-deploy (old-format) Redis-stream event replays cleanly
# (backward-compat edge)
# ===========================================================================


async def test_old_format_event_replays_cleanly(
    app: Any,
    db_session: AsyncSession,
) -> None:
    from gateway.usage.application.flusher import insert_usage_row

    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
        {"id": tenant_id, "name": "OldFormatTenant"},
    )
    await db_session.commit()

    # A pre-deploy event: no "tags" field at all (RecordingUsageRecorder never
    # emitted one before this task shipped).
    old_format_fields: dict[str, str] = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "key_id": str(key_id),
        "model_id": "old-format-model",
        "prompt_tokens": "10",
        "completion_tokens": "5",
        "cost_usd": "0.001",
        "status": "200",
        "raw": "{}",
    }
    record_id = uuid.uuid4()

    # Must NOT raise MalformedUsageEventError over a missing "tags" field.
    await insert_usage_row(app.state.sessionmaker, record_id=record_id, fields=old_format_fields)

    row = (
        await db_session.execute(
            text("SELECT tags FROM usage_records WHERE id = :id"), {"id": record_id}
        )
    ).fetchone()
    assert row is not None
    assert dict(row[0]) == {}, "old-event-safe default: missing tags -> {}"


# ===========================================================================
# S22 — concurrent identically-tagged requests never contend (concurrency edge)
# ===========================================================================


async def test_concurrent_tagged_requests_no_contention(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    redis_client: Any,
    wired_recorder: Any,
    fake_upstream: FakeCompletionUpstream,
    active_model: str,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="CatCo22", email="owner22@cat.io"
    )
    key_body = await create_key(client, jwt, name="concurrent-key")
    key = key_body["key"]
    headers = {**auth_key(key), TAGS_HEADER: json.dumps({"team": "platform"})}

    resp1, resp2 = await asyncio.gather(
        client.post(COMPLETIONS, json=chat_payload(active_model), headers=headers),
        client.post(COMPLETIONS, json=chat_payload(active_model), headers=headers),
    )
    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text

    await _flush(redis_client, app)

    rows = (
        await db_session.execute(
            text("SELECT cost_usd, tags FROM usage_records WHERE model_id = :m"),
            {"m": active_model},
        )
    ).fetchall()
    assert len(rows) == 2, "two independent rows — append-only, no lost update"
    assert all(dict(r[1]) == {"team": "platform"} for r in rows)
    expected_sum = sum((Decimal(str(r[0])) for r in rows), Decimal("0"))

    breakdown_resp = await client.get(COST_BY_TAG, headers=auth_jwt(jwt))
    assert breakdown_resp.status_code == 200, breakdown_resp.text
    body = breakdown_resp.json()
    by_pair = {(item["tag_key"], item["tag_value"]): item for item in body["breakdown"]}
    team_platform = by_pair[("team", "platform")]
    assert team_platform["request_count"] == 2
    assert Decimal(team_platform["cost_usd"]) == expected_sum
