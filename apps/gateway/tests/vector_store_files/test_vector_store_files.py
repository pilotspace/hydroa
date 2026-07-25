"""RED suite for vector-store-files (async attach: enqueue -> background worker ingests).

Contract under test (vector-store-files PLAN.md §3, DRAFT — Tin-directed ASYNC model):
  POST /v1/vector_stores/{vsid}/files   {file_id: "file-<32hex>"}   NON-BLOCKING
    200 -> { id:"file-<32hex>", object:"vector_store.file", created_at:<unix int>,
             vector_store_id:"vs_<32hex>", usage_bytes, status:"in_progress", last_error }
    (validates + inserts the row + enqueues; NO chunk/embed work in the request path)
    422 ERR_VECTOR_STORE_FILE_ID_REQUIRED · 400 ERR_VECTOR_STORE_FILE_PURPOSE_INVALID
    403 ERR_ZDR_PAYLOAD_BLOCKED · 404 ERR_VECTOR_STORE_NOT_FOUND | ERR_FILE_NOT_FOUND
  BACKGROUND: VectorStoreIngestWorker (batches/video durable-queue shape: Redis
    LPUSH/BRPOP + run_forever/process_once/recover_orphans) chunks -> ONE batched embed
    via the ChunkEmbedder port -> atomic finalize tx (chunks + CAS in_progress->completed)
    or CAS in_progress->failed(last_error) — never a half-written index.
  GET  /v1/vector_stores/{vsid}/files/{file_id} -> current status | 404 (uniform)
  GET  /v1/vector_stores/{vsid}/files           -> {object:"list", data, has_more}

Tests drive the worker DETERMINISTICALLY via worker.process_once() — the exact test
seam the batches suite uses (tests/batches/test_batch_durable_queue.py) — never a
sleep/poll race. The embed leg is a fake at ``app.state.vector_store_embedder``.

INVARIANTS under test:
  NON-BLOCKING — attach returns in_progress with the embedder UNCALLED, zero chunks.
  ZDR      — 403 at attach; raise_if_zdr FIRST in the worker chunk-write path
             (ZDR flipped after attach -> failed/zdr_blocked, zero chunks, fail-closed).
  TENANT   — cross-tenant store OR file -> uniform 404, never a leak.
  IDEMPOTENT — UNIQUE(store,file): re-attach returns the same row; retry only from failed.
  CLAIM    — two workers, one job: BRPOP hands the id to exactly one; no double chunks.
  BILLING  — exactly ONE usage_records row per COMPLETED ingest (CAS-gated); zero on failure.

RED until the ingestion worker module + files sub-router are wired. Every test targets
a route/module that does not exist yet, so the suite fails for the RIGHT reason
(missing implementation: 404-no-route / ModuleNotFoundError), not a broken harness.
DO NOT edit to make pass — that is Build's job.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gateway.proxy.domain.errors import UpstreamUnavailableError

pytestmark = pytest.mark.asyncio

_DIM = 1536


# ---------------------------------------------------------------------------
# Helpers / fixtures (mirror tests/vector_store_core + tests/batches durable-queue)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
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
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="VsfA", email="vsf-a@example.io", password="vsf-a-battery"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="VsfB", email="vsf-b@example.io", password="vsf-b-battery"
    )


class FakeChunkEmbedder:
    """Deterministic ChunkEmbedder port fake — one call per file, dim-1536 vectors.

    ``fail_next`` raises UpstreamUnavailableError once (the port's contracted
    failure type) then succeeds — used by the failed-then-retry tests.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_next = False

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        if self.fail_next:
            self.fail_next = False
            raise UpstreamUnavailableError("embeddings upstream down (fake)")
        self.calls.append({"tenant_id": tenant_id, "model": model, "texts": list(texts)})
        vectors = [[float((i + 1) % 7) / 7.0] * _DIM for i in range(len(texts))]
        n_tokens = sum(len(t.split()) for t in texts)
        return vectors, {"prompt_tokens": n_tokens, "total_tokens": n_tokens}


@pytest.fixture
def fake_embedder(app: Any) -> FakeChunkEmbedder:
    fake = FakeChunkEmbedder()
    app.state.vector_store_embedder = fake
    return fake


