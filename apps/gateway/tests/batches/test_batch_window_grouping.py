"""Red/Green test suite for batch-window-grouping (TASK.md, FROZEN @ v1).

Extends batch-auto-grouping (TASK.md, gate=PASS, commit c6349b5): replaces the old
size-1-job-per-request diversion with fixed-tick-from-first-arrival accumulation
across a tenant's whole window, flushed as ONE multi-item batch job.

Contract under test (see .add/tasks/batch-window-grouping/TASK.md §1-§3):
  - G3: fixed-tick window — a second arrival never resets an already-open window.
  - G4/G6/G7: the window flushes as one multi-item job; claim_due is atomic across
    N concurrent gateway replicas (Lua EVAL) — exactly one claims a given window.
  - G8/G9: an accumulated request's HTTP response is ALWAYS 200 text/event-stream,
    returned immediately — "event: queued" then either "event: submitted" (poll the
    real result via the existing GET /v1/batches/{id}, never pushed down the SSE
    connection) or "event: completion" (G10 per-item sync fallback, verbatim body).
  - G10: flush failure OR an elapsed window with no recorded flush attempt both fall
    back to the existing synchronous upstream call per item, delivered as the
    stream's terminal event — never a 5xx.
  - G11/G12: disabling the policy never disturbs an already-open/flushed window; a
    disabled tenant sees zero added code path (mirrors batch-auto-grouping M8/M9).
  - R1/R5/R6: validation is unaffected by policy; an unreachable buffer falls back
    exactly like a disabled policy; a late arrival after a claim opens a fresh
    window rather than being lost or merged into the already-claimed one.

None of this is implemented yet — every test below is expected to fail red at
import time (BatchWindowBuffer / BatchWindowFlusher do not exist yet), not at a
later assertion.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import gateway.proxy.infrastructure.batch_diversion as batch_diversion_module
from gateway.batches.application.window_flusher import BatchWindowFlusher
from gateway.batches.infrastructure.repository import BatchJobRepository
from gateway.proxy.infrastructure.batch_diversion import BatchDiversionAdapter
from gateway.proxy.infrastructure.batch_window_buffer import (
    _RESULT_KEY_PREFIX,
    BatchWindowBuffer,
)

pytestmark = pytest.mark.asyncio

COMPLETIONS = "/v1/chat/completions"
POLICY = "/admin/batch-policy"

UPSTREAM_BODY = {
    "id": "gen-123",
    "object": "chat.completion",
    "choices": [{"message": {"role": "assistant", "content": "hi"}}],
    "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
}


class FakeCompletionUpstream:
    """Same double as test_batch_auto_grouping.py — counting .calls proves a
    genuinely-accumulated request never reaches the upstream synchronously."""

    def __init__(self, status: int = 200, body: dict[str, Any] | None = None) -> None:
        self.status = status
        self.body = body if body is not None else UPSTREAM_BODY
        self.calls = 0

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        return self.status, self.body


class _FakeNoopProcessor:
    """Proves a processor via app.state.batch_processor is not None (the M4/build-note
    safety gate this task restores) without ever resolving the job itself — the row
    stays non-terminal, matching test_batch_auto_grouping.py's own fake."""

    async def process(self, job_id: uuid.UUID) -> None:
        return None


