# PLAN: Attach a /v1/files file: chunk, embed, index

slug: vector-store-files · created: 2026-07-24 · stage: production
milestone: managed-rag-finetune
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: vector-store-files — attach an R4 `/v1/files` file to a vector store: NON-BLOCKING attach (row committed `in_progress`, 200 immediately) + a BACKGROUND WORKER that chunks → embeds (ONE batched provider call via a port) → indexes into the FROZEN `vector_store_chunks` pgvector table and flips the FROZEN `vector_store_files.status`. ZERO DDL, ZERO re-freeze of wave-1.
Framings weighed: background-worker ingestion mirroring the SHIPPED batches/video durable-queue shape — Redis LPUSH/BRPOP queue + `Worker.run_forever()`/`process_once()` test seam + `recover_orphans()` + status-guarded CAS + fail-open inline fallback (chosen — **Tin-directed at the freeze gate 2026-07-24**, superseding the earlier sync-inline draft; reuses the repo's established worker pattern verbatim, no invented machinery, and the wire was already poll-shaped so nothing client-visible changes except attach returning `in_progress`) · synchronous-inline ingestion (REJECTED by Tin — request latency = embed latency) · a NEW ingestion-job table (rejected — the frozen `vector_store_files` row IS the job row: status/last_error/UNIQUE already frozen for exactly this; no new table, no manifest edits) · reuse the full `EmbeddingsUseCase` for the embed leg (rejected — drags governance/cache/guardrails into an internal server-side call and double-couples billing; a thin `ChunkEmbedder` Protocol port + per-tenant-breaker adapter is the clean seam). Deliberate consequence (advisor-confirmed): chunk text gets NO guardrail/PII masking on the embed leg — it is the tenant's OWN previously-uploaded file content going to the tenant's embedding provider, mirroring OpenAI's native vector-store ingestion; stated here so it is a decision, not a side-effect.
Must:
<must>
  - M1 non-blocking attach: `POST /v1/vector_stores/{vsid}/files {file_id}` validates (store, file, purpose, ZDR), commits the `vector_store_files` row with `status="in_progress"`, ENQUEUES the row id to the Redis ingest queue (`vector_store:ingest:pending`, bounded LPUSH), and returns 200 IMMEDIATELY with the `vector_store.file` object at `status:"in_progress"` — NO chunking, NO embed call, NO chunk rows in the request path; enqueue failure falls open to an inline `asyncio.create_task` drive so no job is ever dropped (batches/video precedent).
  - M2 background worker: `VectorStoreIngestWorker` (structurally copied from `BatchJobWorker`/`VideoJobWorker`: `run_forever()` lifespan loop · `process_once()` TEST SEAM · `recover_orphans()` at startup re-enqueuing non-terminal rows) claims a row id via BRPOP, reads the file's bytes, chunks (fixed 2400-char windows, 200 overlap, utf-8 errors="replace"), makes ONE batched embed call for all chunks, then in ONE atomic finalize tx: deletes any stale chunks for the vsf id, bulk-INSERTs `vector_store_chunks` rows (embedding vector(1536), tenant_id + vector_store_id denormalized), and CAS-flips `UPDATE .. SET status='completed' WHERE id=:id AND status='in_progress' RETURNING` — a CAS miss (a racer already finalized) ROLLS BACK the whole tx, so there is NEVER a half-indexed `completed` and never duplicate chunks.
  - M3 status pollable: `GET /v1/vector_stores/{vsid}/files/{file_id}` returns the `vector_store.file` object (`status` ∈ in_progress|completed|failed, `last_error`, `usage_bytes`, unix `created_at`) — `in_progress` before the worker runs, terminal after.
  - M4 list: `GET /v1/vector_stores/{vsid}/files` returns `{object:"list", data, has_more}` newest-first, tenant-scoped, limit default 20 cap 100, has_more via limit+1 probe (mirrors the frozen stores list).
  - M5 ZDR fail-closed twice: (a) at attach entry — 403 `ERR_ZDR_PAYLOAD_BLOCKED`, zero rows of any kind; (b) `raise_if_zdr` is the FIRST line of the worker's chunk-write path — a tenant who flips ZDR on AFTER attach gets `status:"failed"` + `last_error:{code:"zdr_blocked"}` with ZERO chunks (fail-closed, never a silent store; the wave-1-pinned obligation).
  - M6 metering: exactly ONE `usage_records` row per COMPLETED ingest, `model_id` = the store's `embedding_model`, recorded AWAITED by the worker AFTER the finalize tx commits and ONLY when its CAS won — so a duplicate drive can never double-bill; a failed/skipped embed writes ZERO usage records.
  - M7 honest failure + retry: worker embed failure (upstream down / circuit open) → CAS `in_progress`→`failed` with `last_error:{code:"embedding_unavailable"}`, ZERO chunk rows, ZERO usage records; a re-attach of a `failed` row CAS-flips `failed`→`in_progress` and RE-ENQUEUES it (retry); a re-attach of an `in_progress` row returns it AND re-enqueues (heals crash-stranded rows — duplicate enqueue is safe per M2's CAS finalize + the worker's terminal-skip guard); a `completed` row is returned as-is, never re-run.
  - M8 live file_counts: the frozen `vector_store` object's `file_counts` is now computed live (COUNT..GROUP BY vsf.status) — the wave-1 router docstring pre-authorizes this with no contract change.
  - M9 upload purpose: `/v1/files` gains `"assistants"` in `_SUPPORTED_PURPOSES` additively (the exact precedent finetune-broker set adding `"fine-tune"`); every existing purpose stays byte-identical.
  - M10 bounded IO: the production embedder adapter carries connect/read timeouts and a PER-TENANT CircuitBreaker registry (bounded LRU, mirrors `OpenAIFinetuneClient._TenantBreakerRegistry` — this repo HARD-STOPPED twice on shared breakers); embed is retried at most once (idempotent read-only op); the queue enqueue is bounded (2s, batches precedent) and BRPOP claim timeouts keep shutdown responsive.
</must>
Reject:
<reject>
  - unknown | cross-tenant | malformed vector_store_id -> "ERR_VECTOR_STORE_NOT_FOUND" (404, uniform, no oracle; nothing persisted)
  - unknown | cross-tenant | soft-deleted | malformed file_id -> "ERR_FILE_NOT_FOUND" (404, uniform; nothing persisted)
  - missing / non-string file_id in body -> "ERR_VECTOR_STORE_FILE_ID_REQUIRED" (422; nothing persisted)
  - file purpose not in {assistants, user_data} -> "ERR_VECTOR_STORE_FILE_PURPOSE_INVALID" (400; nothing persisted)
  - ZDR tenant attaching -> "ERR_ZDR_PAYLOAD_BLOCKED" (403; zero vsf rows, zero chunks)
  - missing/invalid/expired API key -> 401 ERR_AUTH_INVALID_KEY / ERR_AUTH_KEY_EXPIRED (inherited `_authenticate` dependency, prose-covered)
</reject>
After:
<after>
  - The tenant's file is queryable once the worker completes: `vector_store_chunks` rows exist with tenant_id + vector_store_id denormalized under the FROZEN HNSW cosine index — wave-3 top-k is `SELECT .. WHERE vector_store_id=:v AND tenant_id=:t ORDER BY embedding <=> :q LIMIT k` with ZERO schema change AND ZERO status join — chunk-row existence IMPLIES status=completed because chunks and the CAS flip commit in one atomic finalize tx (load-bearing invariant, advisor-verified: never insert a chunk outside that tx).
  - `vector_store_files.status` reached completed (or failed with a named last_error); the store object's file_counts and usage_bytes reflect it; AT MOST one usage record exists per ingest (exactly one when completed — CAS-gated); alembic head is UNCHANGED at 6f2a9c1e3b7d and NO new table exists (the vsf row is the job row; the queue is Redis, manifest-exempt like batch/video queues).
</after>
Boundary: file_id wire format `file-<32hex>` (hyphen) vs vector_store_id `vs_<32hex>` (underscore) — tests speak both; file bytes may be inline BYTEA or s3-backed (worker reads via FileRow.content | ObjectStore.get, tests use inline); empty/undecodable file content → status `failed`, last_error code `file_empty` (prose-covered secondary); ingestion timing is driven deterministically in tests via `process_once()` — never a sleep/poll race.
<assumptions>
  ⚠ at-least-once delivery: a duplicate drive (recover_orphans / re-attach re-enqueue racing an active worker) can double-CALL the embed provider — wasted provider cost, but NEVER a double tenant bill (M6 CAS-gates the usage record) and NEVER duplicate chunks (M2 finalize tx). If wrong (wasted embed calls matter at scale): add a claim-marker CAS before the embed leg at a later freeze; the wire is unaffected.
  ⚠ one batched embed call covers a whole file (no per-batch splitting) — if wrong (provider input-size caps): split into capped batches; M6's completed-ingest wording already permits it without a contract change.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
POST /v1/vector_stores/{vsid}/files   body: { file_id: "file-<32hex>" }   NON-BLOCKING
  200 -> { id: "file-<32hex>", object: "vector_store.file", created_at: <unix int>,
           vector_store_id: "vs_<32hex>", usage_bytes: <int>,
           status: "in_progress",   -- ALWAYS in_progress on first attach (worker flips it);
                                    -- re-attach returns the row's CURRENT status
           last_error: null | { code: "embedding_unavailable" | "zdr_blocked"
                                      | "file_empty" | "file_too_large", message } }
  422 -> { error: "ERR_VECTOR_STORE_FILE_ID_REQUIRED" }
  400 -> { error: "ERR_VECTOR_STORE_FILE_PURPOSE_INVALID" }
  403 -> { error: "ERR_ZDR_PAYLOAD_BLOCKED" }       (problem+json — inherited spec shape)
  404 -> { error: "ERR_VECTOR_STORE_NOT_FOUND" | "ERR_FILE_NOT_FOUND" }
GET  /v1/vector_stores/{vsid}/files/{file_id}  -> 200 same object | 404 (uniform, no oracle)
GET  /v1/vector_stores/{vsid}/files            -> 200 { object:"list", data:[...], has_more }
                                                  (newest first, limit<=100, limit+1 probe)
Wire error envelope: bare {"error":{"code",...}} JSONResponse for 4xx domain errors
  (mirrors the frozen vector_stores router); 401/403 stay ProblemError specs.
Async pipeline (the batches/video durable-queue shape, structurally copied — NOT invented):
  RedisVectorStoreIngestQueue  — key "vector_store:ingest:pending"; enqueue = bounded LPUSH
                                 (2s, asyncio.wait_for — batches precedent); claim = BRPOP
                                 (timeout>0, shutdown-responsive). Enqueue failure at attach
                                 falls open to inline asyncio.create_task (no job dropped).
  VectorStoreIngestWorker      — run_forever() lifespan loop (single-job exception never kills
                                 the loop) · process_once() TEST SEAM (claim+drive one id,
                                 False on empty) · recover_orphans() at startup re-enqueues
                                 non-terminal vsf rows · terminal-status skip guard.
  Worker drive per row id:     load vsf row -> skip if terminal -> raise_if_zdr (FIRST line of
                               the chunk-write path; ZDR now => CAS to failed/zdr_blocked) ->
                               read file bytes -> chunk -> ONE batched embed via the port ->
                               FINALIZE TX (atomic): DELETE stale chunks for vsf_id + bulk
                               INSERT chunks + CAS status='completed' WHERE status='in_progress'
                               RETURNING; CAS miss => ROLLBACK everything ->
                               after commit, iff CAS won: awaited usage record.
Schema (FROZEN wave-1 — ZERO DDL, no migration, NO new table, head stays 6f2a9c1e3b7d;
        the vsf row IS the job row — status/last_error/UNIQUE were frozen for exactly this):
  vector_store_files  — attach: INSERT .. ON CONFLICT (vector_store_id,file_id) DO NOTHING
                        RETURNING + re-select (idempotent, no TOCTOU); retry: CAS UPDATE ..
                        SET status='in_progress' WHERE status='failed' RETURNING + re-enqueue;
                        worker terminal flips are status-guarded CAS (video/batches idiom).
  vector_store_chunks — bulk INSERT inside the finalize tx ONLY; read-side untouched
                        (wave-3 extension point: top-k ORDER BY embedding <=> :q, no status join).
  vector_stores       — read embedding_model/embedding_dim per store (dim never on the wire);
                        usage_bytes += sum(chunk bytes) at finalize; file_counts read live.
  files               — FileRow via FileRepository.get_active (tenant-scoped seam, the
                        batches-precedent reference pattern); bytes from content | ObjectStore.
  usage_records       — at most one row per ingest; exactly one when completed (CAS-gated,
                        awaited by the worker after the finalize commit).
Port (new, zero-infra-import): ChunkEmbedder Protocol
  async embed(tenant_id, model, texts: list[str]) -> (vectors: list[list[float]], usage: dict|None)
  raises UpstreamUnavailableError | CircuitOpenError (existing gateway.proxy.domain.errors) on
  failure; wired at app.state.vector_store_embedder, read by the worker via a zero-arg
  get_embedder callable (the get_batch_processor idiom — tests swap it after construction);
  production adapter: httpx, connect 5s/read 30s timeouts, ≤1 idempotent retry, per-tenant
  CircuitBreaker bounded-LRU registry (mirrors OpenAIFinetuneClient).
```

Anchors (the contract may cite ONLY these — all [OBSERVED] this session):
- `gateway.vector_stores.infrastructure.orm.VectorStoreRow/VectorStoreFileRow/VectorStoreChunkRow` (FROZEN tables; UNIQUE `uq_vector_store_files_store_file`; `embedding: Vector(1536)`; HNSW `ix_vector_store_chunks_embedding_hnsw`)
- `gateway.vector_stores.infrastructure.repository.VectorStoreRepository.get_active` (tenant-scoped store resolve) · `gateway.vector_stores.api.router` (`_authenticate`, `_err`, `_vector_store_object`/`_zero_file_counts` — the pre-authorized live file_counts seam) · `gateway.vector_stores.wire_id.parse_wire_id/to_wire_id`
- `gateway.files.infrastructure.repository.FileRepository.get_active` (file resolve, loads content) · `gateway.files.wire_id` (`file-<32hex>`) · `gateway.files.api.router._SUPPORTED_PURPOSES` (additive `"assistants"`, M9) · `gateway.objectstore.port.ObjectStore`
- `gateway.tenants.application.retention_policy.raise_if_zdr` (M5 choke point) · `gateway.core.error_catalog` (`VECTOR_STORE_NOT_FOUND`, `FILE_NOT_FOUND`, `ZDR_PAYLOAD_BLOCKED`, + 2 NEW additive ErrorSpecs)
- `gateway.usage.application.recorder` `UsageRecorder.record` via `app.state.usage_recorder` (M6) · `gateway.proxy.domain.errors.UpstreamUnavailableError/CircuitOpenError` · `gateway.proxy.infrastructure.circuit_breaker.CircuitBreaker` + the `_TenantBreakerRegistry` shape in `gateway.finetune.infrastructure.openai_client` (M10 mirror)
- `gateway.batches.application.worker` (`RedisBatchJobQueue` bounded LPUSH/BRPOP · `BatchJobWorker.run_forever/process_once` · `recover_orphans` · terminal-status skip · fail-open inline fallback) and `gateway.video.application.worker` (`VideoJobWorker`, `should_start_video_worker`, status-guarded set_failed/set_succeeded) — the STRUCTURAL TEMPLATE for `VectorStoreIngestWorker` (M1/M2/M7)
- `gateway.main` router registration block (`app.include_router(vector_stores_router)` sibling) + `app.state.usage_recorder` / `app.state.redis_client` wiring + the lifespan worker-startup precedent (video/batch workers)

Target (measurable): all 19 §4 red tests green · `vector_store_core` (23 tests) + `files_uploads_api` + `batches` suites stay green (regression floor) · alembic head unchanged `6f2a9c1e3b7d` (zero DDL, zero new tables — `ls migrations/versions` diff empty, both manifests untouched) · attach P50 request path performs NO embed IO (asserted structurally: embedder uncalled at attach-return in the M1 test) · chunk rows carry (tenant_id, vector_store_id, embedding vector(1536)) so the wave-3 top-k query runs against the frozen HNSW index with zero schema change (demonstrated by a raw `embedding <=> :q` ORDER BY after `process_once()`) · `make ci` pyright strict clean on touched files.
Status: FROZEN @ v1 — approved by Tin
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/vector_stores/` · `apps/gateway/src/gateway/files/api/router.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/main.py`
Regression floor: `apps/gateway/tests/vector_store_core` · `apps/gateway/tests/files_uploads_api` · `apps/gateway/tests/batches` (files seam consumer + worker-pattern sibling) — run before the gate
Persona: backend-architect
Strategy (SOFT, ordered): 1) additive ErrorSpecs + `"assistants"` purpose · 2) `ChunkEmbedder` Protocol + chunker (pure fn) + attach/chunk repository (ZDR first line of the chunk-write path; ON CONFLICT idempotent attach; status-guarded CAS methods mirroring `VideoJobRepository.set_failed/set_succeeded`) · 3) `application/ingest_worker.py`: `RedisVectorStoreIngestQueue` + `VectorStoreIngestWorker` structurally copied from `batches/application/worker.py` (run_forever · process_once · recover_orphans · terminal skip); the drive holds NO open DB transaction across the embed call (short claim-read tx → pure-CPU chunk → await embed → one short finalize tx — advisor-required sequencing) · 4) files_router: non-blocking attach (validate → insert → commit → bounded enqueue → fail-open inline create_task fallback tracked in `app.state.vector_store_ingest_tasks`) + GET status + GET list + live file_counts in the stores router · 5) production embedder adapter (timeouts, ≤1 retry, per-tenant breaker LRU) + lifespan wiring in main.py (queue on `app.state.redis_client`, worker start mirroring `should_start_video_worker`, `recover_orphans` before `run_forever`); tests inject the fake at `app.state.vector_store_embedder` and drive `process_once()` directly (never the lifespan loop).

