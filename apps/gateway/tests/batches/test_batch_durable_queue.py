"""Red/Green test suite for the batch-job durable Redis-backed queue (batch-job-store TASK.md §3).

Contract under test — structurally copied from video's durable-queue (v48), per MILESTONE.md's
shared decision "batch_jobs table + status machine + durable-queue/worker shape (copied from
video_generation_jobs)":
  - RedisBatchJobQueue: enqueue/claim round-trip (LPUSH/BRPOP, FIFO)
  - BatchJobWorker: process_once() drains one job; run_forever() survives a single-job error
  - recover_orphans(): re-enqueues non-terminal rows on startup
    (status IN validating/in_progress/finalizing/cancelling)
  - Default-OFF: batch_durable_queue_enabled=False -> POST /v1/batches never touches Redis;
    processing runs via the same inline asyncio.Task path exercised in test_batch_jobs.py
  - Fail-open: a Redis enqueue failure falls back to the inline path — no job is ever dropped
  - Two workers sharing one Redis queue never double-process the same job (BRPOP is atomic;
    a duplicate drive on an already-terminal job is a no-op via the status guard)

This task's only reachable terminal is "failed" (no BatchProcessor is wired yet — that's
openai-batch-adapter / anthropic-batch-adapter, downstream tasks), so every test here expects
error="no_batch_processor_configured" on completion, exactly like test_batch_jobs.py's
TestWorkerNoProcessorConfigured.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gateway.batches.application.worker import (
    BatchJobWorker,
    RedisBatchJobQueue,
    recover_orphans,
    should_start_batch_worker,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _line_item(
    custom_id: str, *, model: str = "test-model", content: str = "hello"
) -> dict[str, Any]:
    return {
        "custom_id": custom_id,
        "body": {"model": model, "messages": [{"role": "user", "content": content}]},
    }


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a new tenant, log in, create an API key. Returns {key, key_id, tenant_id, jwt}."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


async def _await_all_batch_tasks(app: Any) -> None:
    tasks: set[asyncio.Task[None]] = getattr(app.state, "batch_jobs_tasks", set())
    if tasks:
        pending = set(tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client,
        tenant_name="BatchDurableQueueTest",
        email="batch-durable-queue-test@example.io",
        password="batch-durable-queue-battery",
    )


@pytest.fixture
def redis_client(app: Any) -> Any:
    """Return the test-app's Redis client (db 9)."""
    return app.state.redis_client


@pytest.fixture
def batch_queue(redis_client: Any) -> RedisBatchJobQueue:
    return RedisBatchJobQueue(redis_client)


@pytest.fixture
def batch_worker(app: Any, batch_queue: RedisBatchJobQueue) -> BatchJobWorker:
    return BatchJobWorker(
        sessionmaker=app.state.sessionmaker,
        queue=batch_queue,
        settings=app.state.settings,
        get_batch_processor=lambda: getattr(app.state, "batch_processor", None),
    )


# ---------------------------------------------------------------------------
# Scenario: Durable queue enqueues a submitted job
# ---------------------------------------------------------------------------