class _FakeClock:
    """Injectable, manually-advanced clock for deterministic window-mechanics tests —
    avoids any real sleeping for the pure BatchWindowBuffer tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def __call__(self) -> float:
        return self._t

    def advance(self, delta: float) -> None:
        self._t += delta


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def payload(model: str, stream: bool | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"model": model, "messages": [{"role": "user", "content": "hello"}]}
    if stream is not None:
        body["stream"] = stream
    return body


def install_upstream(app: Any, upstream: FakeCompletionUpstream) -> None:
    app.state.completion_upstream = upstream


def _parse_sse(raw_text: str) -> list[tuple[str, dict[str, Any]]]:
    """Split a fully-drained SSE body into (event_name, json_data) tuples.

    httpx's AsyncClient.post() against ASGITransport fully drains a StreamingResponse
    before returning — resp.text is therefore the WHOLE stream, concatenated. Each
    event this codebase emits is a single "event: X\\ndata: <json>\\n\\n" block (see
    BatchDiversionAdapter._sse_event) — no embedded newlines in the JSON payload.
    """
    import json as _json

    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw_text.strip("\n").split("\n\n"):
        if not block.strip():
            continue
        event_name: str | None = None
        data_raw: str | None = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_raw = line[len("data:") :].strip()
        if event_name is not None and data_raw is not None:
            events.append((event_name, _json.loads(data_raw)))
    return events


def _event_data(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    for event_name, data in events:
        if event_name == name:
            return data
    raise AssertionError(f"no {name!r} event found in {events}")


def _make_flusher(app: Any) -> BatchWindowFlusher:
    """Standalone flusher pointed at the SAME real Redis + sessionmaker the app uses.

    Mirrors how BatchJobWorker/RetentionSweeper are tested: httpx's ASGITransport
    (used by the `client` fixture) never fires FastAPI's lifespan, so the real
    background BatchWindowFlusher.run_forever() task never runs under test — tests
    drive exactly one flush cycle directly via flush_once() instead (the TEST SEAM,
    same shape as BatchJobWorker.process_once() / RetentionSweeper.sweep_once()).
    """
    buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
    return BatchWindowFlusher(
        buffer=buffer,
        sessionmaker=app.state.sessionmaker,
        app_state=app.state,
        settings=app.state.settings,
        get_batch_processor=lambda: app.state.batch_processor,
    )


async def _signup_owner(
    client: httpx.AsyncClient, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
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
        "/admin/keys", json={"name": f"ci-key-{tenant_name}"}, headers=_bearer(jwt)
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"

    return {"tenant_id": tenant_id, "jwt": jwt, "key": created.json()["key"]}


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    return await _signup_owner(
        client,
        tenant_name="BatchWindowGroupTest",
        email="batch-window-group@example.io",
        password="batch-window-group-battery",
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
# Pure BatchWindowBuffer mechanics — real Redis (db 9), fake injectable clock,
# no HTTP layer needed. Each test uses a fresh synthetic tenant_id.
# ---------------------------------------------------------------------------


class TestFirstRequestOpensWindow:
    async def test_first_request_opens_window(self, app: Any) -> None:
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 3.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(tenant_id=tenant_id, custom_id="c1", key_id=uuid.uuid4(), body={"a": 1})

        clock.advance(1.0)
        assert await buffer.claim_due(tenant_id) is None, "must not be due before window elapses"

        clock.advance(2.5)  # total elapsed 3.5s > 3.0s window
        claimed = await buffer.claim_due(tenant_id)
        assert claimed is not None
        assert len(claimed) == 1
        assert claimed[0]["custom_id"] == "c1"


class TestSecondArrivalDoesNotResetWindow:
    async def test_second_arrival_does_not_reset_window(self, app: Any) -> None:
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 3.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(tenant_id=tenant_id, custom_id="c1", key_id=uuid.uuid4(), body={})
        clock.advance(1.5)
        await buffer.append(tenant_id=tenant_id, custom_id="c2", key_id=uuid.uuid4(), body={})

        clock.advance(1.4)  # elapsed from FIRST arrival = 2.9s — not yet due
        assert await buffer.claim_due(tenant_id) is None

        clock.advance(0.2)  # elapsed = 3.1s >= 3.0s (fixed-tick from t=0, NOT reset to t=1.5)
        claimed = await buffer.claim_due(tenant_id)
        assert claimed is not None
        assert {c["custom_id"] for c in claimed} == {"c1", "c2"}


class TestEarlyFlushOnSizeCap:
    async def test_early_flush_on_size_cap(self, app: Any) -> None:
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 300.0  # long enough that time is not the trigger
        settings.batch_max_items_per_job = 2
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(tenant_id=tenant_id, custom_id="c1", key_id=uuid.uuid4(), body={})
        clock.advance(0.01)
        assert await buffer.claim_due(tenant_id) is None, "one item must not trip a cap of 2"

        await buffer.append(tenant_id=tenant_id, custom_id="c2", key_id=uuid.uuid4(), body={})
        claimed = await buffer.claim_due(tenant_id)
        assert claimed is not None, "size cap must flush well before the 300s window"
        assert {c["custom_id"] for c in claimed} == {"c1", "c2"}


class TestLateArrivalJoinsNextWindow:
    async def test_late_arrival_joins_next_window_not_claimed_one(self, app: Any) -> None:
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 3.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(tenant_id=tenant_id, custom_id="c1", key_id=uuid.uuid4(), body={})
        clock.advance(3.1)
        first_claim = await buffer.claim_due(tenant_id)
        assert first_claim is not None
        assert {c["custom_id"] for c in first_claim} == {"c1"}

        # A late arrival AFTER the first window was already claimed must open a fresh
        # window — never silently merged into, or lost alongside, the claimed one (R6).
        await buffer.append(tenant_id=tenant_id, custom_id="c2", key_id=uuid.uuid4(), body={})
        assert await buffer.claim_due(tenant_id) is None, "the new window must not be due yet"

        clock.advance(3.1)
        second_claim = await buffer.claim_due(tenant_id)
        assert second_claim is not None
        assert {c["custom_id"] for c in second_claim} == {"c2"}


class TestAtomicClaim:
    async def test_multi_replica_atomic_claim(self, app: Any) -> None:
        """G6: simulate two gateway replicas racing claim_due() for the same due
        window via a genuine asyncio.gather race against the SAME real Redis —
        exactly one must win (non-None/non-empty), the other must see nothing left
        to claim. A GET-then-DELETE would let both win; this must not."""
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 1.0
        settings.batch_max_items_per_job = 500
        buffer_replica_a = BatchWindowBuffer(
            redis=app.state.redis_client, settings=settings, _now=clock
        )
        buffer_replica_b = BatchWindowBuffer(
            redis=app.state.redis_client, settings=settings, _now=clock
        )

        await buffer_replica_a.append(
            tenant_id=tenant_id, custom_id="race-1", key_id=uuid.uuid4(), body={}
        )
        clock.advance(2.0)  # well past the 1.0s window

        results = await asyncio.gather(
            buffer_replica_a.claim_due(tenant_id),
            buffer_replica_b.claim_due(tenant_id),
        )

        winners = [r for r in results if r]
        assert len(winners) == 1, f"expected exactly one winner, got: {results}"
        assert winners[0][0]["custom_id"] == "race-1"


class TestAbandonBeforeClaim:
    async def test_abandoned_item_excluded_from_subsequent_claim(self, app: Any) -> None:
        """Double-processing fix: an item whose caller gave up (short-wait timed out,
        try_abandon won the race) must never be included in a later claim_due — even
        though it is still sitting in the tenant's items list until the window
        drains. Mixed with a second, never-abandoned item to prove ONLY the abandoned
        one is excluded, not the whole window."""
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 3.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(
            tenant_id=tenant_id, custom_id="abandoned-1", key_id=uuid.uuid4(), body={}
        )
        await buffer.append(tenant_id=tenant_id, custom_id="kept-1", key_id=uuid.uuid4(), body={})

        assert await buffer.try_abandon("abandoned-1") is True

        clock.advance(3.1)
        claimed = await buffer.claim_due(tenant_id)
        assert claimed is not None
        assert {c["custom_id"] for c in claimed} == {"kept-1"}, (
            "the abandoned item must be silently excluded from the claimed batch, "
            "not double-processed alongside the survivor"
        )


class TestClaimBeforeAbandon:
    async def test_try_abandon_refuses_once_claimed(self, app: Any) -> None:
        """The inverse race: claim_due wins first. A subsequent try_abandon call (the
        SSE generator's own timeout landing a hair after the claim) must refuse —
        never overwrite a live "claimed" marker with "abandoned", which would let
        BatchWindowFlusher's real, in-flight job publish a result nobody is listening
        for while the caller ALSO ran a duplicate sync fallback."""
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 1.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(
            tenant_id=tenant_id, custom_id="claimed-1", key_id=uuid.uuid4(), body={}
        )
        clock.advance(1.1)

        claimed = await buffer.claim_due(tenant_id)
        assert claimed is not None
        assert {c["custom_id"] for c in claimed} == {"claimed-1"}

        assert await buffer.try_abandon("claimed-1") is False, (
            "must refuse once claim_due has already claimed this custom_id"
        )


class TestLifecycleReWaitsWhenClaimWinsRace:
    async def test_lifecycle_rewaits_and_resolves_submitted_when_claim_beats_timeout(
        self, app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_lifecycle's own wait_for_result call can time out (None) in the exact
        instant a concurrent claim_due() writes "claimed" (buffer-level proof:
        TestClaimBeforeAbandon). This proves the CALLER honors that: it must re-wait
        rather than fall back, and resolve via the normal "submitted" event once the
        real terminal marker arrives — sync_fallback must never be invoked."""
        tenant_id = uuid.uuid4()
        key_id = uuid.uuid4()
        settings = app.state.settings
        settings.batch_window_seconds = 60.0  # never the trigger by elapsed time
        settings.batch_max_items_per_job = 1  # the single append alone makes it due
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings)
        adapter = BatchDiversionAdapter(settings=settings, buffer=buffer)

        real_wait_for_result = BatchWindowBuffer.wait_for_result
        call_count = 0
        captured_custom_id: str | None = None

        async def _fake_wait(
            self: BatchWindowBuffer,
            custom_id: str,
            *,
            short_max_wait: float,
            long_max_wait: float,
            **kwargs: Any,
        ) -> dict[str, Any] | None:
            nonlocal call_count, captured_custom_id
            call_count += 1
            captured_custom_id = custom_id
            if call_count == 1:
                # Simulate the exact-boundary race deterministically (no real wall-
                # clock race needed): a concurrent claim_due() wins in the SAME
                # instant this call's own deadline elapses.
                await buffer.claim_due(tenant_id)
                return None
            return await real_wait_for_result(
                self,
                custom_id,
                short_max_wait=short_max_wait,
                long_max_wait=long_max_wait,
                **kwargs,
            )

        monkeypatch.setattr(BatchWindowBuffer, "wait_for_result", _fake_wait)

        async def _sync_fallback() -> dict[str, Any]:
            raise AssertionError("must not fall back once a claim has won the race")

        stream = await adapter.try_divert(
            tenant_id=tenant_id,
            key_id=key_id,
            body={},
            batch_processor=object(),
            sync_fallback=_sync_fallback,
        )
        assert stream is not None

        async def _publish_shortly() -> None:
            await asyncio.sleep(0.1)
            assert captured_custom_id is not None
            await buffer.publish_result(
                captured_custom_id,
                {
                    "kind": "submitted",
                    "batch_job_id": "job-race-1",
                    "custom_id": captured_custom_id,
                    "poll_url": "/v1/batches/job-race-1",
                },
            )

        publisher = asyncio.create_task(_publish_shortly())
        chunks = [chunk async for chunk in stream.body_stream]
        await publisher

        events = _parse_sse(b"".join(chunks).decode())
        assert [name for name, _ in events] == ["queued", "submitted"], events