Least-sure flag surfaced at freeze: [contract] at-least-once duplicate-drive semantics — a re-enqueued row racing an active worker can double-CALL the embed provider (wasted provider cost, bounded by the breaker). The tenant can NEVER be double-billed (CAS-gated usage record) and chunks can NEVER duplicate (atomic finalize + CAS), but the wasted upstream call is real; if it matters at scale, a pre-embed claim marker needs a later freeze. §1's top ⚠ feeds this.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_attach_returns_in_progress_immediately: upload assistants file / attach / 200 + status "in_progress", embedder NOT called, ZERO chunk rows at return (non-blocking) · covers: M1
  - test_worker_completes_ingestion: attach / drive worker.process_once() in-test (batches/video test-seam precedent) / GET -> completed + chunk rows (dim-1536, tenant+store denormalized) + raw `embedding <=> :q` top-k runs + vsf & store usage_bytes > 0 · covers: M2
  - test_reattach_is_idempotent: attach, drive, re-attach / same id + current status completed, NO re-embed, chunk count unchanged, exactly ONE usage record · covers: M7, M2
  - test_get_status_returns_object: attach -> GET in_progress; drive -> GET completed; malformed file_id -> 404 · covers: M3
  - test_list_files_newest_first: attach two files, drive both / GET list / newest first, has_more false · covers: M4
  - test_attach_zdr_tenant_403_zero_residue: flip zdr_enabled / attach / 403 ERR_ZDR_PAYLOAD_BLOCKED + ZERO vsf rows + ZERO chunks + embedder never called · covers: M5, R:zdr_payload_blocked
  - test_worker_zdr_flipped_after_attach_fails_closed: attach (ZDR off), flip ZDR on, drive / status failed + last_error.code zdr_blocked, ZERO chunks (raise_if_zdr FIRST in the worker chunk-write path) · covers: M5
  - test_attach_records_exactly_one_usage_record: attach + drive / count usage_records == 1, model_id == store embedding_model; a second drive of the same id adds none · covers: M6
  - test_embed_failure_marks_failed_no_chunks: fake raises UpstreamUnavailableError / attach + drive -> status failed + last_error.code embedding_unavailable, zero chunks, zero usage records · covers: M7
  - test_reattach_after_failure_retries: fail once via drive / re-attach (CAS failed->in_progress + re-enqueue) + drive -> completed + chunks present · covers: M7
  - test_two_workers_never_double_process: one attach, two workers / exactly one process_once() claims (other returns False on empty queue), chunks written once, ONE usage record · covers: M2, M6
  - test_store_file_counts_live: attach + drive / GET store / file_counts {completed:1,total:1} · covers: M8
  - test_upload_purpose_assistants_accepted: POST /v1/files purpose=assistants -> 200 · covers: M9
  - test_embedder_breaker_is_per_tenant: production adapter yields DISTINCT breaker objects for two tenant ids · covers: M10
  - test_attach_missing_file_id_422: body {} -> 422 ERR_VECTOR_STORE_FILE_ID_REQUIRED, nothing persisted · covers: R:vector_store_file_id_required
  - test_attach_unknown_or_malformed_file_404: absent + malformed file_id -> uniform 404 ERR_FILE_NOT_FOUND, no attach row · covers: R:file_not_found
  - test_attach_cross_tenant_file_404: tenant A attaches tenant B's file -> 404 ERR_FILE_NOT_FOUND, no row, B's file untouched · covers: R:file_not_found
  - test_attach_cross_tenant_store_404: tenant A attaches own file to B's store -> 404 ERR_VECTOR_STORE_NOT_FOUND (also malformed vsid), zero rows in B's store · covers: R:vector_store_not_found
  - test_attach_wrong_purpose_400: purpose=batch file -> 400 ERR_VECTOR_STORE_FILE_PURPOSE_INVALID, nothing persisted · covers: R:vector_store_file_purpose_invalid
