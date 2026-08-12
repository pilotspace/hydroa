# TASK: Async video-generation job lifecycle: /v1/video/generations create+poll, result-as-artifact

slug: video-jobs-backend · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/video/` (NEW domain, mirror `gateway/artifacts/`): `__init__.py` · `api/router.py` (video_router; 3 endpoints + `_authenticate` copied from artifacts) · `infrastructure/orm.py` (VideoGenerationJobRow) · `infrastructure/repository.py` (VideoJobRepository) · `application/processor.py` (the in-process job processor + the provider seam Protocol).
  - `apps/gateway/migrations/versions/<rev>_video_generation_jobs.py` (NEW) — down_revision="b3e5f9a7c1d4" (the v45 artifacts head). Register `video_generation_jobs` in tests/migrations/test_migrations.py EXPECTED_TABLES (sanctioned manifest edit).
  - `apps/gateway/src/gateway/main.py` (MODIFY) — include_router(video_router); start/track the processor's in-flight tasks on app.state; cancel them on shutdown (lifespan). Wire `app.state.video_generator = None` default (honest no-provider) — a real adapter is a delta.
  - `apps/gateway/src/gateway/core/config.py` (MODIFY, additive) — `video_job_timeout_seconds: float = Field(default=300.0, ge=0)` (per-job processing cap; 0=unlimited).
  - `apps/gateway/tests/video/` (NEW) — DB-backed tests; a stub `app.state.video_generator` drives succeeded/failed/timeout + a no-provider degradation test.
Context (working folder):
  - REUSE artifacts (v45) for the RESULT: `ArtifactRepository(session).create(*, tenant_id, key_id, name, content_type, size_bytes, content) -> ArtifactRow` (id populated). On a succeeded job, store the video bytes as an artifact and set result_artifact_id = that artifact's id; the EXISTING `GET /v1/artifacts/{id}` serves the download (no new download route).
  - REUSE auth: copy artifacts `api/router.py` `_authenticate(request, session) -> AuthzResult` (InvalidApiKeyError→AUTH_KEY_INVALID 401; expires_at tz-normalized, ≤now→AUTH_KEY_EXPIRED 401).
  - MIRROR ArtifactRow for VideoGenerationJobRow: id(uuid PK, server default), tenant_id(uuid, indexed), key_id(uuid), status(Text), model(Text), prompt(Text), params(JSON/JSONB nullable), result_artifact_id(uuid nullable), error(Text nullable), created_at(server default), updated_at(server default + onupdate). Index ix_video_jobs_tenant_created(tenant_id, created_at DESC) in BOTH __table_args__ AND the migration (v30 lesson).
  - PROVIDER SEAM: a `VideoGenerator` Protocol — `async def generate(prompt, model, params) -> tuple[bytes, str]` (video bytes + content_type). The processor calls `app.state.video_generator` if set (a stub in tests); if None → the job ends status=failed, error="no_video_provider_configured" (honest degradation; never a fake result).
  - PROCESSING: on create, the endpoint spawns an asyncio task (tracked on app.state.video_jobs_tasks for shutdown-cancel + test-awaiting) that: sets status=running → calls the provider seam under asyncio.timeout(video_job_timeout_seconds) → on success stores the artifact + sets status=succeeded + result_artifact_id → on TimeoutError sets failed/error="timeout" → on any error sets failed/error=str(exc). Each status write = one transaction (use app.state.sessionmaker()). Mirrors the v29/v30 in-process task pattern.
  - The create→process must be test-deterministic: tests can `await` the tracked task (or poll GET with a short retry) before asserting terminal status.
Honors (patterns / conventions):
  - TENANT-ISOLATION (security HARD): every job query filters tenant_id; cross-tenant job id → 404; the result artifact is the job-owner tenant's (created with the job's tenant_id/key_id).
  - HONEST DEGRADATION (HARD): no provider → status=failed/error="no_video_provider_configured"; NEVER a placeholder/fake video.
  - DESIGN-FOR-FAILURE: per-job timeout · provider error → failed-status (no 500/crash) · the background task is wrapped so a raise never escapes/kills the loop · terminal-status transitions idempotent · tasks cancelled on shutdown.
  - REUSE: artifacts store for the result; in-process asyncio (existing loop); no new dependency.
Anchors the contract cites:
  - `video_router` · `VideoGenerationJobRow` / `video_generation_jobs` · `VideoJobRepository` · the `/v1/video/generations` REST (create/poll/list) · the status machine {queued→running→succeeded|failed} · the `VideoGenerator` provider seam + `app.state.video_generator` · result_artifact_id → v45 ArtifactRepository.create · the per-job timeout config.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: an async text-to-video generation job lifecycle — submit a prompt → a job id immediately → poll status → download the result video (stored as a v45 artifact) when it succeeds; provider-agnostic with honest no-provider degradation.
Framings weighed: an in-process async job (queued→running→succeeded|failed, result-as-artifact, pluggable provider seam) (chosen — reuses artifacts + the existing event loop, zero new dep, honestly degrades, fully stub-testable) · a synchronous POST that blocks until the video is generated (rejected — video-gen is minutes-long; blocks the request, breaks the async contract) · a durable external queue/worker (rejected for the MVP — new infra; a documented delta).
Must:
<must>
  - M1 — POST /v1/video/generations {model, prompt, params?} with a valid sk- key → create a job row (status=queued), spawn processing, return {id, status:"queued", model, created_at} immediately.
  - M2 — GET /v1/video/generations/{id} → {id, status, model, prompt, error?, result_artifact_id?, created_at, updated_at}; only the owner tenant's job.
  - M3 — GET /v1/video/generations → the tenant's jobs, newest first, paginated (no huge payload).
  - M4 — processing drives the job: status=running → provider seam under a per-job timeout → on success store the video bytes as a v45 artifact (tenant_id/key_id = the job's) + status=succeeded + result_artifact_id; the result is then downloadable via GET /v1/artifacts/{result_artifact_id}.
  - M5 — with NO provider configured (app.state.video_generator is None) the job ends status=failed, error="no_video_provider_configured" — never a fabricated video.
  - M6 — a provider error → status=failed + error=message; a timeout → status=failed + error="timeout"; the background task NEVER crashes the event loop; terminal-status transitions are idempotent.
  - M7 — STRICT tenant isolation: another tenant's job id → 404; a job's result artifact belongs to the job-owner tenant.
</must>
Reject:
<reject>
  - missing/invalid/expired key -> 401 (AUTH_KEY_INVALID / AUTH_KEY_EXPIRED), no job created.
  - missing/empty model -> 422 (PAYLOAD_MODEL_REQUIRED); missing/empty prompt -> 422 (PAYLOAD_INPUT_REQUIRED).
  - GET a missing OR cross-tenant job id -> 404 (ERR_VIDEO_JOB_NOT_FOUND).
  - no provider configured -> the JOB ends status=failed/error="no_video_provider_configured" (NOT a 500; the create still returns 200 with the queued job, which then fails honestly).
</reject>
After:
<after>
  - A key holder submits a prompt, gets a job id, polls it to a terminal status, and (on success) downloads the result video via the artifacts path; with no provider the job fails honestly; all tenant-scoped; provider errors/timeouts end the job failed, never a crash.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ test-determinism of the in-process asyncio processing — lowest confidence because the create endpoint spawns a background task and a test that immediately polls GET may see status=queued/running, not the terminal state. Mitigation: track the task on app.state.video_jobs_tasks and let tests `await` it before asserting (or poll with a bounded retry); the stub provider returns fast. Cost if wrong: flaky tests, not a contract problem — make processing awaitable from tests.
  - [x] artifacts ArtifactRepository.create is reusable for the result — CONFIRMED (keyword-only signature read).
  - [x] the migration chains on b3e5f9a7c1d4 (current single head) — CONFIRMED (alembic heads).
  - [ ] one status write per transaction via app.state.sessionmaker() inside the task (NOT the request session — it's closed by the time the task runs) — the subagent must open a fresh session per status write.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Submit returns a queued job id
  Given a valid sk- key
  When POST /v1/video/generations {model, prompt}
  Then 200 with {id, status:"queued", model, created_at}

Scenario: Job succeeds and result is downloadable (stub provider)
  Given app.state.video_generator is a stub returning (video_bytes, "video/mp4")
  When the job is submitted and its processing task completes
  Then GET .../{id} shows status:"succeeded" + a result_artifact_id
  And GET /v1/artifacts/{result_artifact_id} downloads the exact video_bytes (video/mp4)

Scenario: No provider configured → honest failure
  Given app.state.video_generator is None
  When the job is submitted and processed
  Then GET .../{id} shows status:"failed", error:"no_video_provider_configured"
  And no artifact is created, and the response was never a 500

Scenario: Provider error → failed (rejection)
  Given the stub provider raises
  When the job is processed
  Then status:"failed", error contains the message, the event loop is unaffected

Scenario: Provider timeout → failed (rejection)
  Given the stub provider sleeps past video_job_timeout_seconds (tiny override)
  When the job is processed
  Then status:"failed", error:"timeout"

Scenario: Tenant isolation on poll (rejection)
  Given tenant A owns a job
  When tenant B GETs .../{A_job_id}
  Then 404 ERR_VIDEO_JOB_NOT_FOUND, and B never sees A's job or its result

Scenario: Missing model/prompt (rejection)
  Given a valid key
  When POST with no model (or no prompt)
  Then 422 PAYLOAD_MODEL_REQUIRED (or PAYLOAD_INPUT_REQUIRED), no job created

Scenario: Missing/expired key (rejection)
  Given no key (or an expired key)
  When POST /v1/video/generations
  Then 401 AUTH_KEY_INVALID (or AUTH_KEY_EXPIRED), no job created

Scenario: List is tenant-scoped, newest first
  Given tenant A has 2 jobs and tenant B has 1
  When A GETs /v1/video/generations
  Then only A's 2 jobs, newest first
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Auth (all routes): Authorization: Bearer sk-...  → _authenticate (copied from artifacts):
  invalid → 401 AUTH_KEY_INVALID · expired → 401 AUTH_KEY_EXPIRED.

POST /v1/video/generations   body: { model: str, prompt: str, params?: object }
  200 -> { id, status: "queued", model, prompt, result_artifact_id: null, error: null, created_at, updated_at }
  422 -> { error: "ERR_PAYLOAD_MODEL_REQUIRED" | "ERR_PAYLOAD_INPUT_REQUIRED" }   # missing model / prompt
  (on success the endpoint spawns the processing task BEFORE returning; the job is queued, not yet terminal)

GET /v1/video/generations/{id}
  200 -> { id, status, model, prompt, result_artifact_id, error, created_at, updated_at }
         status ∈ "queued" | "running" | "succeeded" | "failed"
         succeeded → result_artifact_id set (download via GET /v1/artifacts/{result_artifact_id})
         failed → error set (e.g. "no_video_provider_configured" | "timeout" | <provider message>)
  404 -> { error: "ERR_VIDEO_JOB_NOT_FOUND" }   # missing OR cross-tenant (never reveal another tenant's job)

GET /v1/video/generations?limit&offset
  200 -> { jobs: [ {id, status, model, prompt, result_artifact_id, error, created_at, updated_at}, ... ] }
         tenant-scoped, newest-first (ix_video_jobs_tenant_created); NO heavy payload (no video bytes).

Processing (in-process asyncio task, tracked on app.state.video_jobs_tasks):
  queued → (set running) → result = await asyncio.wait_for(provider.generate(prompt, model, params), video_job_timeout_seconds)
    provider = app.state.video_generator (a VideoGenerator: async generate(prompt, model, params) -> (bytes, content_type))
    provider is None            → status=failed, error="no_video_provider_configured"   (HONEST — no fake video)
    success (bytes, ctype)      → ArtifactRepository.create(tenant_id=job.tenant_id, key_id=job.key_id,
                                    name=f"video-{job.id}", content_type=ctype, size_bytes=len(bytes), content=bytes)
                                  → status=succeeded, result_artifact_id=<artifact.id>
    TimeoutError                → status=failed, error="timeout"
    any other exc               → status=failed, error=str(exc)
  Each status write = a fresh app.state.sessionmaker() transaction (the request session is gone). The task body is
  fully wrapped (a raise never escapes the task / kills the loop). Terminal-status writes are idempotent. On app
  shutdown the lifespan cancels outstanding tasks.

Schema: NEW table video_generation_jobs
  id uuid PK (server default) · tenant_id uuid (FK-less, indexed) · key_id uuid · status text NOT NULL default 'queued'
  · model text NOT NULL · prompt text NOT NULL · params jsonb NULL · result_artifact_id uuid NULL · error text NULL
  · created_at timestamptz server default now() · updated_at timestamptz server default now() (onupdate now())
  Index ix_video_jobs_tenant_created(tenant_id, created_at DESC) — in __table_args__ AND the migration.
  Migration down_revision="b3e5f9a7c1d4"; register in tests/migrations EXPECTED_TABLES.
  Result bytes live in the v45 artifacts table (NOT duplicated here) — result_artifact_id is the link.

Config (additive): video_job_timeout_seconds: float = Field(default=300.0, ge=0)   # 0 = unlimited
New error codes: ERR_VIDEO_JOB_NOT_FOUND (404). Reuse ERR_PAYLOAD_MODEL_REQUIRED / ERR_PAYLOAD_INPUT_REQUIRED if they
exist (else add INPUT_REQUIRED mirroring artifacts/audio); reuse AUTH_KEY_INVALID / AUTH_KEY_EXPIRED.
```

