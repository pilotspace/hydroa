"""Red/Green test suite for batch-auto-grouping (TASK.md §3, FROZEN @ v1).

Contract under test:
  POST /v1/chat/completions — policy disabled (default) or streaming or a request that
    fails existing validation -> unchanged, byte-identical to today (M2/M3/M9/R1).
  POST /v1/chat/completions — policy enabled, eligible, AND the batch pathway is
    actually available right now (processor configured + hand-off succeeds) -> 200
    { object: "chat.completion.batch_reference", batch_job_id, custom_id, poll_url },
    NOT a ChatCompletion body (M4 happy branch, M5, M6). Otherwise (no processor
    configured OR the hand-off fails) -> falls back to the existing sync path silently,
    never a 5xx (M4 safety branch, R4). Which branch applies is NOT predictable
    per-request ahead of time for an opted-in tenant (M11) — callers must branch on
    `object`.
  GET /v1/batches/{job_id} — ADDITIVE extension: existing fields unchanged, + a new
    `items` array exposing BatchJobItemRow.result_body (M7).
  GET/PUT /admin/batch-policy — session-JWT auth, same shape as /admin/cache: any role
    may GET; only owner/admin may PUT (member -> 403 ERR_AUTH_FORBIDDEN) (M1, R2).

TENANT ISOLATION: a diverted job/item is scoped to the diverting tenant only; another
tenant polling it gets 404, never a data leak (M10, R3).

None of this is implemented yet — every test below is expected to fail red, either at
its own primary assertion (diversion / extension behavior does not exist) or earlier,
at policy-setup (the /admin/batch-policy endpoint itself does not exist yet — a 404
there is itself evidence of the missing implementation, not a broken harness).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.batches.infrastructure.orm import BatchJobItemRow, BatchJobRow
from gateway.batches.infrastructure.repository import BatchJobRepository

pytestmark = pytest.mark.asyncio

COMPLETIONS = "/v1/chat/completions"
POLICY = "/admin/batch-policy"

# NOTE: the gateway passes non-streaming bodies through VERBATIM — it does not
# guarantee an "object" field on a real (non-diverted) response. This fixture body
# includes one purely because this fake upstream chooses to; a diverted-request
# detector must key off the response content-type (text/event-stream vs
# application/json), never off the presence/absence of "chat.completion" here
# (see TestDualShapeContract below).
UPSTREAM_BODY = {
    "id": "gen-123",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
}

SSE_CHUNKS = [
    b'data: {"id":"gen-123","choices":[{"delta":{"content":"hi"}}]}\n\n',
    b"data: [DONE]\n\n",
]


class FakeCompletionUpstream:
    """Same double as test_proxy_completions.py — counting .calls proves a diverted
    request never reaches the upstream at all (it's queued, not forwarded)."""

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


class _FakeNoopProcessor:
    """Stands in for an as-yet-unbuilt openai/anthropic batch adapter: proves a
    processor via app.state.batch_processor is not None, but never resolves the job —
    it stays non-terminal, exactly what test_batch_jobs.py's own fakes model."""

    async def process(self, job_id: uuid.UUID) -> None:
        return None


class _FakeSucceedingProcessor:
    """Drives the single diverted item to status=succeeded with a result_body — the
    shape a real adapter is documented to own (router.py: 'A configured BatchProcessor
    is responsible for driving the job to its own terminal status')."""

    def __init__(self, sessionmaker: Any, result_body: dict[str, Any]) -> None:
        self._sessionmaker = sessionmaker
        self._result_body = result_body

    async def process(self, job_id: uuid.UUID) -> None:
        async with self._sessionmaker() as session:
            await session.execute(
                update(BatchJobItemRow)
                .where(BatchJobItemRow.batch_job_id == job_id)
                .values(status="succeeded", result_body=self._result_body)
            )
            await session.execute(
                update(BatchJobRow).where(BatchJobRow.id == job_id).values(status="completed")
            )
            await session.commit()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def payload(model: str, stream: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
    if stream is not None:
        body["stream"] = stream
    return body


def install_upstream(app: Any, upstream: FakeCompletionUpstream) -> None:
    app.state.completion_upstream = upstream


async def _await_all_batch_tasks(app: Any) -> None:
    """Same helper as test_batch_stats.py / test_batch_jobs.py."""
    tasks: set[asyncio.Task[None]] = getattr(app.state, "batch_jobs_tasks", set())
    if tasks:
        pending = set(tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a tenant, log in, create an API key. Returns {tenant_id, jwt, key} —
    jwt for /admin/batch-policy (session auth), key for /v1/chat/completions and
    /v1/batches (API-key auth) — same dual-auth need as test_batch_stats.py."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    login = await client.post("/admin/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, f"login failed: {login.text}"
    jwt = login.json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers=_bearer(jwt),
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {"tenant_id": tenant_id, "jwt": jwt, "key": created.json()["key"]}


def _issue_role_token(app: Any, *, tenant_id: str, role_str: str) -> str:
    """Same live token_service pattern as test_batch_stats.py — pinned to a real
    tenant_id so the caller is scoped to the same tenant under test."""
    from gateway.tenants.domain.entities import Role

    role = Role(role_str)
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        email=f"{role_str}@batch-auto-grouping-test.local",
    )
    return token


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await _signup_owner(
        client,
        tenant_name="BatchAutoGroupTest",
        email="batch-auto-group@example.io",
        password="batch-auto-group-battery",
    )


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


async def _enable_policy(client: httpx.AsyncClient, jwt: str) -> None:
    """Setup helper used by every test that needs policy=enabled. Asserts success so a
    missing /admin/batch-policy implementation fails LOUD right here, not silently
    (which would otherwise let a policy-enabled test pass vacuously against
    still-disabled default behavior)."""
    resp = await client.put(POLICY, json={"enabled": True}, headers=_bearer(jwt))
    assert resp.status_code == 200, f"enabling the policy failed: {resp.status_code} {resp.text}"
    assert resp.json()["enabled"] is True


async def _batch_jobs_count(db_session: AsyncSession, tenant_id: str) -> int:
    result = await db_session.execute(
        text("SELECT count(*) FROM batch_jobs WHERE tenant_id = :tid"),
        {"tid": tenant_id},
    )
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Scenario: Disabled tenant sees unchanged behavior (M9)
# ---------------------------------------------------------------------------


class TestDisabledTenantUnchanged:
    async def test_disabled_tenant_sees_unchanged_behavior(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        # Confirms the default is false via the NEW endpoint itself — folds M1's
        # default-value guarantee in, and keeps this test genuinely red pre-build
        # (GET /admin/batch-policy 404s today; there is no implementation to be
        # vacuously correct against).
        default = await client.get(POLICY, headers=_bearer(owner["jwt"]))
        assert default.status_code == 200, (
            f"expected 200, got {default.status_code}: {default.text}"
        )
        assert default.json()["enabled"] is False

        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)

        resp = await client.post(
            COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
        )

        assert resp.status_code == 200
        assert resp.json() == UPSTREAM_BODY
        assert upstream.calls == 1
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario: Owner can view and change the policy (M1) · Non-owner cannot (R2)
# ---------------------------------------------------------------------------


class TestPolicyToggle:
    async def test_owner_can_view_and_change_policy(
        self, client: httpx.AsyncClient, owner: dict[str, str]
    ) -> None:
        before = await client.get(POLICY, headers=_bearer(owner["jwt"]))
        assert before.status_code == 200
        assert before.json() == {"enabled": False}

        put = await client.put(POLICY, json={"enabled": True}, headers=_bearer(owner["jwt"]))
        assert put.status_code == 200
        assert put.json() == {"enabled": True}

        after = await client.get(POLICY, headers=_bearer(owner["jwt"]))
        assert after.status_code == 200
        assert after.json() == {"enabled": True}

    async def test_non_owner_cannot_change_policy(
        self, app: Any, client: httpx.AsyncClient, owner: dict[str, str]
    ) -> None:
        member_token = _issue_role_token(app, tenant_id=owner["tenant_id"], role_str="member")

        resp = await client.put(POLICY, json={"enabled": True}, headers=_bearer(member_token))

        assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "ERR_AUTH_FORBIDDEN"

        unchanged = await client.get(POLICY, headers=_bearer(owner["jwt"]))
        assert unchanged.json()["enabled"] is False


# ---------------------------------------------------------------------------
# Scenario: Streaming bypasses diversion even when enabled (M2)
# ---------------------------------------------------------------------------


class TestStreamingNeverDiverted:
    async def test_streaming_bypasses_diversion_even_when_enabled(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)

        resp = await client.post(
            COMPLETIONS, json=payload(active_model, stream=True), headers=_bearer(owner["key"])
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario: Invalid body fails identically regardless of policy (M3, R1)
# ---------------------------------------------------------------------------


class TestValidationUnaffectedByPolicy:
    async def test_invalid_body_fails_identically_regardless_of_policy(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)
        bad_body = {"model": active_model, "messages": []}

        disabled_resp = await client.post(COMPLETIONS, json=bad_body, headers=_bearer(owner["key"]))
        assert disabled_resp.status_code == 422
        assert disabled_resp.json()["code"] == "ERR_PAYLOAD_INVALID"

        await _enable_policy(client, owner["jwt"])
        enabled_resp = await client.post(COMPLETIONS, json=bad_body, headers=_bearer(owner["key"]))

        assert enabled_resp.status_code == disabled_resp.status_code == 422
        assert enabled_resp.json()["code"] == "ERR_PAYLOAD_INVALID"
        assert upstream.calls == 0
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario: Eligible request diverted when the batch pathway is available
#           (M4 happy branch, M5, M6)
# ---------------------------------------------------------------------------


class TestDivertedWhenAvailable:
    async def test_eligible_request_diverted_when_pathway_available(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        # batch-window-grouping TASK.md SCOPE AMENDMENT (Tin, 2026-07-03): this test
        # asserted the M6 flat batch_reference envelope, which G8 supersedes for the
        # genuinely-accumulated case — updated to assert the new SSE contract instead.
        # Every other test in this file is untouched.
        import json

        from gateway.batches.application.window_flusher import BatchWindowFlusher
        from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

        await _enable_policy(client, owner["jwt"])
        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05
        try:
            resp_task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
            flusher = BatchWindowFlusher(
                buffer=buffer,
                sessionmaker=app.state.sessionmaker,
                app_state=app.state,
                settings=app.state.settings,
                get_batch_processor=lambda: app.state.batch_processor,
            )
            await flusher.flush_once()
            resp = await resp_task
            await _await_all_batch_tasks(app)
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        assert resp.headers["content-type"].startswith("text/event-stream"), (
            "G8 supersedes the M6 flat batch_reference envelope — a genuinely-"
            "accumulated request is now always SSE, never a plain JSON envelope"
        )

        events: list[tuple[str, dict[str, Any]]] = []
        for block in resp.text.strip("\n").split("\n\n"):
            if not block.strip():
                continue
            name: str | None = None
            data: dict[str, Any] | None = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name is not None and data is not None:
                events.append((name, data))

        assert [n for n, _ in events] == ["queued", "submitted"], events
        submitted = dict(events)["submitted"]
        assert "batch_job_id" in submitted
        assert "custom_id" in submitted
        assert "poll_url" in submitted
        assert upstream.calls == 0, "a diverted request must never reach the upstream provider"

        result = await db_session.execute(
            text("SELECT count(*) FROM batch_jobs WHERE tenant_id = :tid"),
            {"tid": owner["tenant_id"]},
        )
        assert int(result.scalar_one()) == 1
        items = await db_session.execute(
            text("SELECT request_body FROM batch_job_items WHERE batch_job_id = :jid"),
            {"jid": submitted["batch_job_id"]},
        )
        rows = items.fetchall()
        assert len(rows) == 1
        assert rows[0][0]["messages"][0]["content"] == "hello"


# ---------------------------------------------------------------------------
# Scenario: No processor configured falls back to sync (M4 safety branch)
# ---------------------------------------------------------------------------


class TestFallsBackWhenUnavailable:
    async def test_no_processor_configured_falls_back_to_sync(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)
        assert getattr(app.state, "batch_processor", None) is None, (
            "precondition: no processor configured"
        )

        resp = await client.post(
            COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
        )

        assert resp.status_code == 200
        assert resp.json() == UPSTREAM_BODY, "must behave exactly as if the policy were disabled"
        assert upstream.calls == 1
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0

    async def test_batch_handoff_failure_falls_back_to_sync(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # batch-window-grouping TASK.md SCOPE AMENDMENT #2 (AI-approved during build,
        # 2026-07-03): identical root cause to the 5 tests amendment #1 already
        # covers — this one also asserted the retired flat batch_reference envelope,
        # this time for a request that resolves via the bounded-fallback path (never
        # claimed by a flusher tick) rather than the happy path. G8's SSE framing
        # supersedes the old contract for every diverted request, fallback-resolved
        # or not — updated to assert the queued/completion SSE events instead.
        import json

        await _enable_policy(client, owner["jwt"])
        upstream = FakeCompletionUpstream()
        install_upstream(app, upstream)
        app.state.batch_processor = _FakeNoopProcessor()

        async def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("db write failed")

        monkeypatch.setattr(BatchJobRepository, "create", _raise)

        try:
            resp = await client.post(
                COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
            )
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200, (
            f"a hand-off failure must never surface as a 5xx: {resp.status_code} {resp.text}"
        )
        assert resp.headers["content-type"].startswith("text/event-stream"), (
            "G8 supersedes the M6 flat batch_reference envelope — a hand-off failure "
            "still resolves via the bounded-fallback SSE path, never a plain JSON body"
        )

        events: list[tuple[str, dict[str, Any]]] = []
        for block in resp.text.strip("\n").split("\n\n"):
            if not block.strip():
                continue
            name: str | None = None
            data: dict[str, Any] | None = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name is not None and data is not None:
                events.append((name, data))

        assert [n for n, _ in events] == ["queued", "completion"], events
        assert dict(events)["completion"] == UPSTREAM_BODY
        assert upstream.calls == 1
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# Scenario: Diverted result is retrievable once resolved (M7)
# ---------------------------------------------------------------------------


class TestResultRetrieval:
    async def test_diverted_result_retrievable_once_resolved(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        # batch-window-grouping SCOPE AMENDMENT (Tin, 2026-07-03): job_id/custom_id are
        # now read off the SSE "submitted" event instead of a flat JSON envelope (G8).
        import json

        from gateway.batches.application.window_flusher import BatchWindowFlusher
        from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        result_body = {
            "id": "gen-999",
            "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": "the real answer"}}],
        }
        app.state.batch_processor = _FakeSucceedingProcessor(app.state.sessionmaker, result_body)
        app.state.settings.batch_window_seconds = 0.05
        try:
            divert_task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
            flusher = BatchWindowFlusher(
                buffer=buffer,
                sessionmaker=app.state.sessionmaker,
                app_state=app.state,
                settings=app.state.settings,
                get_batch_processor=lambda: app.state.batch_processor,
            )
            await flusher.flush_once()
            divert_resp = await divert_task
            await _await_all_batch_tasks(app)
        finally:
            app.state.batch_processor = None

        events: list[tuple[str, dict[str, Any]]] = []
        for block in divert_resp.text.strip("\n").split("\n\n"):
            if not block.strip():
                continue
            name: str | None = None
            data: dict[str, Any] | None = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name is not None and data is not None:
                events.append((name, data))

        submitted = dict(events)["submitted"]
        job_id = submitted["batch_job_id"]
        custom_id = submitted["custom_id"]

        poll = await client.get(f"/v1/batches/{job_id}", headers=_bearer(owner["key"]))

        assert poll.status_code == 200
        items = poll.json()["items"]
        matching = [i for i in items if i["custom_id"] == custom_id]
        assert len(matching) == 1, f"expected exactly one item for {custom_id}: {items}"
        assert matching[0]["status"] == "succeeded"
        assert matching[0]["result_body"] == result_body


# ---------------------------------------------------------------------------
# Scenario: Disabling the policy does not affect an in-flight job (M8)
# ---------------------------------------------------------------------------


class TestDisablingDoesNotAffectInFlight:
    async def test_disabling_policy_does_not_affect_inflight_job(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        # batch-window-grouping SCOPE AMENDMENT (Tin, 2026-07-03): job_id is now read
        # off the SSE "submitted" event instead of a flat JSON envelope (G8).
        import json

        from gateway.batches.application.window_flusher import BatchWindowFlusher
        from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05
        try:
            divert_task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
            flusher = BatchWindowFlusher(
                buffer=buffer,
                sessionmaker=app.state.sessionmaker,
                app_state=app.state,
                settings=app.state.settings,
                get_batch_processor=lambda: app.state.batch_processor,
            )
            await flusher.flush_once()
            divert_resp = await divert_task
            await _await_all_batch_tasks(app)
        finally:
            app.state.batch_processor = None

        events: list[tuple[str, dict[str, Any]]] = []
        for block in divert_resp.text.strip("\n").split("\n\n"):
            if not block.strip():
                continue
            name: str | None = None
            data: dict[str, Any] | None = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name is not None and data is not None:
                events.append((name, data))
        job_id = dict(events)["submitted"]["batch_job_id"]

        before_disable = await client.get(f"/v1/batches/{job_id}", headers=_bearer(owner["key"]))
        assert before_disable.status_code == 200
        status_before = before_disable.json()["status"]

        disable = await client.put(POLICY, json={"enabled": False}, headers=_bearer(owner["jwt"]))
        assert disable.status_code == 200

        after_disable = await client.get(f"/v1/batches/{job_id}", headers=_bearer(owner["key"]))
        assert after_disable.status_code == 200
        assert after_disable.json()["status"] == status_before, (
            "disabling the policy must not touch an existing job"
        )

        new_call = await client.post(
            COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
        )
        assert new_call.status_code == 200
        assert new_call.json() == UPSTREAM_BODY, (
            "a request made after disabling must no longer be diverted"
        )


# ---------------------------------------------------------------------------
# Scenario: Diverted request still honors tenant/key scoping (M10, R3)
# ---------------------------------------------------------------------------


class TestTenantScoping:
    async def test_diverted_request_honors_tenant_scoping(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        # batch-window-grouping SCOPE AMENDMENT (Tin, 2026-07-03): job_id is now read
        # off the SSE "submitted" event instead of a flat JSON envelope (G8).
        import json

        from gateway.batches.application.window_flusher import BatchWindowFlusher
        from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

        other = await _signup_owner(
            client,
            tenant_name="BatchAutoGroupOther",
            email="batch-auto-group-other@example.io",
            password="batch-auto-group-other-battery",
        )

        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05
        try:
            divert_task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
            flusher = BatchWindowFlusher(
                buffer=buffer,
                sessionmaker=app.state.sessionmaker,
                app_state=app.state,
                settings=app.state.settings,
                get_batch_processor=lambda: app.state.batch_processor,
            )
            await flusher.flush_once()
            divert_resp = await divert_task
            await _await_all_batch_tasks(app)
        finally:
            app.state.batch_processor = None

        events: list[tuple[str, dict[str, Any]]] = []
        for block in divert_resp.text.strip("\n").split("\n\n"):
            if not block.strip():
                continue
            name: str | None = None
            data: dict[str, Any] | None = None
            for line in block.split("\n"):
                if line.startswith("event:"):
                    name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data = json.loads(line[len("data:") :].strip())
            if name is not None and data is not None:
                events.append((name, data))
        job_id = dict(events)["submitted"]["batch_job_id"]

        cross_tenant = await client.get(f"/v1/batches/{job_id}", headers=_bearer(other["key"]))
        assert cross_tenant.status_code == 404
        assert cross_tenant.json()["code"] == "ERR_BATCH_JOB_NOT_FOUND"

        own = await client.get(f"/v1/batches/{job_id}", headers=_bearer(owner["key"]))
        assert own.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Opted-in tenant must branch on response shape (M11)
# ---------------------------------------------------------------------------


class TestDualShapeContract:
    async def test_opted_in_tenant_response_shape_varies_by_availability(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        # batch-window-grouping SCOPE AMENDMENT (Tin, 2026-07-03): the sentinel a
        # caller must key off is now the top-level Content-Type (text/event-stream vs
        # application/json) — G8 retires the batch_reference-object sentinel entirely
        # for the genuinely-accumulated case (dead code, not an alternate branch).
        from gateway.batches.application.window_flusher import BatchWindowFlusher
        from gateway.proxy.infrastructure.batch_window_buffer import BatchWindowBuffer

        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())

        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05
        try:
            diverted_task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
            flusher = BatchWindowFlusher(
                buffer=buffer,
                sessionmaker=app.state.sessionmaker,
                app_state=app.state,
                settings=app.state.settings,
                get_batch_processor=lambda: app.state.batch_processor,
            )
            await flusher.flush_once()
            diverted = await diverted_task
            await _await_all_batch_tasks(app)
        finally:
            app.state.batch_processor = None

        not_diverted = await client.post(
            COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
        )

        # The sentinel is what a caller must key off — never the presence/absence of
        # "chat.completion" (the gateway passes non-diverted bodies through verbatim
        # and does not itself guarantee that field; see the UPSTREAM_BODY note above).
        assert diverted.headers["content-type"].startswith("text/event-stream")
        assert not_diverted.headers["content-type"].startswith("application/json")
        assert not_diverted.json() == UPSTREAM_BODY
