"""Red/Green test suite for gateway/video domain.

Contract under test:
  POST /v1/video/generations  {model, prompt, params?}
       -> 200 {id, status:"queued", model, prompt, result_artifact_id:null, error:null, created_at, updated_at}
       -> 422 ERR_PAYLOAD_MODEL_REQUIRED  (missing/empty model)
       -> 422 ERR_PAYLOAD_INPUT_REQUIRED  (missing/empty prompt)
       -> 401 (missing/invalid/expired key)
  GET  /v1/video/generations/{id}
       -> 200 {id, status, model, prompt, result_artifact_id, error, created_at, updated_at}
       -> 404 ERR_VIDEO_JOB_NOT_FOUND  (missing OR cross-tenant)
  GET  /v1/video/generations?limit&offset
       -> 200 {jobs:[...]}  tenant-scoped, newest-first

TENANT-ISOLATION INVARIANT: cross-tenant job id → 404 — never reveal another tenant's job.
HONEST DEGRADATION: when app.state.video_generator is None → status=failed, error="no_video_provider_configured".
DESIGN-FOR-FAILURE: provider error → failed, provider timeout → failed; no crash/unhandled exception.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from gateway.keys.infrastructure.orm import ApiKeyRow

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _await_all_video_tasks(app: Any) -> None:
    """Wait for all tracked video job tasks to complete before asserting terminal state."""
    tasks: set[asyncio.Task[None]] = getattr(app.state, "video_jobs_tasks", set())
    if tasks:
        # Copy because tasks may remove themselves from the set on completion
        pending = set(tasks)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Stub VideoGenerator
# ---------------------------------------------------------------------------


class _SuccessStub:
    """Returns a tiny fake video payload."""

    def __init__(self, content: bytes = b"FAKE_VIDEO_BYTES", content_type: str = "video/mp4") -> None:
        self._content = content
        self._content_type = content_type

    async def generate(self, prompt: str, model: str, params: dict[str, Any] | None) -> tuple[bytes, str]:
        return self._content, self._content_type


class _ErrorStub:
    """Raises on generate."""

    async def generate(self, prompt: str, model: str, params: dict[str, Any] | None) -> tuple[bytes, str]:
        raise RuntimeError("provider exploded")


class _TimeoutStub:
    """Hangs forever so asyncio.wait_for can time it out."""

    async def generate(self, prompt: str, model: str, params: dict[str, Any] | None) -> tuple[bytes, str]:
        await asyncio.sleep(9999)
        return b"", "video/mp4"  # unreachable


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Primary tenant fixture."""
    return await _signup_and_key(
        client,
        tenant_name="VideoTest",
        email="video-test@example.io",
        password="video-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    """A SECOND tenant — used for cross-tenant isolation checks."""
    return await _signup_and_key(
        client,
        tenant_name="VideoOther",
        email="video-other@example.io",
        password="video-other-battery",
    )


# ---------------------------------------------------------------------------
# test_submit_returns_queued
# ---------------------------------------------------------------------------


class TestSubmitReturnsQueued:
    async def test_submit_returns_queued(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST /v1/video/generations returns 200 with status=queued immediately."""
        # Install a success stub so the background task doesn't stall
        app.state.video_generator = _SuccessStub()

        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "a sunset"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "id" in body
        assert body["status"] == "queued"
        assert body["model"] == "test-model"
        assert body["prompt"] == "a sunset"
        assert body["result_artifact_id"] is None
        assert body["error"] is None
        assert "created_at" in body
        assert "updated_at" in body

        # Clean up
        await _await_all_video_tasks(app)
        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test_job_succeeds_and_downloadable
# ---------------------------------------------------------------------------


class TestJobSucceedsAndDownloadable:
    async def test_job_succeeds_and_downloadable(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """On provider success: job transitions to succeeded, artifact downloadable via /v1/artifacts."""
        fake_bytes = b"FAKE_MP4_VIDEO_DATA"
        app.state.video_generator = _SuccessStub(content=fake_bytes, content_type="video/mp4")

        # Submit
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "ocean waves"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        # Wait for task
        await _await_all_video_tasks(app)

        # Poll — expect succeeded
        poll = await client.get(
            f"/v1/video/generations/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200, f"poll failed: {poll.text}"
        job = poll.json()
        assert job["status"] == "succeeded", f"expected succeeded, got: {job}"
        assert job["result_artifact_id"] is not None
        assert job["error"] is None

        # Download the artifact via the existing /v1/artifacts route
        artifact_id = job["result_artifact_id"]
        dl = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert dl.status_code == 200, f"artifact download failed: {dl.text}"
        assert dl.content == fake_bytes
        assert dl.headers["content-type"].startswith("video/mp4")

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test_no_provider_honest_failure
# ---------------------------------------------------------------------------


class TestNoProviderHonestFailure:
    async def test_no_provider_honest_failure(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """When video_generator is None: job status=failed, error='no_video_provider_configured', no artifact."""
        # Ensure no provider
        app.state.video_generator = None

        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "mountains"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        await _await_all_video_tasks(app)

        poll = await client.get(
            f"/v1/video/generations/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200
        job = poll.json()
        assert job["status"] == "failed", f"expected failed, got: {job}"
        assert job["error"] == "no_video_provider_configured"
        assert job["result_artifact_id"] is None


# ---------------------------------------------------------------------------
# test_provider_error_failed
# ---------------------------------------------------------------------------


class TestProviderErrorFailed:
    async def test_provider_error_failed(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """When provider raises: job status=failed, error=str(exc), no 500 crash."""
        app.state.video_generator = _ErrorStub()

        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "fire"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        await _await_all_video_tasks(app)

        poll = await client.get(
            f"/v1/video/generations/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll.status_code == 200
        job = poll.json()
        assert job["status"] == "failed", f"expected failed, got: {job}"
        assert "provider exploded" in (job["error"] or "")
        assert job["result_artifact_id"] is None

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test_provider_timeout_failed
# ---------------------------------------------------------------------------


class TestProviderTimeoutFailed:
    async def test_provider_timeout_failed(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """When provider times out: job status=failed, error='timeout', no crash."""
        # Set a very short timeout so the stub times out immediately
        original_timeout = app.state.settings.video_job_timeout_seconds
        object.__setattr__(app.state.settings, "video_job_timeout_seconds", 0.01)
        app.state.video_generator = _TimeoutStub()

        try:
            resp = await client.post(
                "/v1/video/generations",
                json={"model": "test-model", "prompt": "waterfall"},
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 200
            job_id = resp.json()["id"]

            await _await_all_video_tasks(app)

            poll = await client.get(
                f"/v1/video/generations/{job_id}",
                headers=_bearer(api_key_info["key"]),
            )
            assert poll.status_code == 200
            job = poll.json()
            assert job["status"] == "failed", f"expected failed, got: {job}"
            assert job["error"] == "timeout"
            assert job["result_artifact_id"] is None
        finally:
            object.__setattr__(app.state.settings, "video_job_timeout_seconds", original_timeout)
            app.state.video_generator = None


# ---------------------------------------------------------------------------
# test_tenant_isolation_poll
# ---------------------------------------------------------------------------


class TestTenantIsolationPoll:
    async def test_tenant_isolation_poll(
        self, client: Any, app: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """Cross-tenant: Tenant B cannot GET Tenant A's job — always 404."""
        app.state.video_generator = _SuccessStub()

        # Tenant A submits a job
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "isolation test"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]

        await _await_all_video_tasks(app)

        # Tenant B polls → 404 (cross-tenant — never reveal)
        poll_b = await client.get(
            f"/v1/video/generations/{job_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert poll_b.status_code == 404, f"expected 404 from cross-tenant poll, got: {poll_b.status_code}"
        assert poll_b.json().get("code") == "ERR_VIDEO_JOB_NOT_FOUND"

        # Tenant A can still see it
        poll_a = await client.get(
            f"/v1/video/generations/{job_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert poll_a.status_code == 200

        app.state.video_generator = None


# ---------------------------------------------------------------------------
# test_missing_model_422
# ---------------------------------------------------------------------------


class TestMissingModel422:
    async def test_missing_model_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST without model field → 422 ERR_PAYLOAD_MODEL_REQUIRED."""
        resp = await client.post(
            "/v1/video/generations",
            json={"prompt": "no model here"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("code") == "ERR_PAYLOAD_INVALID" or "model" in str(body).lower()

    async def test_empty_model_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST with empty model → 422."""
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "  ", "prompt": "test"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# test_missing_prompt_422
# ---------------------------------------------------------------------------


class TestMissingPrompt422:
    async def test_missing_prompt_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST without prompt → 422 ERR_PAYLOAD_INPUT_REQUIRED."""
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body.get("code") == "ERR_PAYLOAD_INVALID" or "prompt" in str(body).lower()

    async def test_empty_prompt_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """POST with empty prompt → 422."""
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": ""},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422, f"expected 422, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# test_missing_key_401
# ---------------------------------------------------------------------------


class TestMissingKey401:
    async def test_no_bearer_returns_401(self, client: Any) -> None:
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "test"},
        )
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(self, client: Any) -> None:
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "test"},
            headers={"Authorization": "Bearer sk-invalid-key"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# test_expired_key_401
# ---------------------------------------------------------------------------


class TestExpiredKey401:
    async def test_expired_key_returns_401(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """Expired (but not revoked) key must be rejected with 401."""
        # Sanity: key works before expiry
        ok = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "works"},
            headers=_bearer(api_key_info["key"]),
        )
        assert ok.status_code == 200

        # Clean up created task
        await _await_all_video_tasks(app)

        # Force-expire in DB
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.id == uuid.UUID(api_key_info["key_id"]))
                .values(expires_at=datetime.now(tz=UTC) - timedelta(hours=1))
            )
            await session.commit()

        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "post-expiry"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# test_list_tenant_scoped_newest_first
# ---------------------------------------------------------------------------


class TestListTenantScopedNewestFirst:
    async def test_list_tenant_scoped_newest_first(
        self, client: Any, app: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """GET /v1/video/generations: tenant-scoped list, newest first, no cross-tenant leak."""
        app.state.video_generator = _SuccessStub()

        # Tenant A submits 2 jobs
        r1 = await client.post(
            "/v1/video/generations",
            json={"model": "model-a", "prompt": "first"},
            headers=_bearer(api_key_info["key"]),
        )
        assert r1.status_code == 200
        job_id_1 = r1.json()["id"]

        r2 = await client.post(
            "/v1/video/generations",
            json={"model": "model-b", "prompt": "second"},
            headers=_bearer(api_key_info["key"]),
        )
        assert r2.status_code == 200
        job_id_2 = r2.json()["id"]

        # Tenant B submits 1 job
        r3 = await client.post(
            "/v1/video/generations",
            json={"model": "model-c", "prompt": "other-tenant"},
            headers=_bearer(other_tenant["key"]),
        )
        assert r3.status_code == 200

        await _await_all_video_tasks(app)

        # Tenant A lists — sees only their 2 jobs
        list_resp = await client.get(
            "/v1/video/generations",
            headers=_bearer(api_key_info["key"]),
        )
        assert list_resp.status_code == 200, f"list failed: {list_resp.text}"
        body = list_resp.json()
        assert "jobs" in body
        jobs = body["jobs"]
        assert len(jobs) == 2

        job_ids = [j["id"] for j in jobs]
        assert job_id_1 in job_ids
        assert job_id_2 in job_ids

        # Newest first: job_id_2 should be index 0 (submitted later)
        # (created_at DESC means more recently created jobs appear first)
        assert jobs[0]["id"] == job_id_2

        # Items must NOT contain video bytes
        for j in jobs:
            assert "content" not in j
            assert "bytes" not in j

        # Tenant B lists — sees only their 1 job
        list_resp_b = await client.get(
            "/v1/video/generations",
            headers=_bearer(other_tenant["key"]),
        )
        assert list_resp_b.status_code == 200
        jobs_b = list_resp_b.json()["jobs"]
        assert len(jobs_b) == 1

        app.state.video_generator = None

    async def test_list_job_not_found_unknown_id(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """GET /v1/video/generations/{unknown-id} → 404."""
        fake_id = str(uuid.uuid4())
        resp = await client.get(
            f"/v1/video/generations/{fake_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404
        assert resp.json().get("code") == "ERR_VIDEO_JOB_NOT_FOUND"


# ---------------------------------------------------------------------------
# Terminal-status idempotency: a re-driven set_running must NOT clobber a
# terminal job (protects the durable-worker delta from corrupting results).
# ---------------------------------------------------------------------------


class TestTerminalStatusIdempotent:
    async def test_set_running_does_not_clobber_terminal(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """Once a job is succeeded, a stray set_running leaves it succeeded."""
        from gateway.video.infrastructure.repository import VideoJobRepository

        app.state.video_generator = _SuccessStub(content=b"V", content_type="video/mp4")
        resp = await client.post(
            "/v1/video/generations",
            json={"model": "test-model", "prompt": "p"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        await _await_all_video_tasks(app)

        poll = await client.get(
            f"/v1/video/generations/{job_id}", headers=_bearer(api_key_info["key"])
        )
        assert poll.json()["status"] == "succeeded"

        # Simulate a re-driven / duplicate worker calling set_running on a
        # terminal job — the status guard must make it a no-op.
        async with app.state.sessionmaker() as session:
            repo = VideoJobRepository(session)
            await repo.set_running(job_id=uuid.UUID(job_id))
            await session.commit()

        poll2 = await client.get(
            f"/v1/video/generations/{job_id}", headers=_bearer(api_key_info["key"])
        )
        assert poll2.json()["status"] == "succeeded"  # unchanged — guard held
        assert poll2.json()["result_artifact_id"] is not None

        app.state.video_generator = None
