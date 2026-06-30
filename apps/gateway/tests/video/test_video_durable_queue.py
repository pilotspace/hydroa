"""Red/Green test suite for video-job durable Redis-backed queue (v48 durable-queue).

Contract under test:
  - RedisVideoJobQueue: enqueue/claim round-trip
  - VideoJobWorker: process_once drains a job; run_forever survives errors
  - recover_orphans: re-enqueues non-terminal rows on startup
  - retry cap: max_retries exceeded → status=failed error=max_retries_exceeded
  - Default-OFF: knob off → no Redis enqueue (existing inline path unchanged)
  - Fail-open: Redis enqueue failure → falls back to inline asyncio.create_task

Invariants:
  - Terminal-status idempotency preserved (set_running/set_succeeded/set_failed guards)
  - Stale/missing job id → worker logs + skips (no crash)
  - Worker loop continues after a single-job exception
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from gateway.video.application.worker import (
    RedisVideoJobQueue,
    VideoJobWorker,
    recover_orphans,
    should_start_video_worker,
)

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_QUEUE_KEY = "video:jobs:pending"


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


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


class _SuccessStub:
    """Returns a tiny fake video payload."""

    async def generate(
        self, prompt: str, model: str, params: dict[str, Any] | None
    ) -> tuple[bytes, str]:
        return b"FAKE_VIDEO_BYTES", "video/mp4"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client,
        tenant_name="DurableQueueTest",
        email="durable-queue-test@example.io",
        password="durable-queue-battery",
    )


@pytest.fixture
def redis_client(app: Any) -> Any:
    """Return the test-app's Redis client (db 9)."""
    return app.state.redis_client


@pytest.fixture
def video_queue(redis_client: Any) -> RedisVideoJobQueue:
    return RedisVideoJobQueue(redis_client)


@pytest.fixture
def video_worker(app: Any, video_queue: RedisVideoJobQueue) -> VideoJobWorker:
    return VideoJobWorker(
        sessionmaker=app.state.sessionmaker,
        queue=video_queue,
        settings=app.state.settings,
        get_video_generator=lambda: getattr(app.state, "video_generator", None),
    )


@pytest.fixture(autouse=True)
async def flush_queue(redis_client: Any) -> None:
    """Flush the durable queue before each test (idempotent)."""
    await redis_client.delete(_QUEUE_KEY)


# ---------------------------------------------------------------------------
# test 1 — enable knob, POST job, drain via process_once(), assert succeeded
# ---------------------------------------------------------------------------


class TestEnabledEnqueueThenDrain:
    async def test_enabled_enqueue_then_drain(
        self, client: Any, app: Any, api_key_info: dict[str, str], video_worker: VideoJobWorker
    ) -> None:
        """With durable queue enabled: POST → Redis enqueue → worker drains → succeeded."""
        object.__setattr__(app.state.settings, "video_durable_queue_enabled", True)

        # Wire up the queue on app.state so the router finds it
        app.state.video_job_queue = video_worker._queue
        app.state.video_generator = _SuccessStub()

        try:
            resp = await client.post(
                "/v1/video/generations",
                json={"model": "test-model", "prompt": "sunset"},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id_str = resp.json()["id"]
            job_id = uuid.UUID(job_id_str)

            # Job is now in the Redis queue — drain it via process_once
            drained = await video_worker.process_once()
            assert drained is True

            # Poll job — must be terminal
            poll = await client.get(
                f"/v1/video/generations/{job_id_str}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            job = poll.json()
            assert job["status"] == "succeeded", f"expected succeeded, got: {job}"
            assert job["result_artifact_id"] is not None
        finally:
            object.__setattr__(app.state.settings, "video_durable_queue_enabled", False)
            app.state.video_generator = None


# ---------------------------------------------------------------------------
# test 2 — recover_orphans re-enqueues non-terminal rows; drain → terminal
# ---------------------------------------------------------------------------


class TestRecoveryRedrivesOrphan:
    async def test_recovery_redrives_orphan(
        self, client: Any, app: Any, api_key_info: dict[str, str], video_worker: VideoJobWorker
    ) -> None:
        """recover_orphans() finds queued rows and re-enqueues them; worker can drain them."""
        app.state.video_generator = _SuccessStub()

        # Submit without queue (inline path) to create a job row
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "m", "prompt": "orphan test"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id_str = resp.json()["id"]

        # Drain the in-process task so it stays in queued (we want to revert to queued)
        # Actually: just create a second job that hasn't been processed — simulate orphan by
        # inserting a job directly via the repo and not running the background task.
        from gateway.video.infrastructure.repository import VideoJobRepository

        async with app.state.sessionmaker() as session:
            repo = VideoJobRepository(session)
            orphan_row = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                model="orphan-model",
                prompt="orphan prompt",
                params=None,
            )
            orphan_id = orphan_row.id
            await session.commit()

        # Also wait for any inline tasks
        tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)

        # Now recover_orphans should enqueue the orphan row (and any other non-terminal ones)
        count = await recover_orphans(app.state.sessionmaker, video_worker._queue)
        assert count >= 1

        # Drain until we process the orphan job
        processed_orphan = False
        for _ in range(count + 2):
            drained = await video_worker.process_once()
            if not drained:
                break
            # Check if orphan reached terminal state
            async with app.state.sessionmaker() as session:
                from gateway.video.infrastructure.orm import VideoGenerationJobRow

                row = await session.get(VideoGenerationJobRow, orphan_id)
                if row is not None and row.status in ("succeeded", "failed"):
                    processed_orphan = True
                    break

        assert processed_orphan, "orphan job was not driven to terminal state by worker"

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test 3 — retry cap: max_retries=1, enqueue twice, assert max_retries_exceeded
# ---------------------------------------------------------------------------


