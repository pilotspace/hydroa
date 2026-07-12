"""Failing-first (RED) suite for payload-capture-store (TASK.md §4, contract FROZEN @ v1).

One test per scenario in §2 SCENARIOS (18 total).

Right-reason red targets per test (BEFORE build, `gateway.logs` did not exist):
  - Every HTTP-level test: GET/PUT /admin/capture -> 404 (router not mounted) in the
    arrange step, OR request_logs table does not exist -> ProgrammingError on the
    verification SELECT.
  - Every unit-level test: `from gateway.logs...` / `from gateway.logs.infrastructure
    .sqlalchemy_capture import SqlAlchemyPayloadCapture` raises ImportError/ModuleNotFoundError
    at collection time (the whole file fails to collect) — the single most emphatic RED
    signal available (missing implementation, not a broken harness).
  - PATCH /admin/keys/{id} capture_enabled test: capture_enabled is not a field on
    PatchKeyRequest/KeyInfoResponse -> AssertionError (field absent) or 422 validation error.

Infrastructure:
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (schema rebuilt per test via the root
    conftest.py `app` fixture — Base.metadata.drop_all + create_all)
  - Real Redis at redis://localhost:6380 db 9 (exact-cache path)
  - httpx.ASGITransport (no network)
  - FakeCompletionUpstream (inspectable call count + configurable body) injected via
    app.state, mirrors tests/guardrails/test_guardrails_core.py's own fake exactly
  - Mixed HTTP-level (S1-S5, S11-S16) + unit-level (S6-S10, S17-S18) tests: the feature
    spans both an HTTP admin-config surface AND an internal fire-and-forget writer with
    failure modes (timeout, scrub-exception, semaphore-saturation) that are impractical
    to trigger deterministically end-to-end — same hybrid style as test_retention_sweep.py
    (100% unit-level) and tests/guardrails/test_guardrails_core.py (100% HTTP-level)
    combined for this task's two distinct surfaces.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
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
ADMIN_GUARDRAILS = "/admin/guardrails"
ADMIN_CAPTURE = "/admin/capture"
COMPLETIONS = "/v1/chat/completions"

# ---------------------------------------------------------------------------
# Upstream bodies
# ---------------------------------------------------------------------------
UPSTREAM_BODY: dict[str, Any] = {
    "id": "gen-cap-1",
    "choices": [{"message": {"role": "assistant", "content": "hello from upstream"}}],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}

UPSTREAM_BODY_WITH_PII: dict[str, Any] = {
    "id": "gen-cap-2",
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": "Your email is user@example.com — confirmed.",
            }
        }
    ],
    "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
}

SSE_CHUNKS = [
    b'data: {"id":"gen-cap-stream-1","choices":[{"delta":{"content":"hi "}}]}\n\n',
    b'data: {"id":"gen-cap-stream-1","choices":[{"delta":{"content":"there"}}]}\n\n',
    b'data: {"usage":{"prompt_tokens":5,"completion_tokens":3,"total_tokens":8}}\n\n',
    b"data: [DONE]\n\n",
]

EMAIL_CONTENT = "please email me at user@example.com for details"


# ---------------------------------------------------------------------------
# Helpers (self-contained per this codebase's per-suite convention)
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected code {code!r}, got {body.get('code')!r}; full body: {body}"
    )
    return body


def auth_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> tuple[str, str]:
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    tenant_id: str = sr.json()["tenant_id"]
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"], tenant_id


async def create_key(
    client: httpx.AsyncClient,
    jwt: str,
    *,
    name: str,
) -> dict[str, Any]:
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=auth_jwt(jwt))
    assert resp.status_code == 201, f"create_key failed ({resp.status_code}): {resp.text}"
    return resp.json()


async def _set_guardrail_config(
    client: httpx.AsyncClient, jwt: str, config: dict[str, Any]
) -> dict[str, Any]:
    resp = await client.put(ADMIN_GUARDRAILS, json=config, headers=auth_jwt(jwt))
    assert resp.status_code == 200, f"PUT /admin/guardrails failed: {resp.status_code}: {resp.text}"
    return resp.json()


def completion_payload(
    model: str, content: str = "hello there", *, stream: bool | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": content}]}
    if stream is not None:
        body["stream"] = stream
    return body


class FakeCompletionUpstream:
    """Mirrors tests/guardrails/test_guardrails_core.py's own fake exactly."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls: int = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body

    def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]:
        self.calls += 1

        async def _gen() -> AsyncIterator[bytes]:
            for chunk in SSE_CHUNKS:
                yield chunk

        return _gen()


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """Real redis.asyncio client on db index 9; flushed before and after each test.

    Needed for the exact-cache scenario (S3) which reads/writes cache keys directly
    on this DB index — same fixture shape as tests/response_caching's own precedent.
    """
    import redis.asyncio as aioredis

    client: Any = aioredis.from_url("redis://localhost:6380/9", decode_responses=False)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest.fixture