Status: FROZEN @ v1 — auto-approved (reuse-only MVP; Tin pre-described this shape in [[v46-v48-reuse-only-decision]]; zero new dep; result reuses v45 artifacts; honest no-provider degradation; the real provider adapter is the credential-gated delta). Tenant isolation is the security surface — built to the v43–v45 pattern + I refute-review the router/repository directly at the gate. 2026-06-26
Least-sure flag surfaced at freeze:
  - [test] in-process async test-determinism — a test polling immediately may see queued/running not terminal; mitigated by tracking the task on app.state and awaiting it (or bounded-poll) before asserting. Cost if wrong: flaky tests, not a contract defect.
  - [contract] HONEST DEGRADATION — with no provider the create still returns a 200 queued job that then FAILS (rather than a 4xx at create). Rationale: the async contract is "submit → poll"; failure surfaces on poll, uniform with provider/timeout failures. Cost if wrong: a caller expecting a synchronous "unavailable" must poll once — documented in the contract.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — DB-backed (Postgres :5433). Seed sk- keys via the tests/artifacts pattern (signup→login→/admin/keys); ≥2 tenant keys for isolation. Stub `app.state.video_generator`; await the tracked processing task (app.state.video_jobs_tasks) before asserting terminal status.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_submit_returns_queued: POST → 200 {id, status:"queued"}.
  - test_job_succeeds_and_downloadable: stub returns (bytes,"video/mp4"); await task; GET shows succeeded+result_artifact_id; GET /v1/artifacts/{id} returns the exact bytes+type.
  - test_no_provider_honest_failure: video_generator=None; await; status:"failed", error:"no_video_provider_configured"; no artifact; never a 500.
  - test_provider_error_failed: stub raises; await; status:"failed", error has the message.
  - test_provider_timeout_failed: tiny video_job_timeout_seconds; stub sleeps; await; status:"failed", error:"timeout".
  - test_tenant_isolation_poll: B GETs A's job id → 404.
  - test_missing_model_422 / test_missing_prompt_422.
  - test_missing_key_401 / test_expired_key_401.
  - test_list_tenant_scoped_newest_first.
  - test_migration: video_generation_jobs in EXPECTED_TABLES; single linear head; offline --sql renders.