class TestRetryCapMaxRetries:
    async def test_retry_cap_max_retries(
        self, client: Any, app: Any, api_key_info: dict[str, str], video_queue: RedisVideoJobQueue
    ) -> None:
        """Once retry_count exceeds max_retries, worker sets status=failed error=max_retries_exceeded."""
        from gateway.video.infrastructure.repository import VideoJobRepository

        object.__setattr__(app.state.settings, "video_job_max_retries", 1)
        # Use None generator so _process_video_job transitions to failed quickly (no_video_provider)
        app.state.video_generator = None

        try:
            # Create a job row in queued state
            async with app.state.sessionmaker() as session:
                repo = VideoJobRepository(session)
                row = await repo.create(
                    tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                    key_id=uuid.UUID(api_key_info["key_id"]),
                    model="retry-model",
                    prompt="retry test",
                    params=None,
                )
                job_id = row.id
                await session.commit()

            worker = VideoJobWorker(
                sessionmaker=app.state.sessionmaker,
                queue=video_queue,
                settings=app.state.settings,
                get_video_generator=lambda: getattr(app.state, "video_generator", None),
            )

            # Enqueue and drain twice — first drain succeeds (retry_count=1 <= max_retries=1)
            # but _process_video_job will set it to failed (no provider); second drain is moot
            # because it's already terminal. Let's test the cap directly:
            # Reset to queued and manually set retry_count to max+1 via increment_retry calls
            # Approach: enqueue job, drain it (count becomes 1 <= max=1, processes normally)
            # Then reset status to queued, enqueue, drain again — retry_count becomes 2 > max=1
            # → max_retries_exceeded
            await video_queue.enqueue(job_id)
            await (
                worker.process_once()
            )  # first drain: retry_count = 1, processes (→failed via no provider)

            # Check — job should be failed via normal processing (no provider)
            async with app.state.sessionmaker() as session:
                from gateway.video.infrastructure.orm import VideoGenerationJobRow

                job_row = await session.get(VideoGenerationJobRow, job_id)
                assert job_row is not None
                # Could be "failed" from no provider — that's fine. Now test the retry cap path:
                # We need retry_count > max. Manually increment retry_count one more time by
                # making a fresh job and burning through retries.

            # Create a second job to test the cap path specifically
            async with app.state.sessionmaker() as session:
                repo = VideoJobRepository(session)
                row2 = await repo.create(
                    tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                    key_id=uuid.UUID(api_key_info["key_id"]),
                    model="retry-model-2",
                    prompt="retry test 2",
                    params=None,
                )
                job_id2 = row2.id
                await session.commit()

            # With max_retries=1: drain 3 times, second drain hits the cap
            # First drain: retry_count goes from 0→1, 1 <= 1 → process normally (no provider → failed)
            await video_queue.enqueue(job_id2)
            await worker.process_once()

            # Job is now in terminal state from first processing. To test the cap path,
            # we need a job that is non-terminal with high retry_count. Use increment_retry directly.
            async with app.state.sessionmaker() as session:
                repo = VideoJobRepository(session)
                row3 = await repo.create(
                    tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                    key_id=uuid.UUID(api_key_info["key_id"]),
                    model="cap-test",
                    prompt="cap test prompt",
                    params=None,
                )
                job_id3 = row3.id
                # Pre-increment retry_count to max_retries so next increment exceeds cap
                await repo.increment_retry(job_id3)  # retry_count = 1 (== max)
                await session.commit()

            # Now enqueue and drain — worker increments to 2 > max=1 → max_retries_exceeded
            await video_queue.enqueue(job_id3)
            await worker.process_once()

            async with app.state.sessionmaker() as session:
                from gateway.video.infrastructure.orm import VideoGenerationJobRow

                final_row = await session.get(VideoGenerationJobRow, job_id3)
                assert final_row is not None
                assert final_row.status == "failed", f"expected failed, got: {final_row.status}"
                assert final_row.error == "max_retries_exceeded", f"wrong error: {final_row.error}"

        finally:
            object.__setattr__(app.state.settings, "video_job_max_retries", 3)

    async def test_max_retries_zero_is_unlimited(
        self, client: Any, app: Any, api_key_info: dict[str, str], video_queue: RedisVideoJobQueue
    ) -> None:
        """max_retries=0 means UNLIMITED — a job with a high retry_count is still
        PROCESSED (never poison-capped). Guards the footgun where a 0 cap would
        fail a fresh job's first drive without ever attempting it."""
        from gateway.video.infrastructure.repository import VideoJobRepository

        object.__setattr__(app.state.settings, "video_job_max_retries", 0)
        app.state.video_generator = (
            None  # → no_video_provider_configured (NOT max_retries_exceeded)
        )

        try:
            async with app.state.sessionmaker() as session:
                repo = VideoJobRepository(session)
                row = await repo.create(
                    tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                    key_id=uuid.UUID(api_key_info["key_id"]),
                    model="unlimited-model",
                    prompt="unlimited test",
                    params=None,
                )
                job_id = row.id
                # Drive the retry_count well above any finite cap.
                for _ in range(5):
                    await repo.increment_retry(job_id)
                await session.commit()

            worker = VideoJobWorker(
                sessionmaker=app.state.sessionmaker,
                queue=video_queue,
                settings=app.state.settings,
                get_video_generator=lambda: getattr(app.state, "video_generator", None),
            )
            await video_queue.enqueue(job_id)
            await worker.process_once()

            async with app.state.sessionmaker() as session:
                from gateway.video.infrastructure.orm import VideoGenerationJobRow

                final_row = await session.get(VideoGenerationJobRow, job_id)
                assert final_row is not None
                # It was PROCESSED (honest no-provider failure), NOT poison-capped.
                assert final_row.error == "no_video_provider_configured", (
                    f"expected the job to be processed, got error={final_row.error}"
                )
        finally:
            object.__setattr__(app.state.settings, "video_job_max_retries", 3)