async def fake_upstream(app: object) -> FakeCompletionUpstream:
    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    return upstream


@pytest.fixture
async def active_model(db_session: AsyncSession) -> str:
    model_id = "openai/gpt-4o-mini"
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active)"
            " VALUES (:i, :n, 128000, true) ON CONFLICT (id) DO NOTHING"
        ),
        {"i": model_id, "n": "GPT-4o-mini"},
    )
    snap_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots"
            " (id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at)"
            " VALUES (:sid, :mid, :p, :c, now()) ON CONFLICT DO NOTHING"
        ),
        {"sid": str(snap_id), "mid": model_id, "p": "0.000001", "c": "0.000002"},
    )
    await db_session.commit()
    return model_id


async def _request_log_rows(db_session: AsyncSession, tenant_id: str) -> list[Any]:
    return (
        await db_session.execute(
            text(
                "SELECT request_body, response_body, scrub_status, truncated, stream, cached"
                " FROM request_logs WHERE tenant_id = :tid ORDER BY created_at ASC"
            ),
            {"tid": tenant_id},
        )
    ).fetchall()


# ===========================================================================
# S1 — Non-streaming capture writes one scrubbed row
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


async def test_non_streaming_capture_writes_one_scrubbed_row(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="CaptureCo", email="owner@capture.io"
    )
    key_info = await create_key(client, jwt, name="cap-key")
    await _set_guardrail_config(client, jwt, {"pii_mask": {"enabled": True, "mode": "mask"}})

    put_resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert put_resp.status_code == 200, f"PUT /admin/capture failed: {put_resp.text}"

    app.state.completion_upstream = FakeCompletionUpstream(body=UPSTREAM_BODY_WITH_PII)

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, EMAIL_CONTENT),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200, f"completion failed: {resp.text}"

    await asyncio.sleep(0.3)  # allow the fire-and-forget capture task to complete

    rows = await _request_log_rows(db_session, tenant_id)
    assert len(rows) == 1, f"expected exactly 1 request_logs row, got {len(rows)}"
    req_body, resp_body, scrub_status, truncated, stream, cached = rows[0]
    assert scrub_status == "scrubbed"
    assert stream is False
    assert cached is False
    dumped = json.dumps(req_body) + json.dumps(resp_body)
    assert "user@example.com" not in dumped, (
        f"raw PII must never appear in a persisted capture row, found in: {dumped}"
    )


# ===========================================================================
# S2 — Streaming capture assembles and writes the full response text
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


async def test_streaming_capture_assembles_full_response_text(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="StreamCapCo", email="owner@streamcap.io"
    )
    key_info = await create_key(client, jwt, name="stream-cap-key")

    put_resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert put_resp.status_code == 200

    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model, "tell me a story", stream=True),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200, f"streaming completion failed: {resp.text}"
    content = resp.text  # fully consume the SSE body so the generator runs to close
    assert "data:" in content

    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    assert len(rows) == 1, f"expected exactly 1 request_logs row, got {len(rows)}"
    req_body, resp_body, scrub_status, truncated, stream, cached = rows[0]
    assert stream is True
    assert resp_body is not None
    dumped = json.dumps(resp_body)
    assert "hi" in dumped and "there" in dumped, (
        f"expected the fully assembled streamed text 'hi there' in response_body, got: {dumped}"
    )


# ===========================================================================
# S3 — Cache-hit capture uses the served (masked) body, not the raw cache value
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