class SlowChunkEmbedder:
    """A ChunkEmbedder whose ``embed()`` awaits ``asyncio.sleep`` — simulates the
    real HTTP round-trip (5s connect + 30s read in prod) so a concurrently-running
    coroutine has an actual window to flip ``tenants.zdr_enabled`` mid-flight. The
    instant ``FakeChunkEmbedder`` above has ZERO such window and cannot reproduce
    the ZDR TOCTOU security defect this suite now guards against."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.calls: list[dict[str, Any]] = []

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        await asyncio.sleep(self.delay)
        self.calls.append({"tenant_id": tenant_id, "model": model, "texts": list(texts)})
        vectors = [[float((i + 1) % 7) / 7.0] * _DIM for i in range(len(texts))]
        n_tokens = sum(len(t.split()) for t in texts)
        return vectors, {"prompt_tokens": n_tokens, "total_tokens": n_tokens}


def _build_worker(app: Any) -> Any:
    """Construct queue + worker from app.state — the batches-suite fixture shape.

    Imported INSIDE the helper so each async-lifecycle test fails individually with
    ModuleNotFoundError (missing implementation), not a collection-time error.
    Wires the queue onto app.state so the router's attach enqueue targets the same
    Redis list the worker drains (mirrors app.state.batch_job_queue).
    """
    from gateway.vector_stores.application.ingest_worker import (  # noqa: PLC0415
        RedisVectorStoreIngestQueue,
        VectorStoreIngestWorker,
    )

    queue = RedisVectorStoreIngestQueue(app.state.redis_client)
    app.state.vector_store_ingest_queue = queue
    return VectorStoreIngestWorker(
        sessionmaker=app.state.sessionmaker,
        queue=queue,
        settings=app.state.settings,
        get_embedder=lambda: getattr(app.state, "vector_store_embedder", None),
    )


async def _upload(
    client: Any,
    key: str,
    *,
    filename: str = "kb.txt",
    data: bytes = b"hydroa retrieval corpus " * 300,  # ~7.2KB -> >1 chunk at 2400 chars
    purpose: str = "assistants",
) -> str:
    resp = await client.post(
        "/v1/files",
        files={"file": (filename, data, "text/plain")},
        data={"purpose": purpose},
        headers=_bearer(key),
    )
    assert resp.status_code == 200, f"file upload failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _create_store(client: Any, key: str, name: str = "kb-main") -> str:
    resp = await client.post("/v1/vector_stores", json={"name": name}, headers=_bearer(key))
    assert resp.status_code == 200, f"store create failed: {resp.status_code} {resp.text}"
    return str(resp.json()["id"])


async def _attach(client: Any, key: str, vsid: str, file_id: Any) -> Any:
    body: dict[str, Any] = {} if file_id is None else {"file_id": file_id}
    return await client.post(
        f"/v1/vector_stores/{vsid}/files", json=body, headers=_bearer(key)
    )


async def _get_file(client: Any, key: str, vsid: str, file_id: str) -> Any:
    return await client.get(
        f"/v1/vector_stores/{vsid}/files/{file_id}", headers=_bearer(key)
    )


async def _set_tenant_zdr(app: Any, tenant_id: str) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
            {"tid": tenant_id},
        )
        await session.commit()


async def _count(app: Any, table: str, tenant_id: str) -> int:
    async with app.state.sessionmaker() as session:
        result = await session.execute(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tid"),  # noqa: S608
            {"tid": tenant_id},
        )
        return int(result.scalar_one())


async def _drain(worker: Any) -> None:
    """Drain the ingest queue deterministically (process_once until empty)."""
    while await worker.process_once():
        pass


async def _vsf_row_id(app: Any, *, tenant_id: str, vsid: str, file_id: str) -> uuid.UUID:
    """Resolve the internal vector_store_files.id (the ingest job row id) — the
    wire-visible ``id`` on the vector_store.file object is the FILE's own wire id,
    never this row's internal id."""
    from gateway.files.wire_id import parse_wire_id as _parse_file_wire_id
    from gateway.vector_stores.wire_id import parse_wire_id as _parse_vs_wire_id

    async with app.state.sessionmaker() as session:
        result = await session.execute(
            text(
                "SELECT id FROM vector_store_files"
                " WHERE tenant_id = :tid AND vector_store_id = :vsid AND file_id = :fid"
            ),
            {
                "tid": tenant_id,
                "vsid": str(_parse_vs_wire_id(vsid)),
                "fid": str(_parse_file_wire_id(file_id)),
            },
        )
        return result.scalar_one()


# ---------------------------------------------------------------------------
# M1 — attach is NON-BLOCKING: 200 in_progress immediately, no embed work inline
# ---------------------------------------------------------------------------