class TestLifecycleSurvivesTryAbandonException:
    async def test_try_abandon_exception_falls_back_not_drops(
        self, app: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Second adversarial-review finding: an unguarded try_abandon() would let a
        genuine Redis exception propagate out of _lifecycle, killing the SSE
        generator AFTER "queued" but BEFORE sync_fallback ever runs -- a silent drop
        (G6 "never zero"), proven empirically reachable since the underlying Lua
        script can complete server-side even as the client-side call times out.
        Proves the caller instead treats the exception like a refusal: re-waits
        (bounded), then falls back normally once that also times out -- never a
        raised exception, never a hung stream."""
        tenant_id = uuid.uuid4()
        key_id = uuid.uuid4()
        settings = app.state.settings
        settings.batch_window_seconds = 0.05
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings)
        adapter = BatchDiversionAdapter(settings=settings, buffer=buffer)

        monkeypatch.setattr(batch_diversion_module, "_SHORT_WAIT_MARGIN_SECONDS", 0.05)
        monkeypatch.setattr(batch_diversion_module, "_CLAIMED_CEILING_SECONDS", 0.2)

        async def _raise_abandon(self: BatchWindowBuffer, custom_id: str) -> bool:
            raise RuntimeError("redis unreachable (simulated)")

        monkeypatch.setattr(BatchWindowBuffer, "try_abandon", _raise_abandon)

        fallback_called = False

        async def _sync_fallback() -> dict[str, Any]:
            nonlocal fallback_called
            fallback_called = True
            return UPSTREAM_BODY

        stream = await adapter.try_divert(
            tenant_id=tenant_id,
            key_id=key_id,
            body={},
            batch_processor=object(),
            sync_fallback=_sync_fallback,
        )
        assert stream is not None

        chunks = [chunk async for chunk in stream.body_stream]
        events = _parse_sse(b"".join(chunks).decode())
        assert [name for name, _ in events] == ["queued", "completion"], events
        assert fallback_called, "sync_fallback must still run despite try_abandon raising"
        assert _event_data(events, "completion") == UPSTREAM_BODY


class TestConcurrentAbandonVsClaim:
    async def test_concurrent_try_abandon_vs_claim_due_never_both_never_neither(
        self, app: Any
    ) -> None:
        """G6, the abandon side: race a genuine try_abandon() against a genuine
        claim_due() for the SAME item via asyncio.gather against the SAME real Redis
        (same proof shape as TestAtomicClaim) -- exactly one of {abandon wins, claim
        wins} must happen. Both winning double-processes (a real job AND a sync
        fallback for the same request); neither winning drops it (excluded from
        claim_due's result yet never marked abandoned either)."""
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 1.0
        settings.batch_max_items_per_job = 500
        buffer_a = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)
        buffer_b = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer_a.append(
            tenant_id=tenant_id, custom_id="race-abandon-1", key_id=uuid.uuid4(), body={}
        )
        clock.advance(2.0)  # well past the 1.0s window -- claim_due will see it as due

        abandon_result, claim_result = await asyncio.gather(
            buffer_a.try_abandon("race-abandon-1"),
            buffer_b.claim_due(tenant_id),
        )

        claimed_ids = {c["custom_id"] for c in claim_result} if claim_result else set()
        abandon_won = abandon_result is True
        claim_won = "race-abandon-1" in claimed_ids

        assert abandon_won != claim_won, (
            f"exactly one of {{abandon, claim}} must win, never both/neither: "
            f"abandon_result={abandon_result}, claim_result={claim_result}"
        )


class TestAbandonedMarkerDeletedOnDrain:
    async def test_abandoned_marker_deleted_on_drain_sibling_claim_unaffected(
        self, app: Any
    ) -> None:
        """batch-claim-drain-del TASK.md, M1+M2: _CLAIM_DUE_LUA now DELs an
        abandoned item's result key at the exact moment it drains past it, instead
        of leaving it to expire via _ABANDONED_TTL_SECONDS. One window, two items,
        so both loop branches fire in the same drain -- the abandoned item's marker
        must be gone; the sibling claimed item's marker must be byte-identical to
        before this change."""
        tenant_id = uuid.uuid4()
        clock = _FakeClock()
        settings = app.state.settings
        settings.batch_window_seconds = 1.0
        settings.batch_max_items_per_job = 500
        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

        await buffer.append(
            tenant_id=tenant_id, custom_id="drain-abandoned", key_id=uuid.uuid4(), body={}
        )
        await buffer.append(
            tenant_id=tenant_id, custom_id="drain-claimed", key_id=uuid.uuid4(), body={}
        )

        abandoned = await buffer.try_abandon("drain-abandoned")
        assert abandoned is True, "nothing else touched this custom_id yet -- must win"

        clock.advance(2.0)  # well past the 1.0s window
        claimed = await buffer.claim_due(tenant_id)

        claimed_ids = {c["custom_id"] for c in claimed} if claimed else set()
        assert claimed_ids == {"drain-claimed"}, (
            "the abandoned item must stay excluded; the sibling must still be claimed"
        )

        abandoned_marker = await app.state.redis_client.get(f"{_RESULT_KEY_PREFIX}drain-abandoned")
        claimed_key = f"{_RESULT_KEY_PREFIX}drain-claimed"
        claimed_marker = await app.state.redis_client.get(claimed_key)
        claimed_ttl = await app.state.redis_client.ttl(claimed_key)

        assert abandoned_marker is None, (
            "drain must DEL the abandoned marker -- it lingered until this fix"
        )
        assert claimed_marker is not None
        assert json.loads(claimed_marker.decode()) == {"kind": "claimed"}
        assert claimed_ttl > 0, "the claimed marker keeps its own _RESULT_TTL_SECONDS TTL"


class TestConcurrentAbandonVsClaimDelOnDrain:
    async def test_concurrent_abandon_vs_claim_del_on_drain(self, app: Any) -> None:
        """Extends TestConcurrentAbandonVsClaim's exact race shape across N fresh
        iterations: the XOR mutual-exclusion property must still hold with the new
        DEL branch in place, AND whichever iterations have abandon win must show
        the marker actually deleted afterward -- proving the new branch survives
        concurrent execution, not just a deterministic sequential call (advisor
        review requirement, batch-claim-drain-del TASK.md)."""
        settings = app.state.settings
        settings.batch_window_seconds = 1.0
        settings.batch_max_items_per_job = 500

        abandon_wins_observed = 0
        for i in range(20):
            tenant_id = uuid.uuid4()
            custom_id = f"race-drain-{i}"
            clock = _FakeClock()
            buffer_a = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)
            buffer_b = BatchWindowBuffer(redis=app.state.redis_client, settings=settings, _now=clock)

            await buffer_a.append(
                tenant_id=tenant_id, custom_id=custom_id, key_id=uuid.uuid4(), body={}
            )
            clock.advance(2.0)

            abandon_result, claim_result = await asyncio.gather(
                buffer_a.try_abandon(custom_id),
                buffer_b.claim_due(tenant_id),
            )

            claimed_ids = {c["custom_id"] for c in claim_result} if claim_result else set()
            abandon_won = abandon_result is True
            claim_won = custom_id in claimed_ids

            assert abandon_won != claim_won, (
                f"iteration {i}: exactly one of {{abandon, claim}} must win, "
                f"never both/neither: abandon_result={abandon_result}, "
                f"claim_result={claim_result}"
            )

            marker = await app.state.redis_client.get(f"{_RESULT_KEY_PREFIX}{custom_id}")
            if abandon_won:
                abandon_wins_observed += 1
                assert marker is None, (
                    f"iteration {i}: abandon won the race, so drain must have "
                    f"DELeted the marker -- it survived instead"
                )
            else:
                assert marker is not None
                assert json.loads(marker.decode()) == {"kind": "claimed"}

        assert abandon_wins_observed > 0, (
            "0/20 iterations had abandon win the race -- this test never actually "
            "exercised the new DEL-on-drain branch; increase N or investigate "
            "scheduling bias before trusting this test"
        )