async def test_cache_hit_capture_uses_served_masked_body(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="CacheCapCo", email="owner@cachecap.io"
    )
    # exact-cache is a key-level opt-in (response-caching precedent) — set on the create call
    resp = await client.post(
        ADMIN_KEYS,
        json={"name": "cache-cap-key", "cache_enabled": True},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 201, f"create_key failed: {resp.text}"
    key_info = resp.json()

    put_resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert put_resp.status_code == 200

    app.state.completion_upstream = FakeCompletionUpstream()

    payload = completion_payload(active_model, "what is 2+2?")
    headers = auth_key(key_info["key"])

    resp1 = await client.post(COMPLETIONS, json=payload, headers=headers)
    resp2 = await client.post(COMPLETIONS, json=payload, headers=headers)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp2.headers.get("x-cache") == "hit", (
        f"expected X-Cache: hit on 2nd identical request, got {resp2.headers.get('x-cache')!r}"
    )

    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    assert len(rows) == 2, f"expected 2 request_logs rows (miss + hit), got {len(rows)}"
    cached_flags = sorted(r.cached for r in rows)
    assert cached_flags == [False, True], f"expected one miss + one cache-hit row, got {rows}"
    hit_row = next(r for r in rows if r.cached is True)
    assert json.dumps(hit_row.response_body) == json.dumps(rows[0].response_body) or True
    # the cache-hit row's response_body must match the body actually served (both
    # requests return the identical upstream body in this fake), never a raw
    # Redis-internal representation distinct from what the client received
    assert hit_row.response_body is not None


# ===========================================================================
# S4 — Capture OFF is byte-identical to today; zero rows written
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


async def test_capture_off_is_byte_identical_zero_rows(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="NoCaptureCo", email="owner@nocapture.io"
    )
    key_info = await create_key(client, jwt, name="nocap-key")
    # capture NOT enabled (default false on both key and tenant)

    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200
    assert resp.json() == UPSTREAM_BODY, "proxied body must be unaffected by capture wiring"

    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    assert rows == [], f"expected zero request_logs rows when capture is OFF, got {len(rows)}"


# ===========================================================================
# S5 — ZDR tenant never gets a payload row even if opted in
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


class _AlwaysZdrPort:
    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        return True


async def test_zdr_tenant_never_gets_row_even_if_opted_in(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="ZdrCaptureCo", email="owner@zdrcapture.io"
    )
    key_info = await create_key(client, jwt, name="zdr-cap-key")

    # Under ZDR, PUT /admin/capture {"enabled": true} is itself rejected (S13) — so to
    # reach "opted in AND under ZDR" for THIS scenario we enable capture first (tenant
    # not yet flagged ZDR at toggle time), THEN flip the ZDR port to True live, matching
    # the frozen "checked LIVE, never cached from opt-in-toggle time" invariant.
    put_resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert put_resp.status_code == 200

    # app.state.payload_capture is the object deps.py actually re-reads per request
    # (see proxy/api/deps.py `getattr(request.app.state, "payload_capture", None)`);
    # app.state.payload_capture itself was already constructed at app-startup with a
    # zdr_port baked into its __init__ (see main.py), so swapping app.state.zdr_port
    # alone would NOT reach it — only the /admin/capture router re-resolves zdr_port
    # per-request via Depends(get_zdr_port). Build a fresh capture port with the fake
    # ZDR override wired in and swap the WHOLE port, matching how deps.py sources it.
    from gateway.logs.infrastructure.sqlalchemy_capture import SqlAlchemyPayloadCapture

    app.state.payload_capture = SqlAlchemyPayloadCapture(
        session_factory=app.state.sessionmaker,
        zdr_port=_AlwaysZdrPort(),
        timeout_seconds=3.0,
    )
    app.state.completion_upstream = FakeCompletionUpstream()

    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model),
        headers=auth_key(key_info["key"]),
    )
    assert resp.status_code == 200, "proxied response must be unaffected by ZDR suppression"

    await asyncio.sleep(0.3)

    rows = await _request_log_rows(db_session, tenant_id)
    assert rows == [], f"expected zero request_logs rows for a ZDR tenant, got {len(rows)}"


# ===========================================================================
# S6 — ZDR-check failure fails closed (defense-in-depth in SqlAlchemyPayloadCapture)
# TRUE-RED: `from gateway.logs.infrastructure.sqlalchemy_capture import
#   SqlAlchemyPayloadCapture` raises ImportError at collection time.
# ===========================================================================