async def test_attach_returns_in_progress_immediately(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M1 — 200 + status in_progress; embedder UNCALLED; zero chunks at return."""
    _build_worker(app)  # wires the queue the router enqueues to
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])

    resp = await _attach(client, tenant_a["key"], vsid, file_id)
    assert resp.status_code == 200, f"attach failed: {resp.status_code} {resp.text}"
    obj = resp.json()
    assert obj["object"] == "vector_store.file"
    assert obj["id"] == file_id
    assert obj["vector_store_id"] == vsid
    assert obj["status"] == "in_progress", "attach must NOT ingest inline"
    assert obj["last_error"] is None
    assert isinstance(obj["created_at"], int)

    # NON-BLOCKING invariants: the request path did zero embed/chunk work.
    assert fake_embedder.calls == [], "embed must happen in the worker, not the request"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M2 — the background worker ingests: chunk -> embed -> index -> completed (atomic)
# ---------------------------------------------------------------------------


async def test_worker_completes_ingestion(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M2 — process_once() drives the job to completed with dim-1536 chunks."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    drained = await worker.process_once()
    assert drained is True, "the attach must have enqueued exactly one job"

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "completed", f"worker did not complete: {obj}"
    assert obj["usage_bytes"] > 0

    # exactly ONE batched embed call carried every chunk text
    assert len(fake_embedder.calls) == 1
    n_chunks = len(fake_embedder.calls[0]["texts"])
    assert n_chunks >= 2, "a 7KB file must produce >1 chunk at 2400 chars"

    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tenant_id::text, vector_store_id::text, chunk_index,"
                    " vector_dims(embedding) AS dim FROM vector_store_chunks"
                    " WHERE tenant_id = :tid ORDER BY chunk_index"
                ),
                {"tid": tenant_a["tenant_id"]},
            )
        ).mappings().all()
        assert len(rows) == n_chunks
        assert all(r["dim"] == _DIM for r in rows)
        assert all(r["tenant_id"] == tenant_a["tenant_id"] for r in rows)
        # wave-3 extension point: the frozen HNSW cosine substrate answers top-k NOW,
        # zero schema change, ZERO status join (chunk existence implies completed).
        probe = "[" + ",".join(["0.1"] * _DIM) + "]"
        topk = (
            await session.execute(
                text(
                    "SELECT chunk_index FROM vector_store_chunks"
                    " WHERE tenant_id = :tid ORDER BY embedding <=> :q LIMIT 3"
                ),
                {"tid": tenant_a["tenant_id"], "q": probe},
            )
        ).all()
        assert 1 <= len(topk) <= 3

    # parent store object reflects the ingest
    store = (
        await client.get(f"/v1/vector_stores/{vsid}", headers=_bearer(tenant_a["key"]))
    ).json()
    assert store["usage_bytes"] > 0


# ---------------------------------------------------------------------------
# M7/M2 — idempotent re-attach (no re-ingest of a completed row, no double bill)
# ---------------------------------------------------------------------------


async def test_reattach_is_idempotent(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M7, M2 — re-attach of a completed row: same id, no re-embed, one bill."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    first = (await _attach(client, tenant_a["key"], vsid, file_id)).json()
    await worker.process_once()
    chunks_after_first = await _count(app, "vector_store_chunks", tenant_a["tenant_id"])

    second_resp = await _attach(client, tenant_a["key"], vsid, file_id)
    assert second_resp.status_code == 200
    second = second_resp.json()
    assert second["id"] == first["id"]
    assert second["status"] == "completed", "re-attach returns the CURRENT status"

    # a completed row may have been re-enqueued (healing semantics) — drain the queue;
    # the worker's terminal-skip + CAS finalize must make it a no-op.
    await _drain(worker)
    assert len(fake_embedder.calls) == 1, "a completed row must NOT be re-embedded"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == chunks_after_first
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# M3 / M4 — pollable status + list
# ---------------------------------------------------------------------------


async def test_get_status_returns_object(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M3 — GET shows in_progress BEFORE the worker runs, completed after."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    before = await _get_file(client, tenant_a["key"], vsid, file_id)
    assert before.status_code == 200, f"get failed: {before.status_code} {before.text}"
    assert before.json()["status"] == "in_progress"
    assert before.json()["object"] == "vector_store.file"

    await worker.process_once()

    after = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert after["status"] == "completed"

    malformed = await _get_file(client, tenant_a["key"], vsid, "not-a-file-id")
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "ERR_FILE_NOT_FOUND"


async def test_list_files_newest_first(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M4 — list envelope, newest first, has_more via probe."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    f1 = await _upload(client, tenant_a["key"], filename="one.txt", data=b"alpha " * 50)
    f2 = await _upload(client, tenant_a["key"], filename="two.txt", data=b"beta " * 50)
    await _attach(client, tenant_a["key"], vsid, f1)
    await _attach(client, tenant_a["key"], vsid, f2)
    await _drain(worker)

    resp = await client.get(
        f"/v1/vector_stores/{vsid}/files", headers=_bearer(tenant_a["key"])
    )
    assert resp.status_code == 200, f"list failed: {resp.status_code} {resp.text}"
    body = resp.json()
    assert body["object"] == "list"
    assert body["has_more"] is False
    ids = [row["id"] for row in body["data"]]
    assert ids == [f2, f1], "newest first"


# ---------------------------------------------------------------------------
# M5 — ZDR fail-closed at BOTH gates: attach entry AND the worker chunk-write path
# ---------------------------------------------------------------------------


async def test_attach_zdr_tenant_403_zero_residue(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M5, R:zdr_payload_blocked — 403 + NO vsf row + NO chunks + NO embed call."""
    _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _set_tenant_zdr(app, tenant_a["tenant_id"])

    resp = await _attach(client, tenant_a["key"], vsid, file_id)
    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text}"
    assert "ERR_ZDR_PAYLOAD_BLOCKED" in resp.text
    assert await _count(app, "vector_store_files", tenant_a["tenant_id"]) == 0
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0
    assert fake_embedder.calls == []