class TestDurableQueueEnqueueThenWorkerClaims:
    async def test_enqueue_then_worker_claims(
        self,
        client: Any,
        app: Any,
        api_key_info: dict[str, str],
        batch_worker: BatchJobWorker,
    ) -> None:
        """With durable queue enabled: POST -> Redis enqueue -> worker drains -> terminal."""
        object.__setattr__(app.state.settings, "batch_durable_queue_enabled", True)
        app.state.batch_job_queue = batch_worker._queue

        try:
            resp = await client.post(
                "/v1/batches",
                json={"line_items": [_line_item("d1")]},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id_str = resp.json()["id"]
            job_id = uuid.UUID(job_id_str)

            # Job is now in the Redis queue — drain it via process_once.
            drained = await batch_worker.process_once()
            assert drained is True

            poll = await client.get(
                f"/v1/batches/{job_id_str}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            job = poll.json()
            assert job["status"] == "failed", f"expected failed (no processor), got: {job}"
            assert job["status_counts"]["errored"] == 1
            assert job_id == uuid.UUID(job["id"])
        finally:
            object.__setattr__(app.state.settings, "batch_durable_queue_enabled", False)


# ---------------------------------------------------------------------------
# Scenario: Redis enqueue failure falls back to inline processing
# ---------------------------------------------------------------------------


class TestRedisDownEnqueueFallback:
    async def test_redis_down_enqueue_fallback(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """If queue.enqueue raises, the router falls back to an inline asyncio.create_task —
        the submission still returns 200 and no job is silently dropped."""
        object.__setattr__(app.state.settings, "batch_durable_queue_enabled", True)

        mock_queue = RedisBatchJobQueue(app.state.redis_client)
        mock_queue.enqueue = AsyncMock(side_effect=Exception("Redis down"))  # type: ignore[method-assign]
        app.state.batch_job_queue = mock_queue

        try:
            resp = await client.post(
                "/v1/batches",
                json={"line_items": [_line_item("f1")]},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id_str = resp.json()["id"]

            # The router fell back to an in-process task — wait for it.
            await _await_all_batch_tasks(app)

            poll = await client.get(
                f"/v1/batches/{job_id_str}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            # It must not still be "validating" — the in-process fallback ran it.
            assert poll.json()["status"] != "validating", f"job still validating: {poll.json()}"
        finally:
            object.__setattr__(app.state.settings, "batch_durable_queue_enabled", False)


# ---------------------------------------------------------------------------
# Scenario: Orphaned jobs are recovered on restart
# ---------------------------------------------------------------------------


class TestRecoveryRedrivesOrphan:
    async def test_recovery_redrives_orphan(
        self,
        client: Any,
        app: Any,
        api_key_info: dict[str, str],
        batch_worker: BatchJobWorker,
    ) -> None:
        """recover_orphans() finds non-terminal rows and re-enqueues them; the worker drains them."""
        from gateway.batches.infrastructure.repository import BatchJobRepository

        # Create a job row directly via the repository — bypassing the router/inline
        # task so it stays "validating" (simulating a gateway restart mid-flight).
        async with app.state.sessionmaker() as session:
            repo = BatchJobRepository(session)
            orphan_row = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                line_items=[_line_item("orphan-1")],
            )
            orphan_id = orphan_row.id
            await session.commit()

        count = await recover_orphans(app.state.sessionmaker, batch_worker._queue)
        assert count >= 1

        processed_orphan = False
        for _ in range(count + 2):
            drained = await batch_worker.process_once()
            if not drained:
                break
            async with app.state.sessionmaker() as session:
                from gateway.batches.infrastructure.orm import BatchJobRow

                row = await session.get(BatchJobRow, orphan_id)
                if row is not None and row.status in (
                    "failed",
                    "completed",
                    "expired",
                    "cancelled",
                ):
                    processed_orphan = True
                    break

        assert processed_orphan, "orphan job was not driven to terminal state by the worker"


# ---------------------------------------------------------------------------
# Scenario: Two worker instances never double-process the same job
# ---------------------------------------------------------------------------


class TestTwoWorkersNoDoubleProcess:
    async def test_two_workers_never_double_process(
        self, app: Any, api_key_info: dict[str, str], batch_queue: RedisBatchJobQueue
    ) -> None:
        """Two BatchJobWorker instances share one Redis queue. A job enqueued once is
        claimed by exactly one worker (BRPOP is atomic); a re-enqueued terminal job is
        a no-op drive (status guard)."""
        from gateway.batches.infrastructure.repository import BatchJobRepository

        async with app.state.sessionmaker() as session:
            repo = BatchJobRepository(session)
            row = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                line_items=[_line_item("race-1")],
            )
            job_id = row.id
            await session.commit()

        worker_1 = BatchJobWorker(
            sessionmaker=app.state.sessionmaker,
            queue=batch_queue,
            settings=app.state.settings,
            get_batch_processor=lambda: getattr(app.state, "batch_processor", None),
        )
        worker_2 = BatchJobWorker(
            sessionmaker=app.state.sessionmaker,
            queue=batch_queue,
            settings=app.state.settings,
            get_batch_processor=lambda: getattr(app.state, "batch_processor", None),
        )

        await batch_queue.enqueue(job_id)

        # Only one worker's BRPOP can claim the single queued id — the other's claim
        # times out on an empty queue (process_once returns False).
        claimed_1 = await worker_1.process_once()
        claimed_2 = await worker_2.process_once()
        assert claimed_1 is True
        assert claimed_2 is False, "a second worker must not claim an already-drained job"

        async with app.state.sessionmaker() as session:
            from gateway.batches.infrastructure.orm import BatchJobRow

            row = await session.get(BatchJobRow, job_id)
            assert row is not None
            assert row.status == "failed"  # no processor configured — honest terminal

        # A duplicate drive on an already-terminal job (e.g. a stray re-enqueue) is a
        # no-op — the status guard prevents corrupting the terminal result.
        await batch_queue.enqueue(job_id)
        claimed_again = await worker_1.process_once()
        assert claimed_again is True  # claimed from the queue...

        async with app.state.sessionmaker() as session:
            from gateway.batches.infrastructure.orm import BatchJobRow

            row2 = await session.get(BatchJobRow, job_id)
            assert row2 is not None
            assert row2.status == "failed"  # ...but the guard held: unchanged


# ---------------------------------------------------------------------------
# Hardening: a job that never reaches a terminal status (processor "succeeds"
# without driving it to terminal) is marked failed once retry_count exceeds
# batch_job_max_retries — it is not redriven forever.
# ---------------------------------------------------------------------------


class _NoopProcessor:
    """Fake BatchProcessor whose process() returns cleanly without ever setting
    a terminal status — simulates an adapter bug where the call "succeeds" but
    the job is left in_progress, so _drive() keeps re-claiming it."""

    async def process(self, job_id: uuid.UUID) -> None:
        return None


class TestMaxRetriesExceeded:
    async def test_retry_count_exceeding_max_retries_marks_failed(
        self,
        app: Any,
        api_key_info: dict[str, str],
        batch_worker: BatchJobWorker,
        batch_queue: RedisBatchJobQueue,
    ) -> None:
        """A job stuck non-terminal is redriven up to batch_job_max_retries times,
        then marked failed/max_retries_exceeded rather than redriven forever."""
        from gateway.batches.infrastructure.orm import BatchJobRow
        from gateway.batches.infrastructure.repository import BatchJobRepository

        app.state.batch_processor = _NoopProcessor()
        object.__setattr__(app.state.settings, "batch_job_max_retries", 1)

        try:
            async with app.state.sessionmaker() as session:
                repo = BatchJobRepository(session)
                row = await repo.create(
                    tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                    key_id=uuid.UUID(api_key_info["key_id"]),
                    line_items=[_line_item("retry-cap-1")],
                )
                job_id = row.id
                await session.commit()

            # Drive 1: retry_count -> 1; 1 > max_retries(1) is False -> processes
            # (no-op processor leaves the job "in_progress", non-terminal).
            await batch_queue.enqueue(job_id)
            assert await batch_worker.process_once() is True

            async with app.state.sessionmaker() as session:
                mid_row = await session.get(BatchJobRow, job_id)
                assert mid_row is not None
                assert mid_row.status == "in_progress", (
                    f"expected still in_progress after drive 1, got: {mid_row.status}"
                )

            # Drive 2: retry_count -> 2; 2 > max_retries(1) is True -> exceeded.
            await batch_queue.enqueue(job_id)
            assert await batch_worker.process_once() is True

            async with app.state.sessionmaker() as session:
                final_row = await session.get(BatchJobRow, job_id)
                assert final_row is not None
                assert final_row.status == "failed", (
                    f"expected failed after exceeding max_retries, got: {final_row.status}"
                )
                assert final_row.error == "max_retries_exceeded"
        finally:
            app.state.batch_processor = None
            object.__setattr__(app.state.settings, "batch_job_max_retries", 3)


# ---------------------------------------------------------------------------
# Hardening: recover_orphans() must not crash (or lose track of the rest of the
# batch) when a single job's re-enqueue raises (Redis down mid-recovery).
# ---------------------------------------------------------------------------


class TestRecoverOrphansEnqueueFailure:
    async def test_enqueue_failure_is_logged_and_skipped_not_raised(
        self,
        app: Any,
        api_key_info: dict[str, str],
    ) -> None:
        """A Redis failure while re-enqueuing one orphan must not raise out of
        recover_orphans, and must not stop the rest of the batch from being
        recovered — it is logged and skipped; the returned count excludes it.

        Two orphans, one failing enqueue and one succeeding: proves the loop
        continues past the failure (await_count == 2) rather than the count
        being vacuously satisfiable by a discovery/loop path that never runs
        at all (a single-orphan version can't distinguish the two)."""
        from gateway.batches.infrastructure.repository import BatchJobRepository

        async with app.state.sessionmaker() as session:
            repo = BatchJobRepository(session)
            orphan_1 = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                line_items=[_line_item("orphan-enqueue-fail-1")],
            )
            orphan_2 = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                line_items=[_line_item("orphan-enqueue-fail-2")],
            )
            await session.commit()
        assert orphan_1.status == "validating"
        assert orphan_2.status == "validating"

        failing_queue = RedisBatchJobQueue(app.state.redis_client)
        failing_queue.enqueue = AsyncMock(  # type: ignore[method-assign]
            side_effect=[Exception("Redis down"), None]
        )

        count = await recover_orphans(app.state.sessionmaker, failing_queue)

        assert count == 1, "exactly one of the two orphans must be counted as recovered"
        assert failing_queue.enqueue.await_count == 2, (
            "both orphans must have an enqueue attempt — the failure on the first "
            "must not short-circuit the loop before the second is tried"
        )


# ---------------------------------------------------------------------------
# should_start_batch_worker predicate
# ---------------------------------------------------------------------------


class TestShouldStartBatchWorker:
    async def test_returns_false_when_disabled(self, app: Any) -> None:
        """Default settings: should_start_batch_worker returns False."""
        assert not should_start_batch_worker(app.state.settings)

    async def test_returns_true_when_enabled(self, app: Any) -> None:
        object.__setattr__(app.state.settings, "batch_durable_queue_enabled", True)
        try:
            assert should_start_batch_worker(app.state.settings)
        finally:
            object.__setattr__(app.state.settings, "batch_durable_queue_enabled", False)


# ---------------------------------------------------------------------------
# Worker loop survives a single-job failure (loop does not die)
# ---------------------------------------------------------------------------


class TestWorkerSurvivesFailure:
    async def test_worker_survives_stale_id(
        self, app: Any, batch_worker: BatchJobWorker, batch_queue: RedisBatchJobQueue
    ) -> None:
        """A stale/invalid job id (not in DB) does not kill the worker — process_once
        returns True (claimed) without crashing, and can process again afterward."""
        phantom_id = uuid.uuid4()
        await batch_queue.enqueue(phantom_id)

        result1 = await batch_worker.process_once()
        assert result1 is True

        phantom_id2 = uuid.uuid4()
        await batch_queue.enqueue(phantom_id2)
        result2 = await batch_worker.process_once()
        assert result2 is True


# ---------------------------------------------------------------------------
# Hardening: enqueue() must not block the request path forever on a hung Redis
# connection (design-for-failure — timeouts on outbound IO).
# ---------------------------------------------------------------------------


class TestEnqueueTimeoutBounded:
    async def test_enqueue_raises_within_bound_on_hung_redis(self) -> None:
        """A hung LPUSH (connection alive but never replying — e.g. a mid-op network
        partition) must not block enqueue() indefinitely. It must raise (TimeoutError)
        within a small bounded wall-clock window instead of hanging the caller."""
        import time

        hung_redis = AsyncMock()

        async def _hang(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(60)

        hung_redis.lpush = AsyncMock(side_effect=_hang)
        queue = RedisBatchJobQueue(hung_redis)

        start = time.monotonic()
        with pytest.raises(TimeoutError):
            await queue.enqueue(uuid.uuid4())
        elapsed = time.monotonic() - start

        # TIME BUDGET: bad path is 60s (the _hang mock's sleep); good path is enqueue's own
        # timeout firing, which is what pytest.raises(TimeoutError) above already proves. 10s
        # sits far below the hang and far above the bound, so only an UNBOUNDED enqueue fails.
        assert elapsed < 10, f"enqueue() took {elapsed:.1f}s — not bounded by a timeout"


# ---------------------------------------------------------------------------
# Hardening: a missing app.state.batch_job_queue (settings/lifespan wiring drift)
# must take the SAME fail-open fallback as a raised enqueue() exception — not
# escape as an uncaught 500 that strands the job row in "validating" forever.
# ---------------------------------------------------------------------------


class TestMissingQueueAttributeFallback:
    async def test_missing_batch_job_queue_falls_back_to_inline(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """batch_durable_queue_enabled=True but app.state.batch_job_queue was never set
        (e.g. settings flipped after lifespan startup already ran) — the submission must
        still return 200 and the job must still complete via the inline fallback, not 500."""
        assert not hasattr(app.state, "batch_job_queue"), (
            "test assumption violated: batch_job_queue must be absent on a fresh app"
        )
        object.__setattr__(app.state.settings, "batch_durable_queue_enabled", True)

        try:
            resp = await client.post(
                "/v1/batches",
                json={"line_items": [_line_item("missing-queue-attr")]},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200 (fail-open), got: {resp.text}"
            job_id_str = resp.json()["id"]

            await _await_all_batch_tasks(app)

            poll = await client.get(
                f"/v1/batches/{job_id_str}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            assert poll.json()["status"] != "validating", (
                f"job stranded in validating (fallback never ran): {poll.json()}"
            )
        finally:
            object.__setattr__(app.state.settings, "batch_durable_queue_enabled", False)