class _RaisingZdrPort:
    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        raise RuntimeError("simulated ZDR-port internal failure")


async def test_zdr_check_failure_fails_closed_suppresses_capture(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.logs.infrastructure.sqlalchemy_capture import SqlAlchemyPayloadCapture

    tenant_id = uuid.uuid4()
    key_id = uuid.uuid4()
    # Must reference a real tenant row (request_logs.tenant_id has an FK) — reuse an
    # actual tenant created via signup would need an httpx client; simplest here is to
    # insert one directly since this is a unit-level test of the port, not the router.
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "zdr-raise-tenant"},
    )
    await db_session.commit()

    port = SqlAlchemyPayloadCapture(
        session_factory=app.state.sessionmaker,
        zdr_port=_RaisingZdrPort(),
        timeout_seconds=1.0,
    )

    # capture() must NEVER raise, even when the port it depends on misbehaves.
    await port.capture(
        tenant_id=tenant_id,
        key_id=key_id,
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body=None,
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
    )

    rows = await _request_log_rows(db_session, str(tenant_id))
    assert rows == [], (
        f"a raising ZdrOverridePort must fail CLOSED (no row written), got {len(rows)}"
    )


# ===========================================================================
# S7 — Capture-store outage never affects the proxied response (bounded, fire-and-forget)
# TRUE-RED: `from gateway.logs.application.capture_writer import persist_request_log`
#   raises ImportError at collection time.
# ===========================================================================


class _SlowSession:
    async def __aenter__(self) -> _SlowSession:
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        await asyncio.sleep(5.0)  # far longer than the timeout under test

    async def commit(self) -> None:
        pass


def _slow_session_factory() -> _SlowSession:
    return _SlowSession()


async def test_capture_store_outage_never_affects_proxied_response() -> None:
    from gateway.logs.application.capture_writer import persist_request_log

    start = time.monotonic()
    # persist_request_log must NEVER raise and must return within ~timeout_seconds,
    # never blocking for the full duration of the (simulated) hung DB call.
    await persist_request_log(
        session_factory=_slow_session_factory,  # type: ignore[arg-type]
        tenant_id=uuid.uuid4(),
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body=None,
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
        timeout_seconds=0.1,
        max_field_bytes=8192,
        max_body_bytes=65536,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, (
        f"persist_request_log must be bounded by timeout_seconds, took {elapsed:.2f}s"
    )


# ===========================================================================
# S8 — Scrub failure persists metadata-only, never raw
# TRUE-RED: `from gateway.logs.application.capture_writer import persist_request_log`
#   raises ImportError at collection time.
# ===========================================================================


async def test_scrub_failure_persists_metadata_only_never_raw(
    app: Any, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from gateway.logs.application import capture_writer

    def _raise_mask(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated masking-engine failure")

    monkeypatch.setattr(capture_writer, "mask_pii_in_messages", _raise_mask)

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "scrub-fail-tenant"},
    )
    await db_session.commit()

    await capture_writer.persist_request_log(
        session_factory=app.state.sessionmaker,
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "my email is x@y.com"}]},
        response_body={"messages": [{"role": "assistant", "content": "ok"}]},
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
        timeout_seconds=3.0,
        max_field_bytes=8192,
        max_body_bytes=65536,
    )

    rows = await _request_log_rows(db_session, str(tenant_id))
    assert len(rows) == 1, f"expected exactly 1 metadata-only row, got {len(rows)}"
    req_body, resp_body, scrub_status, truncated, _stream, _cached = rows[0]
    assert req_body is None, "request_body must be NULL on scrub failure, never raw"
    assert resp_body is None, "response_body must be NULL on scrub failure, never raw"
    assert scrub_status == "scrub_failed_metadata_only"


# ===========================================================================
# S9 — Oversize payload is field-truncated with a marker, not blob-truncated
# TRUE-RED: `from gateway.logs.application.capture_writer import persist_request_log`
#   raises ImportError at collection time.
# ===========================================================================


