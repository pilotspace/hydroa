"""Red/Green test suite for gateway/batches domain — HTTP surface (default inline path).

Contract under test (batch-job-store TASK.md §3, frozen v1):
  POST /v1/batches   body: {line_items: [{custom_id, body}, ...]}
       -> 200 {id, status:"validating", item_count, status_counts, created_at, updated_at}
       -> 422 batch_items_empty | batch_items_too_many | batch_item_invalid
       -> 401 (missing/invalid key, existing auth contract unchanged)
  GET  /v1/batches/{id}
       -> 200 {id, status, item_count, status_counts, error, created_at, updated_at}
       -> 404 ERR_BATCH_JOB_NOT_FOUND  (missing OR cross-tenant — no oracle)
  GET  /v1/batches?limit&offset
       -> 200 {jobs:[...]}  tenant-scoped, newest-first

status vocabulary (job, OpenAI's exact 8-state batch.status):
  validating|failed|in_progress|finalizing|completed|expired|cancelling|cancelled
status_counts vocabulary (items, Anthropic's exact result.type + pending):
  pending|succeeded|errored|canceled|expired

TENANT-ISOLATION INVARIANT: cross-tenant job id -> 404 — never reveal another tenant's job.
HONEST DEGRADATION: no BatchProcessor configured -> validating -> in_progress -> failed,
  error="no_batch_processor_configured", every pending item cascades to errored (never left
  stuck pending behind a terminal job).
ATOMICITY: any Reject (empty/too-many/invalid/duplicate-custom_id) creates NO row at all.

This task builds the job-store skeleton only — no real provider call. openai-batch-adapter /
anthropic-batch-adapter (downstream tasks) plug in a real BatchProcessor later.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ITEM_VOCAB = ("pending", "succeeded", "errored", "canceled", "expired")
_JOB_VOCAB = (
    "validating",
    "failed",
    "in_progress",
    "finalizing",
    "completed",
    "expired",
    "cancelling",
    "cancelled",
)


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
    """Wait for all tracked in-process batch job tasks to complete."""
    tasks: set[asyncio.Task[None]] = getattr(app.state, "batch_jobs_tasks", set())
    if tasks:
        pending = set(tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def _list_batch_jobs(client: Any, key: str) -> list[dict[str, Any]]:
    resp = await client.get("/v1/batches", headers=_bearer(key))
    assert resp.status_code == 200, f"list failed: {resp.text}"
    return resp.json()["jobs"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Primary tenant fixture."""
    return await _signup_and_key(
        client,
        tenant_name="BatchTest",
        email="batch-test@example.io",
        password="batch-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    """A SECOND tenant — used for cross-tenant isolation checks."""
    return await _signup_and_key(
        client,
        tenant_name="BatchOther",
        email="batch-other@example.io",
        password="batch-other-battery",
    )


# ---------------------------------------------------------------------------
# Scenario: Submit a batch job with valid line items
# ---------------------------------------------------------------------------


class TestSubmitReturnsValidating:
    async def test_submit_returns_validating(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST /v1/batches returns 200 with status=validating immediately, item_count=3."""
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("c1"), _line_item("c2"), _line_item("c3")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "id" in body
        assert body["status"] == "validating"
        assert body["item_count"] == 3
        assert body["status_counts"] == {
            "pending": 3,
            "succeeded": 0,
            "errored": 0,
            "canceled": 0,
            "expired": 0,
        }
        assert "created_at" in body
        assert "updated_at" in body

        await _await_all_batch_tasks(app)


# ---------------------------------------------------------------------------
# Scenario: Poll a batch job by id
# ---------------------------------------------------------------------------


class TestPollBatchJobById:
    async def test_poll_batch_job_by_id(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """GET /v1/batches/{id} returns 200 with the job's current status + item counts."""
        submit = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("p1")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert submit.status_code == 200
        job_id = submit.json()["id"]

        poll = await client.get(
            f"/v1/batches/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200, f"poll failed: {poll.text}"
        job = poll.json()
        assert job["id"] == job_id
        # The background task may have already raced this job to its only reachable
        # terminal (failed, no processor configured) — assert the full valid vocabulary,
        # not a single racy value (see TestWorkerNoProcessorConfigured for the
        # deterministic terminal-state assertion).
        assert job["status"] in _JOB_VOCAB, f"unexpected status: {job['status']}"
        assert job["item_count"] == 1
        assert set(job["status_counts"].keys()) == set(_ITEM_VOCAB)
        assert sum(job["status_counts"].values()) == 1
        assert "error" in job
        assert "created_at" in job
        assert "updated_at" in job

        await _await_all_batch_tasks(app)


# ---------------------------------------------------------------------------
# Scenario: List batch jobs for a tenant
# ---------------------------------------------------------------------------


class TestListBatchJobsTenantScopedNewestFirst:
    async def test_list_tenant_scoped_newest_first(
        self, client: Any, app: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """GET /v1/batches: tenant-scoped list, newest first, no cross-tenant leak."""
        r1 = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("a1")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert r1.status_code == 200
        job_id_1 = r1.json()["id"]

        r2 = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("a2")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert r2.status_code == 200
        job_id_2 = r2.json()["id"]

        r3 = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("b1")]},
            headers=_bearer(other_tenant["key"]),
        )
        assert r3.status_code == 200

        await _await_all_batch_tasks(app)

        jobs_a = await _list_batch_jobs(client, api_key_info["key"])
        assert len(jobs_a) == 2
        job_ids = [j["id"] for j in jobs_a]
        assert job_id_1 in job_ids
        assert job_id_2 in job_ids
        # Newest first (created_at DESC)
        assert jobs_a[0]["id"] == job_id_2
        for j in jobs_a:
            assert set(j.keys()) >= {
                "id",
                "status",
                "item_count",
                "status_counts",
                "created_at",
                "updated_at",
            }

        jobs_b = await _list_batch_jobs(client, other_tenant["key"])
        assert len(jobs_b) == 1

    async def test_unknown_job_id_404(self, client: Any, api_key_info: dict[str, str]) -> None:
        """GET /v1/batches/{unknown-id} -> 404 ERR_BATCH_JOB_NOT_FOUND."""
        fake_id = str(uuid.uuid4())
        resp = await client.get(
            f"/v1/batches/{fake_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404
        assert resp.json().get("code") == "ERR_BATCH_JOB_NOT_FOUND"


# ---------------------------------------------------------------------------
# Scenario: Worker picks up a validating job with no processor configured
# Scenario: A terminal job never leaves pending items behind
# ---------------------------------------------------------------------------


class TestWorkerNoProcessorConfigured:
    async def test_no_processor_configured_fails_honestly_and_drains_items(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """No BatchProcessor on app.state -> validating -> in_progress -> failed,
        error='no_batch_processor_configured'; every pending item cascades to errored."""
        # Ensure honest default: no processor configured.
        assert getattr(app.state, "batch_processor", None) is None

        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("n1"), _line_item("n2"), _line_item("n3")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        await _await_all_batch_tasks(app)

        poll = await client.get(
            f"/v1/batches/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200
        job = poll.json()
        assert job["status"] == "failed", f"expected failed, got: {job}"
        assert job["error"] == "no_batch_processor_configured"
        assert job["item_count"] == 3
        # Never left "pending" behind a terminal job — all 3 cascade to errored.
        assert job["status_counts"] == {
            "pending": 0,
            "succeeded": 0,
            "errored": 3,
            "canceled": 0,
            "expired": 0,
        }


# ---------------------------------------------------------------------------
# Scenario: Configured processor outlasts the per-job timeout
# ---------------------------------------------------------------------------


class _SlowProcessor:
    """Fake BatchProcessor whose process() outlasts a short timeout."""

    async def process(self, job_id: uuid.UUID) -> None:
        await asyncio.sleep(5)


class TestWorkerProcessorTimeout:
    async def test_processor_timeout_sets_failed_with_timeout_error(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """batch_processor.process() outlasting batch_job_timeout_seconds ->
        asyncio.wait_for raises TimeoutError -> failed, error='timeout'."""
        app.state.batch_processor = _SlowProcessor()
        object.__setattr__(app.state.settings, "batch_job_timeout_seconds", 0.05)

        try:
            resp = await client.post(
                "/v1/batches",
                json={"line_items": [_line_item("timeout-1")]},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id = resp.json()["id"]

            await _await_all_batch_tasks(app)

            poll = await client.get(
                f"/v1/batches/{job_id}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            job = poll.json()
            assert job["status"] == "failed", f"expected failed, got: {job}"
            assert job["error"] == "timeout"
            assert job["status_counts"]["errored"] == 1
        finally:
            app.state.batch_processor = None
            object.__setattr__(app.state.settings, "batch_job_timeout_seconds", 300.0)


# ---------------------------------------------------------------------------
# Scenario: Configured processor raises an unexpected exception
# ---------------------------------------------------------------------------


class _RaisingProcessor:
    """Fake BatchProcessor whose process() raises a generic exception."""

    async def process(self, job_id: uuid.UUID) -> None:
        raise RuntimeError("adapter exploded")


class TestWorkerProcessorRaises:
    async def test_processor_exception_sets_failed_with_str_error(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """batch_processor.process() raising a generic Exception -> caught ->
        failed, error=str(exc)."""
        app.state.batch_processor = _RaisingProcessor()

        try:
            resp = await client.post(
                "/v1/batches",
                json={"line_items": [_line_item("raises-1")]},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200, f"expected 200: {resp.text}"
            job_id = resp.json()["id"]

            await _await_all_batch_tasks(app)

            poll = await client.get(
                f"/v1/batches/{job_id}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            job = poll.json()
            assert job["status"] == "failed", f"expected failed, got: {job}"
            assert job["error"] == "adapter exploded"
            assert job["status_counts"]["errored"] == 1
        finally:
            app.state.batch_processor = None


# ---------------------------------------------------------------------------
# Scenario: the outermost last-resort catch-all survives a double failure
# ---------------------------------------------------------------------------


class _BrokenSessionmaker:
    """Raises on every call — simulates a DB connection pool that is fully down,
    so even the outer except's OWN recovery attempt (set_failed) also fails."""

    def __call__(self) -> Any:
        raise RuntimeError("db connection pool exhausted")


class TestOutermostFailureNeverEscapes:
    async def test_process_batch_job_survives_sessionmaker_down(self) -> None:
        """_process_batch_job's module docstring guarantees 'ENTIRE body is wrapped
        in try/except — a raise NEVER escapes'. Drive that guarantee directly: even
        the very first sessionmaker() call (set_in_progress) raising is caught by the
        outer except, and that handler's own recovery write (set_failed) using the
        SAME broken sessionmaker also fails — caught by the innermost except — and
        the coroutine still returns normally instead of propagating."""
        from gateway.batches.api.router import _process_batch_job

        tasks_set: set[asyncio.Task[None]] = set()

        # Must complete without raising — that is the entire assertion.
        await _process_batch_job(
            job_id=uuid.uuid4(),
            sessionmaker=_BrokenSessionmaker(),
            batch_processor=None,
            timeout_seconds=1.0,
            tasks_set=tasks_set,
        )


class _FailsFirstCallSessionmaker:
    """Raises on its first call only — simulating a transient DB blip during
    set_in_progress — then delegates every subsequent call to the real
    sessionmaker, letting the outer except's OWN recovery write (set_failed)
    actually reach the database."""

    def __init__(self, real_sessionmaker: Any) -> None:
        self._real = real_sessionmaker
        self._calls = 0

    def __call__(self) -> Any:
        self._calls += 1
        if self._calls == 1:
            raise RuntimeError("db connection pool exhausted (transient)")
        return self._real()


class TestOutermostFailureRecoveryWriteSucceeds:
    async def test_recovery_write_marks_job_failed_when_set_in_progress_blips(
        self,
        client: Any,
        app: Any,
        api_key_info: dict[str, str],
    ) -> None:
        """TestOutermostFailureNeverEscapes only proves survival when EVERY
        sessionmaker() call fails — its own broken sessionmaker means the outer
        except's recovery write (set_failed, router.py) can never actually reach
        the database, so it cannot prove the write itself is correct. Here only
        the FIRST call (set_in_progress) blips; the recovery write uses the real
        sessionmaker and must actually persist status=failed, error=internal_error
        — proving the last-resort rollback write works, not just that it doesn't
        crash. Started from "validating" (set_in_progress never landed) rather
        than "in_progress", since set_failed's guard is any non-terminal status —
        assert the real final row state instead of assuming that holds."""
        from gateway.batches.api.router import _process_batch_job
        from gateway.batches.infrastructure.repository import BatchJobRepository

        async with app.state.sessionmaker() as session:
            repo = BatchJobRepository(session)
            job = await repo.create(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                line_items=[_line_item("outer-recovery-1")],
            )
            await session.commit()
        assert job.status == "validating"

        tasks_set: set[asyncio.Task[None]] = set()
        await _process_batch_job(
            job_id=job.id,
            sessionmaker=_FailsFirstCallSessionmaker(app.state.sessionmaker),
            batch_processor=None,
            timeout_seconds=1.0,
            tasks_set=tasks_set,
        )

        poll = await client.get(
            f"/v1/batches/{job.id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200
        polled = poll.json()
        assert polled["status"] == "failed", f"expected failed, got: {polled}"
        assert polled["error"] == "internal_error"
        assert polled["status_counts"]["errored"] == 1


# ---------------------------------------------------------------------------
# Scenario: Reject an empty line-items array
# ---------------------------------------------------------------------------


class TestRejectEmptyLineItems:
    async def test_reject_empty_line_items(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/batches",
            json={"line_items": []},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "batch_items_empty"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == [], "no batch_jobs row must be created on reject"


# ---------------------------------------------------------------------------
# Scenario: Reject a batch exceeding the item cap
# Scenario: Line-item count exactly at the cap boundary
# ---------------------------------------------------------------------------


class TestItemCap:
    async def test_reject_too_many_line_items(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """501 items exceeds the default cap (500) -> 422 batch_items_too_many."""
        items = [_line_item(f"cap-{i}") for i in range(501)]
        resp = await client.post(
            "/v1/batches",
            json={"line_items": items},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "batch_items_too_many"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == [], "no batch_jobs row must be created on reject"

    async def test_cap_boundary_exactly_500_succeeds(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """Exactly 500 items (the default cap) succeeds — the cap is inclusive (> not >=)."""
        items = [_line_item(f"cap-ok-{i}") for i in range(500)]
        resp = await client.post(
            "/v1/batches",
            json={"line_items": items},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200, f"expected 200 at cap boundary, got: {resp.text}"
        assert resp.json()["item_count"] == 500

        await _await_all_batch_tasks(app)


# ---------------------------------------------------------------------------
# Scenario: Reject a malformed line item
# ---------------------------------------------------------------------------


class TestRejectMalformedLineItem:
    async def test_reject_missing_model(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/batches",
            json={
                "line_items": [
                    _line_item("ok1"),
                    {
                        "custom_id": "bad1",
                        "body": {"messages": [{"role": "user", "content": "hi"}]},
                    },
                ]
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "batch_item_invalid"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == [], "the whole submission is atomic — never a partial job"

    async def test_reject_missing_messages(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/batches",
            json={
                "line_items": [
                    _line_item("ok1"),
                    {"custom_id": "bad2", "body": {"model": "test-model"}},
                ]
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "batch_item_invalid"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == [], "the whole submission is atomic — never a partial job"


# ---------------------------------------------------------------------------
# Scenario: Duplicate custom_id within one submission
# ---------------------------------------------------------------------------


class TestRejectDuplicateCustomId:
    async def test_reject_duplicate_custom_id(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("dupe"), _line_item("dupe")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        assert resp.json().get("code") == "batch_item_invalid"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == [], "no batch_jobs row must be created on reject"


# ---------------------------------------------------------------------------
# Scenario: Unknown or cross-tenant job id returns 404
# ---------------------------------------------------------------------------


class TestTenantIsolationPoll:
    async def test_cross_tenant_poll_404_no_oracle(
        self, client: Any, app: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """Tenant B cannot GET Tenant A's job — always 404, identical shape to unknown id."""
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("iso1")]},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        await _await_all_batch_tasks(app)

        poll_b = await client.get(
            f"/v1/batches/{job_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert poll_b.status_code == 404, (
            f"expected 404 from cross-tenant poll, got: {poll_b.status_code}"
        )
        cross_tenant_body = poll_b.json()
        assert cross_tenant_body.get("code") == "ERR_BATCH_JOB_NOT_FOUND"

        # Identical shape to a truly-unknown id — no oracle revealing cross-tenant existence.
        unknown = await client.get(
            f"/v1/batches/{uuid.uuid4()}",
            headers=_bearer(other_tenant["key"]),
        )
        assert unknown.status_code == 404
        assert unknown.json() == cross_tenant_body or unknown.json().get(
            "code"
        ) == cross_tenant_body.get("code")

        # Tenant A can still see it.
        poll_a = await client.get(
            f"/v1/batches/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll_a.status_code == 200


# ---------------------------------------------------------------------------
# Scenario: Missing or invalid API key is rejected
# ---------------------------------------------------------------------------


class TestMissingOrInvalidApiKey401:
    async def test_no_bearer_returns_401(self, client: Any) -> None:
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("noauth")]},
        )
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(self, client: Any) -> None:
        resp = await client.post(
            "/v1/batches",
            json={"line_items": [_line_item("badauth")]},
            headers={"Authorization": "Bearer sk-invalid-key"},
        )
        assert resp.status_code == 401

    async def test_get_without_bearer_returns_401(self, client: Any) -> None:
        resp = await client.get(f"/v1/batches/{uuid.uuid4()}")
        assert resp.status_code == 401

    async def test_list_without_bearer_returns_401(self, client: Any) -> None:
        resp = await client.get("/v1/batches")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Scenario: Malformed request body is rejected before any row is created
# ---------------------------------------------------------------------------


class TestMalformedRequestBody422:
    async def test_non_json_body_422(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/batches",
            content=b"{not valid json at all",
            headers={**_bearer(api_key_info["key"]), "Content-Type": "application/json"},
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == []

    async def test_line_items_wrong_type_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/batches",
            json={"line_items": "not-an-array"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"

        jobs = await _list_batch_jobs(client, api_key_info["key"])
        assert jobs == []