# ---------------------------------------------------------------------------
# HTTP-level: SSE lifecycle, flush orchestration, fallback, policy semantics.
# ---------------------------------------------------------------------------


class TestImmediateSSEResponse:
    async def test_immediate_sse_response_never_blocked(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 2.0  # deliberately long

        try:
            async with client.stream(
                "POST", COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")

                lines_iter = resp.aiter_lines()
                first_line = await asyncio.wait_for(anext(lines_iter), timeout=0.5)
                assert first_line == "event: queued", (
                    f"expected the first SSE line within 0.5s (well under the 2.0s "
                    f"window), got: {first_line!r}"
                )
        finally:
            app.state.batch_processor = None


class TestWindowFlushesAsOneJob:
    async def test_window_flushes_as_one_multi_item_job(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        # Small REAL window: claim_due's due-check is elapsed-time-based (elapsed >=
        # window_seconds), so flush_once() only finds something to claim once the
        # window has genuinely aged past it by wall-clock time — a large window here
        # would make flush_once() a guaranteed no-op.
        app.state.settings.batch_window_seconds = 0.05

        try:
            tasks = [
                asyncio.create_task(
                    client.post(
                        COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
                    )
                )
                for _ in range(3)
            ]
            await asyncio.sleep(0.5)  # let all 3 appends land AND the window age past it

            flusher = _make_flusher(app)
            result = await flusher.flush_once()
            assert result == {"claimed": 1, "items": 3}, f"expected one claimed group: {result}"

            responses = await asyncio.gather(*tasks)
        finally:
            app.state.batch_processor = None

        for resp in responses:
            events = _parse_sse(resp.text)
            names = [name for name, _ in events]
            assert names[0] == "queued"
            assert "submitted" in names

        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 1
        items = await db_session.execute(
            text(
                "SELECT count(*) FROM batch_job_items bji JOIN batch_jobs bj "
                "ON bji.batch_job_id = bj.id WHERE bj.tenant_id = :tid"
            ),
            {"tid": owner["tenant_id"]},
        )
        assert int(items.scalar_one()) == 3


class TestSubmittedEventCarriesJobReference:
    async def test_submitted_event_carries_job_reference(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05

        try:
            task = asyncio.create_task(
                client.post(COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"]))
            )
            await asyncio.sleep(0.5)
            flusher = _make_flusher(app)
            await flusher.flush_once()
            resp = await task
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert [name for name, _ in events] == ["queued", "submitted"], events
        submitted = _event_data(events, "submitted")
        assert "batch_job_id" in submitted
        assert "custom_id" in submitted
        assert submitted["poll_url"] == f"/v1/batches/{submitted['batch_job_id']}"

        # G9: the real result is retrievable ONLY via GET /v1/batches/{id} — the SSE
        # stream has already closed by the time any real provider result could exist.
        poll = await client.get(submitted["poll_url"], headers=_bearer(owner["key"]))
        assert poll.status_code == 200
        matching = [i for i in poll.json()["items"] if i["custom_id"] == submitted["custom_id"]]
        assert len(matching) == 1


class TestFlushFailureFallsBack:
    async def test_flush_failure_falls_back_per_item_over_sse(
        self,
        client: httpx.AsyncClient,
        app: Any,
        owner: dict[str, str],
        active_model: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05

        async def _raise_create(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated DB failure creating job row")

        monkeypatch.setattr(BatchJobRepository, "create", _raise_create)

        try:
            tasks = [
                asyncio.create_task(
                    client.post(
                        COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
                    )
                )
                for _ in range(2)
            ]
            await asyncio.sleep(0.5)
            flusher = _make_flusher(app)
            await flusher.flush_once()
            responses = await asyncio.gather(*tasks)
        finally:
            app.state.batch_processor = None

        for resp in responses:
            assert resp.status_code == 200, resp.text
            events = _parse_sse(resp.text)
            assert [name for name, _ in events] == ["queued", "completion"], events
            assert _event_data(events, "completion") == UPSTREAM_BODY


class TestElapsedWithNoFlush:
    async def test_elapsed_with_no_flush_falls_back(
        self,
        client: httpx.AsyncClient,
        app: Any,
        owner: dict[str, str],
        active_model: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.1
        # Shrink the fixed post-window margin too (production default is generous;
        # keeps this test fast without changing the contract it exercises).
        monkeypatch.setattr(batch_diversion_module, "_SHORT_WAIT_MARGIN_SECONDS", 0.1)

        try:
            # Deliberately never call flush_once() — nothing ever claims this window,
            # simulating a buffer/sweeper that became unreachable after G8's response
            # was already sent.
            resp = await client.post(
                COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
            )
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        assert [name for name, _ in events] == ["queued", "completion"], events
        assert _event_data(events, "completion") == UPSTREAM_BODY


class TestBufferUnreachableFallsBack:
    async def test_buffer_unreachable_falls_back_like_disabled(
        self,
        client: httpx.AsyncClient,
        app: Any,
        owner: dict[str, str],
        active_model: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()

        async def _raise_append(self: Any, **kwargs: Any) -> None:
            raise RuntimeError("redis unreachable (simulated)")

        monkeypatch.setattr(BatchWindowBuffer, "append", _raise_append)

        try:
            resp = await client.post(
                COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
            )
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == UPSTREAM_BODY


class TestDisableMidWindow:
    async def test_disable_mid_window_does_not_disturb_it(
        self, client: httpx.AsyncClient, app: Any, owner: dict[str, str], active_model: str
    ) -> None:
        await _enable_policy(client, owner["jwt"])
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        app.state.settings.batch_window_seconds = 0.05

        try:
            tasks = [
                asyncio.create_task(
                    client.post(
                        COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
                    )
                )
                for _ in range(2)
            ]
            await asyncio.sleep(0.5)

            disable = await client.put(
                POLICY, json={"enabled": False}, headers=_bearer(owner["jwt"])
            )
            assert disable.status_code == 200

            flusher = _make_flusher(app)
            result = await flusher.flush_once()
            assert result["items"] == 2, f"pre-existing window must still flush: {result}"

            responses = await asyncio.gather(*tasks)
        finally:
            app.state.batch_processor = None

        for resp in responses:
            events = _parse_sse(resp.text)
            names = [name for name, _ in events]
            assert "submitted" in names, f"expected submitted despite mid-window disable: {events}"

        # A NEW request made after disabling must not be diverted at all (G11).
        new_call = await client.post(
            COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
        )
        assert new_call.status_code == 200
        assert new_call.headers["content-type"].startswith("application/json")
        assert new_call.json() == UPSTREAM_BODY


class TestDisabledTenantZeroChange:
    async def test_disabled_tenant_zero_change(
        self,
        client: httpx.AsyncClient,
        app: Any,
        db_session: AsyncSession,
        owner: dict[str, str],
        active_model: str,
    ) -> None:
        install_upstream(app, FakeCompletionUpstream())
        app.state.batch_processor = _FakeNoopProcessor()
        try:
            resp = await client.post(
                COMPLETIONS, json=payload(active_model), headers=_bearer(owner["key"])
            )
        finally:
            app.state.batch_processor = None

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert resp.json() == UPSTREAM_BODY
        assert await _batch_jobs_count(db_session, owner["tenant_id"]) == 0

        buffer = BatchWindowBuffer(redis=app.state.redis_client, settings=app.state.settings)
        assert await buffer.active_tenant_ids() == []


class TestMalformedBodyRejectedIdentically:
    async def test_malformed_body_rejected_identically(
        self, client: httpx.AsyncClient, owner: dict[str, str]
    ) -> None:
        await _enable_policy(client, owner["jwt"])

        enabled_resp = await client.post(
            COMPLETIONS,
            json={"model": "openai/gpt-4o"},  # missing "messages" — fails existing validation
            headers=_bearer(owner["key"]),
        )

        disable = await client.put(POLICY, json={"enabled": False}, headers=_bearer(owner["jwt"]))
        assert disable.status_code == 200

        disabled_resp = await client.post(
            COMPLETIONS, json={"model": "openai/gpt-4o"}, headers=_bearer(owner["key"])
        )

        assert enabled_resp.status_code == disabled_resp.status_code
        assert enabled_resp.status_code >= 400
        assert enabled_resp.json().get("code") == disabled_resp.json().get("code")