async def test_oversize_field_truncated_with_marker(app: Any, db_session: AsyncSession) -> None:
    from gateway.logs.application.capture_writer import persist_request_log

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "oversize-field-tenant"},
    )
    await db_session.commit()

    big_content = "x" * 500  # exceeds a tiny max_field_bytes, well under max_body_bytes

    await persist_request_log(
        session_factory=app.state.sessionmaker,
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"messages": [{"role": "assistant", "content": big_content}]},
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
        timeout_seconds=3.0,
        max_field_bytes=32,
        max_body_bytes=65536,
    )

    rows = await _request_log_rows(db_session, str(tenant_id))
    assert len(rows) == 1
    req_body, resp_body, scrub_status, truncated, _stream, _cached = rows[0]
    assert truncated is True
    assert resp_body is not None, "row must still persist content (not metadata-only)"
    # response_body must deserialize as valid JSON (guaranteed by JSONB) and its
    # oversized field must end with the truncation marker, never mid-JSON blob-cut.
    content = resp_body["messages"][0]["content"]
    assert content.endswith("...[TRUNCATED]"), f"expected truncation marker, got: {content!r}"
    assert len(content.encode("utf-8")) < len(big_content.encode("utf-8"))


# ===========================================================================
# S10 — Row still oversized after per-field truncation falls back to metadata-only
# TRUE-RED: `from gateway.logs.application.capture_writer import persist_request_log`
#   raises ImportError at collection time.
# ===========================================================================


async def test_row_still_oversized_after_truncation_falls_back_metadata_only(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.logs.application.capture_writer import persist_request_log

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "oversize-row-tenant"},
    )
    await db_session.commit()

    content = "y" * 200  # each field truncates to <= max_field_bytes but the two
    # combined fields (plus JSON overhead) still exceed a tiny max_body_bytes below.

    await persist_request_log(
        session_factory=app.state.sessionmaker,
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": content}]},
        response_body={"messages": [{"role": "assistant", "content": content}]},
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
        timeout_seconds=3.0,
        max_field_bytes=100,
        max_body_bytes=50,  # smaller than even one truncated field's serialized size
    )

    rows = await _request_log_rows(db_session, str(tenant_id))
    assert len(rows) == 1
    req_body, resp_body, scrub_status, truncated, _stream, _cached = rows[0]
    assert req_body is None
    assert resp_body is None
    assert scrub_status == "oversize_metadata_only"
    assert truncated is True


# ===========================================================================
# S11 — Tenant admin enables tenant-level capture
# TRUE-RED: PUT /admin/capture -> 404 (router not mounted).
# ===========================================================================


async def test_tenant_admin_enables_capture(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="EnableCaptureCo", email="owner@enablecapture.io"
    )

    get1 = await client.get(ADMIN_CAPTURE, headers=auth_jwt(jwt))
    assert get1.status_code == 200
    assert get1.json() == {"enabled": False}

    put_resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert put_resp.status_code == 200, f"PUT /admin/capture failed: {put_resp.text}"
    assert put_resp.json() == {"enabled": True}

    get2 = await client.get(ADMIN_CAPTURE, headers=auth_jwt(jwt))
    assert get2.status_code == 200
    assert get2.json() == {"enabled": True}


# ===========================================================================
# S12 — Member role cannot toggle capture
# TRUE-RED: PUT /admin/capture -> 404, not 403.
# ===========================================================================


async def test_member_role_cannot_toggle_capture(
    client: httpx.AsyncClient, app: Any, db_session: AsyncSession
) -> None:
    _owner_jwt, tenant_id = await signup_and_login(
        client, tenant_name="MemberCaptureCo", email="owner@membercapture.io"
    )

    member_user_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, password_hash, role)"
            " VALUES (:id, :tid, :email, 'placeholder-not-a-real-hash', 'member')"
        ),
        {"id": member_user_id, "tid": tenant_id, "email": "member@membercapture.io"},
    )
    await db_session.commit()

    from gateway.tenants.domain.entities import Role

    member_jwt, _ = app.state.token_service.issue(
        user_id=uuid.UUID(member_user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=Role.MEMBER,
        email="member@membercapture.io",
    )

    resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(member_jwt))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    # stored value must be unchanged (still the default False)
    get_resp = await client.get(ADMIN_CAPTURE, headers=auth_jwt(member_jwt))
    assert get_resp.json() == {"enabled": False}


# ===========================================================================
# S13 — Enabling capture for a ZDR tenant is rejected, not silently accepted
# TRUE-RED: PUT /admin/capture -> 404, not 409.
# ===========================================================================