# ---------------------------------------------------------------------------
# test 4 — stale id (not in DB) → worker logs + skips, returns True, no crash
# ---------------------------------------------------------------------------


class TestStaleIdSkipped:
    async def test_stale_id_skipped(
        self, app: Any, video_worker: VideoJobWorker, video_queue: RedisVideoJobQueue
    ) -> None:
        """Enqueueing a UUID not in DB: process_once returns True without crashing."""
        phantom_id = uuid.uuid4()
        await video_queue.enqueue(phantom_id)

        # Must not raise
        result = await video_worker.process_once()
        assert result is True


# ---------------------------------------------------------------------------
# test 5 — terminal job re-enqueued → worker skips (idempotent)
# ---------------------------------------------------------------------------


class TestTerminalNotClobbered:
    async def test_terminal_not_clobbered(
        self, client: Any, app: Any, api_key_info: dict[str, str], video_worker: VideoJobWorker
    ) -> None:
        """A succeeded job re-enqueued to the durable queue is skipped — status stays succeeded."""
        app.state.video_generator = _SuccessStub()

        # Submit via inline path
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "m", "prompt": "terminal test"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id_str = resp.json()["id"]
        job_id = uuid.UUID(job_id_str)

        # Wait for in-process task to complete
        tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)

        # Verify succeeded
        poll = await client.get(
            f"/v1/video/generations/{job_id_str}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.json()["status"] == "succeeded"

        # Re-enqueue the terminal job id
        await video_worker._queue.enqueue(job_id)
        result = await video_worker.process_once()
        assert result is True  # claimed from queue

        # Status must still be succeeded (idempotent skip)
        poll2 = await client.get(
            f"/v1/video/generations/{job_id_str}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll2.json()["status"] == "succeeded"
        # Artifact still present
        assert poll2.json()["result_artifact_id"] is not None

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test 6 — knob off (default) → POST does NOT enqueue to Redis
# ---------------------------------------------------------------------------


class TestKnobOffInProcess:
    async def test_knob_off_in_process(
        self, client: Any, app: Any, api_key_info: dict[str, str], redis_client: Any
    ) -> None:
        """Default (knob off): POST job → no entry in video:jobs:pending Redis key."""
        # Ensure knob is off (default)
        assert not app.state.settings.video_durable_queue_enabled

        app.state.video_generator = _SuccessStub()

        resp = await client.post(
            "/v1/video/generations",
            json={"model": "m", "prompt": "knob-off test"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200

        # Queue key must not exist or be empty
        queue_len = await redis_client.llen(_QUEUE_KEY)
        assert queue_len == 0, f"expected queue empty (knob off), got len={queue_len}"

        # Wait for in-process task
        tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test 7 — Redis enqueue failure → fails open, falls back to in-process task
# ---------------------------------------------------------------------------


class TestRedisDownEnqueueFallback:
    async def test_redis_down_enqueue_fallback(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """If queue.enqueue raises, router falls back to inline asyncio.create_task."""
        object.__setattr__(app.state.settings, "video_durable_queue_enabled", True)
        app.state.video_generator = _SuccessStub()

        # Create the queue and install it on app.state
        from gateway.video.application.worker import RedisVideoJobQueue

        mock_queue = RedisVideoJobQueue(app.state.redis_client)
        # Patch enqueue to raise
        mock_queue.enqueue = AsyncMock(side_effect=Exception("Redis down"))  # type: ignore[method-assign]
        app.state.video_job_queue = mock_queue

        try:
            resp = await client.post(
                "/v1/video/generations",
                json={"model": "m", "prompt": "fallback test"},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id_str = resp.json()["id"]

            # The router fell back to in-process task — wait for it
            tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
            if tasks:
                await asyncio.gather(*list(tasks), return_exceptions=True)

            # Job should have been processed by the in-process fallback
            poll = await client.get(
                f"/v1/video/generations/{job_id_str}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            # It should not still be queued — the in-process task ran
            assert poll.json()["status"] != "queued", f"job still queued: {poll.json()}"
        finally:
            object.__setattr__(app.state.settings, "video_durable_queue_enabled", False)
            app.state.video_generator = None


# ---------------------------------------------------------------------------
# test 8 — worker loop survives single-job failure (loop does not die)
# ---------------------------------------------------------------------------


class TestWorkerSurvivesFailure:
    async def test_worker_survives_failure(
        self, app: Any, video_worker: VideoJobWorker, video_queue: RedisVideoJobQueue
    ) -> None:
        """A stale/invalid job id does not kill the worker loop — process_once works again."""
        # Enqueue a phantom id (not in DB) — _drive logs + skips, no raise
        phantom_id = uuid.uuid4()
        await video_queue.enqueue(phantom_id)

        # First process_once: claims phantom, drive skips (not in DB) → no raise
        result1 = await video_worker.process_once()
        assert result1 is True

        # Enqueue another phantom — worker is still alive and can process
        phantom_id2 = uuid.uuid4()
        await video_queue.enqueue(phantom_id2)
        result2 = await video_worker.process_once()
        assert result2 is True


# ---------------------------------------------------------------------------
# test 9 — DB column retry_count exists on video_generation_jobs
# ---------------------------------------------------------------------------


class TestMigrationRetryCount:
    async def test_retry_count_column_exists(self, app: Any) -> None:
        """video_generation_jobs.retry_count column must exist after migrations."""
        async with app.state.sessionmaker() as session:
            result = await session.execute(
                # information_schema is available in PostgreSQL
                # Use text() to query directly
                __import__("sqlalchemy").text(
                    "SELECT column_name, data_type, column_default "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'video_generation_jobs' "
                    "  AND column_name = 'retry_count'"
                )
            )
            row = result.fetchone()
            assert row is not None, "retry_count column missing from video_generation_jobs"
            assert row[0] == "retry_count"
            assert "int" in row[1].lower(), f"expected integer type, got: {row[1]}"


# ---------------------------------------------------------------------------
# test 10 — should_start_video_worker predicate
# ---------------------------------------------------------------------------


class TestShouldStartVideoWorker:
    async def test_returns_false_when_disabled(self, app: Any) -> None:
        """Default settings: should_start_video_worker returns False."""
        assert not should_start_video_worker(app.state.settings)

    async def test_returns_true_when_enabled(self, app: Any) -> None:
        """With knob True: should_start_video_worker returns True."""
        object.__setattr__(app.state.settings, "video_durable_queue_enabled", True)
        try:
            assert should_start_video_worker(app.state.settings)
        finally:
            object.__setattr__(app.state.settings, "video_durable_queue_enabled", False)