</test_plan>

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Secondary behaviors described as prose build-guidance (NOT gated): empty/undecodable file content → status failed, last_error.code "file_empty" · a file whose chunk count exceeds the one-batch cap (build sets it, e.g. 2048 chunks) → status failed, last_error.code "file_too_large" checked BEFORE the embed call (diagnosable, distinct from provider outage — advisor finding) · crash-stranded in_progress rows healed by `recover_orphans()` at startup and by the re-attach re-enqueue (M7) — duplicate enqueues are safe by design (CAS finalize + terminal skip; batches/video LPUSH-duplicates precedent) · enqueue fail-open: Redis down at attach → inline asyncio.create_task drive, job never dropped (fail-open path not red-gated; verify-lens obligation) · 401 auth inherited from the shared `_authenticate` dependency (already exercised by the frozen vector_store_core suite) · s3-backed file bytes path (ObjectStore.get) — tests use inline BYTEA; the build handles both via the FileRow.storage_backend switch · concurrent double-attach resolves to one row via ON CONFLICT (asserted structurally by the idempotency test; a true parallel race test is verify-lens work).

Coverage target: every Must M1–M10 and every named Reject code has ≥1 red test above (19 tests); coverage must not decrease at verify.

Tests live in: `apps/gateway/tests/vector_store_files/` · MUST run red (missing implementation) before Build.