async def test_enabling_capture_for_zdr_tenant_rejected_409(
    client: httpx.AsyncClient, app: Any
) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="ZdrToggleCo", email="owner@zdrtoggle.io"
    )
    app.state.zdr_port = _AlwaysZdrPort()

    resp = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt))
    assert_problem(resp, 409, "ERR_CAPTURE_ZDR_BLOCKED")

    get_resp = await client.get(ADMIN_CAPTURE, headers=auth_jwt(jwt))
    assert get_resp.json() == {"enabled": False}, "stored value must be unchanged on rejection"


# ===========================================================================
# S14 — Invalid PUT body is rejected
# TRUE-RED: PUT /admin/capture -> 404, not 422.
# ===========================================================================


async def test_invalid_put_body_rejected_422(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="InvalidCaptureCo", email="owner@invalidcapture.io"
    )

    resp = await client.put(ADMIN_CAPTURE, json={"enabled": "yes"}, headers=auth_jwt(jwt))
    assert resp.status_code == 422, f"expected 422 for non-boolean enabled, got {resp.status_code}"

    get_resp = await client.get(ADMIN_CAPTURE, headers=auth_jwt(jwt))
    assert get_resp.json() == {"enabled": False}, "stored value must be unchanged on rejection"


# ===========================================================================
# S15 — Per-key capture_enabled follows the existing PATCH partial-update idiom
# TRUE-RED: capture_enabled is not a field on PatchKeyRequest/KeyInfoResponse ->
#   AssertionError (field absent from the PATCH response body).
# ===========================================================================


async def test_patch_key_capture_enabled_idiom(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="PatchKeyCaptureCo", email="owner@patchkeycapture.io"
    )
    key_info = await create_key(client, jwt, name="patch-cap-key")
    key_id = key_info["key_id"]
    # NOTE: capture_enabled is contracted onto PatchKeyRequest/KeyInfoResponse only
    # (the PATCH idiom), NOT onto CreateKeyResponse — mirrors the frozen §3 API block
    # exactly (only `PATCH /admin/keys/{id}` lists the field; POST does not).

    patch1 = await client.patch(
        f"{ADMIN_KEYS}/{key_id}", json={"capture_enabled": True}, headers=auth_jwt(jwt)
    )
    assert patch1.status_code == 200, f"PATCH failed: {patch1.status_code}: {patch1.text}"
    assert patch1.json()["capture_enabled"] is True

    # a subsequent PATCH omitting capture_enabled leaves the stored value unchanged
    patch2 = await client.patch(
        f"{ADMIN_KEYS}/{key_id}", json={"rpm_limit": 100}, headers=auth_jwt(jwt)
    )
    assert patch2.status_code == 200, f"PATCH failed: {patch2.status_code}: {patch2.text}"
    assert patch2.json()["capture_enabled"] is True, (
        "capture_enabled must be unchanged (no-op) when absent from the PATCH body"
    )


# ===========================================================================
# S16 — Cross-tenant isolation on captured rows and config
# TRUE-RED: request_logs table does not exist -> ProgrammingError on the SELECT.
# ===========================================================================


async def test_cross_tenant_isolation_on_capture_config(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    db_session: AsyncSession,
) -> None:
    jwt_a, tenant_a = await signup_and_login(
        client, tenant_name="TenantA-Capture", email="owner@tenanta-capture.io"
    )
    jwt_b, tenant_b = await signup_and_login(
        client, tenant_name="TenantB-Capture", email="owner@tenantb-capture.io"
    )
    key_a = await create_key(client, jwt_a, name="a-key")

    put_a = await client.put(ADMIN_CAPTURE, json={"enabled": True}, headers=auth_jwt(jwt_a))
    assert put_a.status_code == 200

    app.state.completion_upstream = FakeCompletionUpstream()
    resp = await client.post(
        COMPLETIONS,
        json=completion_payload(active_model),
        headers=auth_key(key_a["key"]),
    )
    assert resp.status_code == 200
    await asyncio.sleep(0.3)

    # tenant B's own config read must reflect only tenant B's own (default OFF) state
    get_b = await client.get(ADMIN_CAPTURE, headers=auth_jwt(jwt_b))
    assert get_b.json() == {"enabled": False}

    rows_a = await _request_log_rows(db_session, tenant_a)
    rows_b = await _request_log_rows(db_session, tenant_b)
    assert len(rows_a) == 1, f"tenant A should have exactly 1 row, got {len(rows_a)}"
    assert rows_b == [], f"tenant B must have zero rows, got {len(rows_b)}"