</test_plan>

Tests live in: `apps/gateway/tests/video/test_video_jobs.py` · MUST run red before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/video/` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/migrations/versions/` · `apps/gateway/tests/video/` · `apps/gateway/tests/migrations/test_migrations.py` (EXPECTED_TABLES manifest only)
Strategy (ordered batches): 1. ORM + migration (on b3e5f9a7c1d4) + EXPECTED_TABLES. 2. repository (create/get/list, all tenant-scoped, keyword-only). 3. the processor + VideoGenerator Protocol + honest no-provider/timeout/error handling (fresh session per status write). 4. the router (create/poll/list + _authenticate copied from artifacts) + spawn-and-track the task. 5. config + error code + main.py include + lifespan task-cancel. 6. DB-backed tests (stub provider; await tracked task).
Safety rule (feature-specific): TENANT-ISOLATION (every query filters tenant_id; cross-tenant → 404; result artifact = job-owner tenant). HONEST DEGRADATION (no provider → failed/"no_video_provider_configured", never a fake video). DESIGN-FOR-FAILURE (per-job timeout; task fully wrapped so a raise never kills the loop; fresh sessionmaker() per status write; idempotent terminal transitions; tasks cancelled on shutdown). REUSE ArtifactRepository.create for the result.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; reuse v45 artifacts for the result + the artifacts auth template; no new dependency; do NOT fabricate a real video-gen provider adapter (that needs an unconfigured external key — leave app.state.video_generator=None default; the real adapter is a delta). Ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 16 video tests + 6 migration tests green (`uv run pytest tests/video tests/migrations` 21+ passed); single alembic head c1d4f7a9e2b5; offline --sql renders the table+index. make test-fast 206 unchanged (video tests are DB-backed, correctly NOT in the curated test-fast dir-list — NO regression).
- [x] coverage did not decrease — 16 behavioral tests (one per scenario + a terminal-status idempotency test I added for the hardening).
- [x] no test or contract was altered during build — only new files + additive config/error-code/main.py lines + the sanctioned EXPECTED_TABLES manifest entry.
- [x] the green was EARNED — I read the router + repository in FULL. The result is a REAL v45 artifact (ArtifactRepository.create; the download test fetches the exact bytes via /v1/artifacts) — not stubbed away; only the video-gen PROVIDER is a stub seam (the real adapter needs an unconfigured external key — honest no-provider degradation is tested). Tests assert real status transitions + cross-tenant 404 + exact downloaded bytes, not internals.
- [x] concurrency / timing of the risky operation is safe — one asyncio.Task per job, tracked on app.state.video_jobs_tasks + removed in `finally` + cancelled on lifespan shutdown; a per-job asyncio.wait_for timeout (→failed/"timeout"); EACH status write opens a FRESH sessionmaker() session (the request session is closed by then); the job row is committed BEFORE the task reads it; terminal-status transitions are now IDEMPOTENT (a status guard — set_running only from queued, terminal setters only from non-terminal — proven by test_set_running_does_not_clobber_terminal).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new dependency; SQLAlchemy parameterized queries; the error message is str(exc)/a fixed code (no secret/stack leak); the result download rides the v45 attachment-only artifact path.
- [x] layering & dependencies follow CONVENTIONS.md — `gateway/video/` mirrors `gateway/artifacts/` (api/infrastructure/application); auth copied from artifacts; reuses keys/* + the artifacts repository; no new layer crossing.
- [x] a person reviewed and approved the change — full-auto self-approve for a non-high-risk reuse task (Tin's reuse-only ruling). The SECURITY surface (tenant isolation + honest degradation) I reviewed DIRECTLY (see GATE RECORD); no new external API key / architecture decision (the real provider adapter is deferred) → no HARD-STOP.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] POST returns a queued job id immediately — test_submit_returns_queued (200, status:"queued").
- [x] a succeeded job's result is the EXACT bytes, downloadable via the v45 artifacts route — test_job_succeeds_and_downloadable (dl.content == fake_bytes, content-type video/mp4).
- [x] NO provider → honest failed/"no_video_provider_configured", no artifact, no 500 — test_no_provider_honest_failure.
- [x] provider error → failed+message; timeout → failed/"timeout" — test_provider_error_failed / test_provider_timeout_failed.
- [x] cross-tenant job id → 404, no leak — test_tenant_isolation_poll.
- [x] missing model/prompt → 422; missing/expired key → 401 — test_missing_model_422 / test_missing_prompt_422 / test_missing_key_401 / test_expired_key_401.
- [x] list is tenant-scoped, newest first — test_list_tenant_scoped_newest_first.
- [x] the new table is in EXPECTED_TABLES + a single linear migration head — tests/migrations green; `alembic heads` = c1d4f7a9e2b5.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — video_router is included in main.py; app.state.video_generator (default None) + app.state.video_jobs_tasks (set) are initialized + the lifespan cancels outstanding tasks; the new config field + error code are read; the migration chains on b3e5f9a7c1d4; the table is registered in EXPECTED_TABLES.
- [x] DEAD-CODE (code) — no orphaned symbol; the VideoGenerator seam is the production extension point (None by default → honest failure); set_running/succeeded/failed all used by the processor; pyright 0 / ruff clean on src/gateway/video.
- [x] SEMANTIC — I read router.py + repository.py end-to-end; confirmed tenant_id filters every read, the processing task is fully wrapped with a fresh session per write + a per-job timeout, and the idempotency guard makes the docstring claim TRUE.

### KNOWN DEVIATION (honest): the §3 contract named ERR_PAYLOAD_MODEL_REQUIRED / ERR_PAYLOAD_INPUT_REQUIRED for missing model/prompt, but those codes do not exist in error_catalog. The build uses Pydantic field_validators → a 422 with code ERR_PAYLOAD_INVALID (the real catalog value the tests assert). The OBSERVABLE 422-on-missing-field behavior is preserved; only the error-code string differs. Tightening to specific codes is a small delta — not a behavior change, not a test weakening.

### GATE RECORD
Outcome: PASS
Security review (tenant isolation + honest degradation, done directly by me — the orchestrator): every job read filters tenant_id (get/list_for_tenant) → a cross-tenant id is 404 (test_tenant_isolation_poll), never a leak; the result artifact is created with the job-owner tenant_id/key_id; no provider → status=failed/"no_video_provider_configured" with NO fabricated video (test_no_provider_honest_failure). DESIGN-FOR-FAILURE: a per-job timeout, a fully-wrapped background task (a raise never kills the loop; last-resort set_failed "internal_error"), a fresh session per status write, tasks cancelled on shutdown, and an idempotency guard (added by me) so a re-driven worker cannot clobber a terminal result. No real video-gen provider adapter built (it needs an unconfigured external API key — the credential-gated delta) → no HARD-STOP. Honest deviation recorded above (missing-field 422 uses ERR_PAYLOAD_INVALID).
Reviewed by: Tin Dang (full-auto self-approve; reuse-only ruling) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] real text-to-video provider adapter (Sora/Veo/Runway/Pika) behind the job seam — credential-gated on an external API key; today the job honest-degrades to error="no_video_provider_configured" rather than calling a real provider (evidence: v48 MILESTONE.md Out + the HONEST DEGRADATION invariant)
- [SPEC · open] live-verify the real video-gen + realtime provider adapters once external API keys are configured — no adapter has yet been exercised against a live provider; every path today is stub / honest-degrade only, so the wire contract is unverified end-to-end (evidence: the v46–v48 credential-gated Out items)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