RED evidence (ASYNC model re-run 2026-07-24 after Tin's sync->worker contract change;
DB `gateway_test_vsfiles`; `--override-ini="addopts="`):

```
$ cd apps/gateway && GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test_vsfiles \
    uv run pytest tests/vector_store_files/ --override-ini="addopts=" -q
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_returns_in_progress_immediately
FAILED tests/vector_store_files/test_vector_store_files.py::test_worker_completes_ingestion
FAILED tests/vector_store_files/test_vector_store_files.py::test_reattach_is_idempotent
FAILED tests/vector_store_files/test_vector_store_files.py::test_get_status_returns_object
FAILED tests/vector_store_files/test_vector_store_files.py::test_list_files_newest_first
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_zdr_tenant_403_zero_residue
FAILED tests/vector_store_files/test_vector_store_files.py::test_worker_zdr_flipped_after_attach_fails_closed
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_records_exactly_one_usage_record
FAILED tests/vector_store_files/test_vector_store_files.py::test_embed_failure_marks_failed_no_chunks
FAILED tests/vector_store_files/test_vector_store_files.py::test_reattach_after_failure_retries
FAILED tests/vector_store_files/test_vector_store_files.py::test_two_workers_never_double_process
FAILED tests/vector_store_files/test_vector_store_files.py::test_store_file_counts_live
FAILED tests/vector_store_files/test_vector_store_files.py::test_upload_purpose_assistants_accepted
FAILED tests/vector_store_files/test_vector_store_files.py::test_embedder_breaker_is_per_tenant
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_missing_file_id_422
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_unknown_or_malformed_file_404
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_cross_tenant_file_404
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_cross_tenant_store_404
FAILED tests/vector_store_files/test_vector_store_files.py::test_attach_wrong_purpose_400
19 failed in 16.26s

Failure REASONS (--tb=line, deduped; ALL missing-implementation, none harness-broken):
  12x ModuleNotFoundError: No module named 'gateway.vector_stores.application'
      (RedisVectorStoreIngestQueue / VectorStoreIngestWorker do not exist yet —
       every async-lifecycle test imports them inside _build_worker, batches idiom)
   1x ModuleNotFoundError: 'gateway.vector_stores.infrastructure.embedding_client'
      (the per-tenant-breaker production adapter is missing)
   3x AssertionError: upload 422 ERR_FILE_PURPOSE_UNSUPPORTED ("purpose must be one
      of: batch, vision, user_data, fine-tune") — the M9 additive "assistants"
      purpose does not exist yet
   3x Reject tests — POST /v1/vector_stores/{vsid}/files -> 404 {"detail":"Not
      Found"} (FastAPI default, no route) instead of the contracted 422/400/404
      wire envelopes
```

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-24

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose background-worker ingestion mirroring the SHIPPED batches/video durable-queue shape — Redis LPUSH/BRPOP queue + `Worker.run_forever()`/`process_once()` test seam + `recover_orphans()` + status-guarded CAS + fail-open inline fallback; rejected synchronous-inline ingestion (REJECTED by Tin — request latency = embed latency) · a NEW ingestion-job table (rejected — the frozen `vector_store_files` row IS the job row: status/last_error/UNIQUE already frozen for exactly this; no new table, no manifest edits) · reuse the full `EmbeddingsUseCase` for the embed leg (rejected — drags governance/cache/guardrails into an internal server-side call and double-couples billing; a thin `ChunkEmbedder` Protocol port + per-tenant-breaker adapter is the clean seam). Deliberate consequence (advisor-confirmed): chunk text gets NO guardrail/PII masking on the embed leg — it is the tenant's OWN previously-uploaded file content going to the tenant's embedding provider, mirroring OpenAI's native vector-store ingestion; stated here so it is a decision, not a side-effect.
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