async def test_worker_zdr_flipped_after_attach_fails_closed(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M5 — ZDR flips ON between attach and drive: failed/zdr_blocked, 0 chunks.

    raise_if_zdr is the FIRST line of the worker's chunk-write path — a ZDR tenant's
    queued ingestion must fail-closed, never silently store payload at rest.
    """
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)
    await _set_tenant_zdr(app, tenant_a["tenant_id"])

    await worker.process_once()

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "failed", f"ZDR ingest must fail closed: {obj}"
    assert obj["last_error"] is not None
    assert obj["last_error"]["code"] == "zdr_blocked"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M6 — exactly one usage record per COMPLETED ingest (CAS-gated, duplicate-drive safe)
# ---------------------------------------------------------------------------


async def test_attach_records_exactly_one_usage_record(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M6 — one usage_records row (model_id = embedding model); re-drive adds none."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)
    await worker.process_once()

    async with app.state.sessionmaker() as session:
        rows = (
            await session.execute(
                text("SELECT model_id FROM usage_records WHERE tenant_id = :tid"),
                {"tid": tenant_a["tenant_id"]},
            )
        ).all()
    assert len(rows) == 1, f"expected exactly one usage record, got {len(rows)}"
    assert rows[0][0] == "text-embedding-3-small"

    # re-attach re-enqueues (healing semantics); the terminal skip + CAS-gated
    # usage record must keep the bill at exactly one after a full drain.
    second = await _attach(client, tenant_a["key"], vsid, file_id)
    assert second.status_code == 200
    await _drain(worker)
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# M7 — honest worker failure + retry via re-attach
# ---------------------------------------------------------------------------


async def test_embed_failure_marks_failed_no_chunks(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M7 — upstream down in the WORKER -> failed + last_error, 0 chunks, 0 bills."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    fake_embedder.fail_next = True

    attach = (await _attach(client, tenant_a["key"], vsid, file_id)).json()
    assert attach["status"] == "in_progress", "attach itself never fails on embed outage"
    await worker.process_once()

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "failed"
    assert obj["last_error"] is not None
    assert obj["last_error"]["code"] == "embedding_unavailable"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 0


async def test_reattach_after_failure_retries(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M7 — re-attach CAS-flips failed->in_progress, re-enqueues, completes."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    fake_embedder.fail_next = True
    await _attach(client, tenant_a["key"], vsid, file_id)
    await worker.process_once()
    failed = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert failed["status"] == "failed"

    retry = (await _attach(client, tenant_a["key"], vsid, file_id)).json()
    assert retry["status"] == "in_progress", "re-attach of failed row restarts the job"
    await worker.process_once()

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "completed"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) > 0


# ---------------------------------------------------------------------------
# M2/M6 — claim safety: two workers never double-process one job
# ---------------------------------------------------------------------------


async def test_two_workers_never_double_process(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M2, M6 — BRPOP hands one job to exactly one worker; one bill, no dup chunks.

    Mirrors tests/batches/test_batch_durable_queue.py::test_two_workers_never_double_process.
    """
    worker_1 = _build_worker(app)
    worker_2 = _build_worker(app)  # same queue key, second consumer
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    claimed_1 = await worker_1.process_once()
    claimed_2 = await worker_2.process_once()
    assert claimed_1 is True
    assert claimed_2 is False, "queue must be empty for the second worker"

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "completed"
    assert len(fake_embedder.calls) == 1, "exactly one worker embedded the file"
    n_chunks = len(fake_embedder.calls[0]["texts"])
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == n_chunks
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 1


# ---------------------------------------------------------------------------
# M8 — the store object's file_counts go live
# ---------------------------------------------------------------------------


async def test_store_file_counts_live(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: M8 — GET /v1/vector_stores/{id} counts the attached file post-ingest."""
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)
    await worker.process_once()

    store = (
        await client.get(f"/v1/vector_stores/{vsid}", headers=_bearer(tenant_a["key"]))
    ).json()
    assert store["file_counts"]["completed"] == 1
    assert store["file_counts"]["total"] == 1


# ---------------------------------------------------------------------------
# M9 — /v1/files additively accepts purpose=assistants
# ---------------------------------------------------------------------------


async def test_upload_purpose_assistants_accepted(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M9 — purpose 'assistants' joins the vocabulary additively (200)."""
    resp = await client.post(
        "/v1/files",
        files={"file": ("kb.txt", b"hello corpus", "text/plain")},
        data={"purpose": "assistants"},
        headers=_bearer(tenant_a["key"]),
    )
    assert resp.status_code == 200, f"upload failed: {resp.status_code} {resp.text}"
    assert resp.json()["purpose"] == "assistants"


# ---------------------------------------------------------------------------
# M10 — the production embedder adapter keeps a PER-TENANT breaker
# ---------------------------------------------------------------------------


async def test_embedder_breaker_is_per_tenant() -> None:
    """covers: M10 — two tenants never share a CircuitBreaker (moderations CR-1 / finetune D1)."""
    from gateway.vector_stores.infrastructure.embedding_client import (  # noqa: PLC0415
        VectorStoreEmbeddingClient,
    )

    adapter = VectorStoreEmbeddingClient()
    t1, t2 = uuid.uuid4(), uuid.uuid4()
    b1 = adapter._breaker_for(t1)  # noqa: SLF001 — structural invariant probe
    b2 = adapter._breaker_for(t2)  # noqa: SLF001
    assert b1 is not b2, "breaker MUST be per-tenant, never shared"
    assert adapter._breaker_for(t1) is b1  # noqa: SLF001 — stable per tenant


# ---------------------------------------------------------------------------
# Rejects — file_id required · unknown/cross-tenant file · cross-tenant store · purpose
# ---------------------------------------------------------------------------


async def test_attach_missing_file_id_422(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: R:vector_store_file_id_required — {} body -> 422, nothing persisted."""
    vsid = await _create_store(client, tenant_a["key"])
    resp = await _attach(client, tenant_a["key"], vsid, None)
    assert resp.status_code == 422, f"expected 422, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_VECTOR_STORE_FILE_ID_REQUIRED"
    assert await _count(app, "vector_store_files", tenant_a["tenant_id"]) == 0


async def test_attach_unknown_or_malformed_file_404(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: R:file_not_found — absent AND malformed file ids -> ONE uniform 404."""
    vsid = await _create_store(client, tenant_a["key"])
    for bad in (f"file-{uuid.uuid4().hex}", "not-a-file-id"):
        resp = await _attach(client, tenant_a["key"], vsid, bad)
        assert resp.status_code == 404, f"{bad}: {resp.status_code} {resp.text}"
        assert resp.json()["error"]["code"] == "ERR_FILE_NOT_FOUND"
    assert await _count(app, "vector_store_files", tenant_a["tenant_id"]) == 0


async def test_attach_cross_tenant_file_404(
    client: Any,
    app: Any,
    tenant_a: dict[str, str],
    tenant_b: dict[str, str],
    fake_embedder: FakeChunkEmbedder,
) -> None:
    """covers: R:file_not_found — tenant A attaching B's file is 404, zero residue."""
    vsid = await _create_store(client, tenant_a["key"])
    file_b = await _upload(client, tenant_b["key"])
    resp = await _attach(client, tenant_a["key"], vsid, file_b)
    assert resp.status_code == 404, f"expected 404, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_FILE_NOT_FOUND"
    assert await _count(app, "vector_store_files", tenant_a["tenant_id"]) == 0
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0


async def test_attach_cross_tenant_store_404(
    client: Any,
    app: Any,
    tenant_a: dict[str, str],
    tenant_b: dict[str, str],
    fake_embedder: FakeChunkEmbedder,
) -> None:
    """covers: R:vector_store_not_found — A's file into B's store -> 404, B untouched."""
    store_b = await _create_store(client, tenant_b["key"])
    file_a = await _upload(client, tenant_a["key"])

    resp = await _attach(client, tenant_a["key"], store_b, file_a)
    assert resp.status_code == 404, f"expected 404, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_VECTOR_STORE_NOT_FOUND"
    assert await _count(app, "vector_store_files", tenant_b["tenant_id"]) == 0

    malformed = await _attach(client, tenant_a["key"], "vs_zz", file_a)
    assert malformed.status_code == 404
    assert malformed.json()["error"]["code"] == "ERR_VECTOR_STORE_NOT_FOUND"


async def test_attach_wrong_purpose_400(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: FakeChunkEmbedder
) -> None:
    """covers: R:vector_store_file_purpose_invalid — a purpose=batch file is refused."""
    vsid = await _create_store(client, tenant_a["key"])
    jsonl = b'{"custom_id":"r1","method":"POST","url":"/v1/chat/completions","body":{}}\n'
    file_id = await _upload(
        client, tenant_a["key"], filename="batch.jsonl", data=jsonl, purpose="batch"
    )
    resp = await _attach(client, tenant_a["key"], vsid, file_id)
    assert resp.status_code == 400, f"expected 400, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_VECTOR_STORE_FILE_PURPOSE_INVALID"
    assert await _count(app, "vector_store_files", tenant_a["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# SECURITY HARD-STOP HEAL (2026-07-24) — ZDR TOCTOU during the embed round-trip.
#
# Adversarial refute-read: raise_if_zdr fired ONCE at drive() entry. The embed
# call is a real network round-trip (5s connect + 30s read in prod); a tenant
# could flip tenants.zdr_enabled=true via their OWN retention-policy admin
# endpoint WHILE the embed call was in flight, and finalize_completed — opened
# in a fresh session AFTER embed returned, with no second ZDR check — would
# commit chunk rows (payload at rest) for a now-ZDR tenant. The existing
# test_worker_zdr_flipped_after_attach_fails_closed test uses an INSTANT
# FakeChunkEmbedder (zero TOCTOU window) and could never catch this.
#
# Fix: raise_if_zdr is now re-checked a SECOND time, INSIDE the same
# session/transaction as finalize_completed, immediately before the chunk
# write + CAS status flip and before that transaction commits — see
# VectorStoreIngestWorker.drive()'s `zdr_flipped_during_ingest` branch.
# ---------------------------------------------------------------------------


async def test_worker_zdr_flip_during_embed_fails_closed_toctou(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M5 SECURITY HARD-STOP heal — a ZDR flip DURING the embed
    round-trip (the TOCTOU window an instant fake embedder cannot reproduce)
    must fail closed: status != completed, zero chunks, zero usage records."""
    slow_embedder = SlowChunkEmbedder(delay=0.3)
    app.state.vector_store_embedder = slow_embedder
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    async def _flip_zdr_mid_flight() -> None:
        # Give drive() time to pass the ENTRY raise_if_zdr check and reach the
        # (slow) embed call before flipping — this is the exact race window.
        await asyncio.sleep(0.1)
        await _set_tenant_zdr(app, tenant_a["tenant_id"])

    await asyncio.gather(worker.process_once(), _flip_zdr_mid_flight())

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] != "completed", f"ZDR flip mid-ingest must fail closed: {obj}"
    assert obj["status"] == "failed"
    assert obj["last_error"] is not None
    assert obj["last_error"]["code"] == "zdr_blocked"
    assert len(slow_embedder.calls) == 1, "the embed call itself is allowed to complete"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0, (
        "the TOCTOU defect: chunks must NEVER survive a ZDR flip that lands"
        " before the finalize commit"
    )
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M2 claim-safety — a TRUE concurrent finalize_completed() race on one row_id
# (test_two_workers_never_double_process only proves BRPOP queue-level
# pop-once; this proves the DB-level CAS itself under real concurrency).
# ---------------------------------------------------------------------------


async def test_concurrent_finalize_completed_race_exactly_one_wins_cas(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M2 — two finalize_completed() calls racing the SAME row_id via
    asyncio.gather: exactly one wins the CAS, one set of chunks persists, no
    duplicate/partial write survives the loser (its whole transaction rolls
    back, chunk inserts included)."""
    from gateway.vector_stores.infrastructure.file_repository import (
        VectorStoreFileRepository,
    )
    from gateway.vector_stores.wire_id import parse_wire_id as parse_vs_wire_id

    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    row_id = await _vsf_row_id(
        app, tenant_id=tenant_a["tenant_id"], vsid=vsid, file_id=file_id
    )
    vector_store_id = parse_vs_wire_id(vsid)
    assert vector_store_id is not None
    tenant_uuid = uuid.UUID(tenant_a["tenant_id"])
    chunks = ["alpha chunk", "beta chunk"]
    vectors = [[0.1] * _DIM, [0.2] * _DIM]

    async def _finalize_in_own_session() -> bool:
        async with app.state.sessionmaker() as session:
            repo = VectorStoreFileRepository(session)
            won = await repo.finalize_completed(
                row_id=row_id,
                vector_store_id=vector_store_id,
                tenant_id=tenant_uuid,
                chunks=chunks,
                vectors=vectors,
                usage_bytes=1234,
            )
            if won:
                await session.commit()
            else:
                await session.rollback()
            return won

    results = await asyncio.gather(
        _finalize_in_own_session(), _finalize_in_own_session()
    )
    assert sorted(results) == [False, True], "exactly one racer must win the CAS"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == len(chunks), (
        "the loser's chunk inserts must roll back with its whole transaction —"
        " never a duplicate/partial set of chunks"
    )

    final = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert final["status"] == "completed"


# ---------------------------------------------------------------------------
# SECURITY HARD-STOP HEAL #2 (zdr-ingest-lock-heal, 2026-07-25) — the ZDR
# re-check → finalize-commit window.
#
# The heal above (2026-07-24) added a SECOND raise_if_zdr inside the finalize
# session, which closed the flip-during-EMBED window. It did NOT close the
# window it opened by existing: that re-check is a PLAIN, non-locking SELECT,
# so under read-committed it only sees a flip that COMMITTED BEFORE it ran. A
# flip landing between the re-check and the commit of finalize_completed (a
# DELETE + up to MAX_CHUNKS_PER_FILE inserts + 2 UPDATEs) still persists
# payload chunks at rest for a now-ZDR tenant.
#
# The sibling path already rejected exactly this fix: responses_store/
# application/persistence.py::_is_zdr_locked states it verbatim — "A plain
# SELECT only catches a flip that COMMITTED BEFORE the read; the re-read ->
# INSERT-commit window stays open." It was healed with SELECT ... FOR UPDATE.
# That lesson was never back-applied here.
#
# Why the shipped test could not catch it: test_worker_zdr_flip_during_embed_
# fails_closed_toctou flips during the EMBED, which the plain re-check DOES
# catch. Pinning the flip inside the re-check → commit window needs a lock
# held across it, not a sleep.
#
# The guarantee FOR UPDATE buys is serialization, not clairvoyance: the flip
# lands either fully before the write (caught, fail-closed) or fully after
# (chunks written, then swept by the retention sweeper's ZDR purge pass). It
# can never interleave.
# ---------------------------------------------------------------------------


async def test_worker_zdr_flip_inflight_at_finalize_fails_closed(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M1, M2, R:zdr_blocked — a ZDR flip held UNCOMMITTED (row-locked)
    across the worker's finalize re-check must fail closed: zero chunks at rest.

    Timing is structural, not racy: the holder takes the tenants row lock BEFORE
    the worker is driven (awaited via ``flip_locked``) and releases it strictly
    AFTER the worker would otherwise have committed (hold 0.5s > embed 0.3s).

      UNPATCHED — the plain re-check reads the pre-flip value under
        read-committed (an uncommitted UPDATE is invisible to it and never
        blocks it), writes the chunks, commits at ~0.3s. The flip commits at
        0.5s. Chunks survive for a ZDR tenant -> this assertion FAILS.
      PATCHED  — the locked re-check BLOCKS on the held row lock until 0.5s,
        then reads zdr_enabled=true, rolls the whole finalize transaction back
        and marks the row failed/zdr_blocked -> zero chunks.
    """
    embedder = SlowChunkEmbedder(delay=0.3)
    app.state.vector_store_embedder = embedder
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)

    flip_locked = asyncio.Event()

    async def _hold_uncommitted_flip() -> None:
        """Take the tenants row lock via an UNCOMMITTED UPDATE and hold it
        across the worker's entire finalize window, then commit."""
        async with app.state.sessionmaker() as session:
            await session.execute(
                text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
                {"tid": tenant_a["tenant_id"]},
            )
            flip_locked.set()
            await asyncio.sleep(0.5)  # > the 0.3s embed: spans the finalize window
            await session.commit()

    async def _drive() -> None:
        await flip_locked.wait()  # the lock is held before drive() ever starts
        await worker.process_once()

    await asyncio.gather(_drive(), _hold_uncommitted_flip())

    # CONFOUND GUARD (independent refute finding, 2026-07-25): the attach router's
    # `_enqueue_or_fallback` is FAIL-OPEN — if the Redis enqueue raises (a hiccup on
    # a contended shared test Redis, or its 2s timeout) it spawns an UNSYNCHRONIZED
    # `asyncio.create_task` drive. That drive can run to completion BEFORE the flip is
    # even in flight, legitimately persisting chunks with ZERO ZDR violation — and the
    # chunk assertion below would then read a benign infrastructure hiccup as a
    # security regression. It is the single most alarming false signal this suite can
    # produce, so fail here first, loudly and diagnosably, rather than there.
    inline_tasks = getattr(app.state, "vector_store_ingest_tasks", None)
    assert not inline_tasks, (
        "TEST CONFOUND, not a security failure: the attach fell back to an inline"
        " drive (Redis enqueue failed), so this run never exercised the"
        " flip-synchronized window. Re-run; if persistent, the shared test Redis is"
        f" unhealthy. Fallback tasks: {inline_tasks!r}"
    )
    assert len(embedder.calls) == 1, (
        "TEST CONFOUND, not a security failure: expected EXACTLY ONE drive of this"
        f" row, saw {len(embedder.calls)} embed calls — a second, unsynchronized"
        " drive raced the flip and invalidates the window this test measures"
    )

    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) == 0, (
        "TOCTOU: a ZDR flip in flight across the finalize re-check must never"
        " leave chunk payload at rest — the re-check must take a row lock"
    )
    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "failed", f"must fail closed, got: {obj}"
    assert obj["last_error"] is not None
    assert obj["last_error"]["code"] == "zdr_blocked"
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 0


async def test_zdr_locked_read_has_a_single_definition_site() -> None:
    """covers: M3 — the lock-taking ZDR read is ONE shared primitive.

    Two hand-copied ``FOR UPDATE`` strings is how the first heal drifted from
    the second: the responses-store path got the lock, the ingest worker did
    not. Assert the primitive is defined once and reached by both callers.
    """
    from pathlib import Path

    import gateway

    src = Path(gateway.__file__).parent
    # Match the EXACT executable SQL literal that was being hand-copied. Deliberately
    # not a looser token match:
    #   - a file-wide "FOR UPDATE" + "tenants" match also flags three LEGITIMATE and
    #     unrelated locks — `FOR UPDATE OF t` on the seat-cap
    #     (tenants/application/entitlements.py) and billing-owner
    #     (tenants/infrastructure/users_repository.py, scim/) invariants. Those lock
    #     DIFFERENT columns for DIFFERENT reasons and must survive untouched.
    #   - a per-line "zdr_enabled" + "FOR UPDATE" match also flags PROSE — the
    #     docstrings that describe the lock (e.g. "SELECT tenants.zdr_enabled ...
    #     FOR UPDATE"). Those should stay; they are the explanation, not the copy.
    statement = "SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE"
    definitions = sorted(
        {path.name for path in src.rglob("*.py") if statement in path.read_text()}
    )
    assert definitions == ["retention_policy.py"], (
        "the locked ZDR read must be defined ONLY in tenants/application/"
        f"retention_policy.py; found it in: {definitions}"
    )

    from gateway.tenants.application import retention_policy

    assert hasattr(retention_policy, "is_zdr_locked")
    assert hasattr(retention_policy, "raise_if_zdr_locked")


async def test_plain_raise_if_zdr_still_takes_no_lock(
    app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M4 — the FROZEN v1 plain read must NOT gain a lock.

    Six pre-existing choke points (artifacts, conversations, memories,
    batch_job_items, video_generation_jobs, files) call raise_if_zdr on their
    hot write paths. Adding FOR UPDATE there would serialize all of them on the
    tenant row. With another session holding the tenants row lock, the plain
    read must still return promptly.
    """
    from gateway.tenants.application.retention_policy import raise_if_zdr

    async with app.state.sessionmaker() as holder:
        await holder.execute(
            text("SELECT zdr_enabled FROM tenants WHERE id = :tid FOR UPDATE"),
            {"tid": tenant_a["tenant_id"]},
        )
        async with app.state.sessionmaker() as reader:
            await asyncio.wait_for(
                raise_if_zdr(reader, uuid.UUID(tenant_a["tenant_id"])),
                timeout=2.0,  # a lock-taking read would block here until rollback
            )
        await holder.rollback()


async def test_non_zdr_ingest_completes_unchanged_after_lock_heal(
    client: Any, app: Any, tenant_a: dict[str, str], fake_embedder: Any
) -> None:
    """covers: M5 — the non-ZDR majority path is behaviorally identical.

    The locked re-check adds a row lock to every ingest finalize; this pins that
    an ordinary (zdr_enabled=false) ingest still reaches completed with its
    chunks and exactly one usage record.
    """
    worker = _build_worker(app)
    vsid = await _create_store(client, tenant_a["key"])
    file_id = await _upload(client, tenant_a["key"])
    await _attach(client, tenant_a["key"], vsid, file_id)
    await _drain(worker)

    obj = (await _get_file(client, tenant_a["key"], vsid, file_id)).json()
    assert obj["status"] == "completed", f"majority path must be unchanged: {obj}"
    assert await _count(app, "vector_store_chunks", tenant_a["tenant_id"]) > 0
    assert await _count(app, "usage_records", tenant_a["tenant_id"]) == 1