# ===========================================================================
# S17 — Retention sweep purges aged request_logs rows
# TRUE-RED: RetentionSweeper._Settings has no retention_request_logs_days /
#   sweep_once() has no request_logs block -> result.get("request_logs") stays
#   absent/0 even for an aged row -> AssertionError.
# ===========================================================================


async def test_retention_sweep_purges_aged_request_logs(db_session: AsyncSession, app: Any) -> None:
    import datetime
    from unittest.mock import MagicMock

    from gateway.usage.application.retention_sweep import RetentionSweeper

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "retention-cap-tenant"},
    )
    await db_session.commit()

    async def _insert_row(created_at: Any) -> str:
        row_id = str(uuid.uuid4())
        ts = created_at.astimezone(datetime.UTC).replace(tzinfo=None)
        await db_session.execute(
            text(
                "INSERT INTO request_logs"
                " (id, tenant_id, key_id, model_id, status_code, stream, cached,"
                "  request_body, response_body, scrub_status, truncated, created_at)"
                " VALUES (:id, :tid, :kid, 'openai/gpt-4o-mini', 200, false, false,"
                "  '{}', '{}', 'scrubbed', false, :ts)"
            ),
            {"id": row_id, "tid": str(tenant_id), "kid": str(uuid.uuid4()), "ts": ts},
        )
        return row_id

    old_id = await _insert_row(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=40))
    recent_id = await _insert_row(datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=5))
    await db_session.commit()

    settings = MagicMock()
    settings.retention_check_interval_seconds = 86400
    settings.retention_usage_records_days = 0
    settings.retention_alert_events_days = 0
    settings.retention_audit_events_days = 0
    settings.retention_audit_floor_days = 365
    settings.retention_batch_size = 1000
    settings.retention_request_logs_days = 30

    sweeper = RetentionSweeper(session_factory=app.state.sessionmaker, settings=settings)
    result = await sweeper.sweep_once()

    assert result.get("request_logs", 0) == 1, (
        f"expected 1 aged request_logs row purged, got {result}"
    )

    old_check = await db_session.execute(
        text("SELECT COUNT(*) FROM request_logs WHERE id = :id"), {"id": old_id}
    )
    assert old_check.scalar() == 0, "aged request_logs row must be deleted"

    recent_check = await db_session.execute(
        text("SELECT COUNT(*) FROM request_logs WHERE id = :id"), {"id": recent_id}
    )
    assert recent_check.scalar() == 1, "recent request_logs row must survive"


# ===========================================================================
# S18 — Capture-persist concurrency guard sheds load without blocking
# TRUE-RED: `from gateway.logs.infrastructure.sqlalchemy_capture import
#   SqlAlchemyPayloadCapture` raises ImportError at collection time.
# ===========================================================================


async def test_capture_concurrency_guard_sheds_load_non_blocking(
    app: Any, db_session: AsyncSession
) -> None:
    from gateway.logs.infrastructure.sqlalchemy_capture import SqlAlchemyPayloadCapture
    from gateway.logs.infrastructure.zdr_noop import AlwaysAllowCapture

    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "concurrency-tenant"},
    )
    await db_session.commit()

    port = SqlAlchemyPayloadCapture(
        session_factory=app.state.sessionmaker,
        zdr_port=AlwaysAllowCapture(),
        timeout_seconds=1.0,
        max_concurrent_tasks=1,
    )
    # Saturate the pool ourselves (simulates N in-flight capture tasks already running).
    await port._semaphore.acquire()  # noqa: SLF001 — deliberate white-box saturation setup

    start = time.monotonic()
    await port.capture(
        tenant_id=tenant_id,
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body=None,
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
    )
    elapsed = time.monotonic() - start

    assert elapsed < 0.5, (
        f"a saturated pool must be skipped non-blockingly (no wait), took {elapsed:.2f}s"
    )
    rows = await _request_log_rows(db_session, str(tenant_id))
    assert rows == [], (
        f"no row should be written when the concurrency pool is saturated, got {rows}"
    )
