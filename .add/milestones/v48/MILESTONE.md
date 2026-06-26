# MILESTONE: Video generation (async jobs)

goal: An API key holder can submit a text-to-video generation request, get a job id back immediately, poll the job's status, and download the resulting video when it succeeds — reusing the artifacts store for the result and an in-process job lifecycle, with the real video-gen provider as a credential-gated delta.
rationale: new-major → milestone 9 of 9 (program v40–v48, "AI Application Platform"). Tin's checkpoint: reuse-only MVP, keep full-auto ([[v46-v48-reuse-only-decision]] — Tin pre-described THIS shape: "an async job row in Postgres (reuse the artifacts/BYTEA + a status column + polling endpoint) forwarding to a provider's video-gen API; a real job queue/worker = delta"). Video generation is inherently LONG-RUNNING + ASYNC, so the reusable, architecturally-meaningful core is the ASYNC JOB LIFECYCLE (submit → job id → poll → result-as-artifact), NOT a specific provider. The deployment has NO video-gen provider configured (confirmed: no text-to-video adapter exists; that needs an external API key — Sora/Veo/Runway/Pika), so the real provider adapter is a CREDENTIAL-GATED delta and a job with no provider degrades HONESTLY to status=failed/error="no_video_provider_configured" (never a fake result). The lifecycle reuses the v45 artifacts store (BYTEA) for the result + the same KeyAuthenticator + tenant-isolation + BFF patterns; the in-process processing reuses the existing event loop (mirrors the v29/v30 periodic in-process tasks) — a durable external queue/worker is a delta.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A new `gateway/video/` domain: a `video_generation_jobs` table (tenant-scoped) + an alembic migration chained on the v45 artifacts head `b3e5f9a7c1d4` + a repository + a `/v1/video` REST surface authenticated by the same `KeyAuthenticator` (+ expiry gate) as v43–v47.
    - `POST /v1/video/generations` ({model, prompt, params?}) → create a job row (status=queued) → return {id, status} immediately (202/200). Validate prompt/model.
    - `GET /v1/video/generations/{id}` → poll: {id, status, error?, result_artifact_id?, created_at, updated_at}; cross-tenant id → 404.
    - `GET /v1/video/generations` → list the tenant's jobs (newest first, paginated).
  - In-process async processing: on create, an asyncio task drives the job queued → running → succeeded|failed via a PLUGGABLE provider seam (`app.state.video_generator` — a test stub when present). On success the video bytes are stored as a v45 ARTIFACT (BYTEA) and `result_artifact_id` is set; the existing `GET /v1/artifacts/{id}` download serves it. With NO provider configured the job degrades HONESTLY → status=failed, error="no_video_provider_configured".
  - Design-for-failure: a per-job processing timeout (→ status=failed, error="timeout"); any provider error → status=failed + a message (no crash, the event loop survives); the background task is wrapped + tracked on app.state for clean shutdown + test-awaiting; idempotent terminal-status transitions; STRICT tenant isolation (cross-tenant job id → 404; the result artifact is the job-owner tenant's).
  - Dashboard: an `/app/video` surface — submit a prompt, see the job list + live status (poll), download the result video when succeeded (via the artifacts download path). Role-open.
  - DB-backed gateway tests (stub provider drives success/failed/timeout + no-provider degradation) + dashboard vitest.
Out:
  - The real text-to-video provider adapter (Sora / Veo / Runway / Pika) — CREDENTIAL-GATED delta (needs an external API key not configured in the deployment). The job lifecycle is provider-agnostic + ships with honest "no_video_provider_configured" degradation + a stub seam.
  - A durable external job queue / worker / retry-across-restart (Celery/RQ/SQS) — the MVP processes in-process on the existing event loop; a job interrupted by a restart is a documented delta (the row persists; re-drive is future work).
  - Streaming/preview frames, webhooks/callbacks on completion, cancellation mid-generation, cost-estimation for video — deltas.
  - Changing any existing route — additive only; the result download REUSES the v45 `/v1/artifacts/{id}` path.

## Shared decisions & glossary deltas   (living — every task must honor these)
- VIDEO-GEN JOB (NEW glossary): a tenant-scoped {id, tenant_id, key_id, status, model, prompt, params, result_artifact_id?, error?, created_at, updated_at} row. status ∈ {queued, running, succeeded, failed}. Keyed by a server UUID. The result video is stored as a v45 ARTIFACT; result_artifact_id links to it.
- HONEST DEGRADATION (HARD): with no video-gen provider configured the job MUST end status=failed / error="no_video_provider_configured" — NEVER a fabricated/placeholder video. The real provider adapter is a credential-gated delta.
- TENANT-ISOLATION (security, HARD invariant — same as v43–v45): every job query filters by the authenticated tenant_id; a cross-tenant job id → 404; the result artifact belongs to the job-owner tenant; a caller never sees another tenant's job or result.
- AUTH REUSE: `/v1/video` authenticates with `KeyAuthenticator.authenticate(raw_key)` + the v43–v47 expiry gate (sk- key).
- DESIGN-FOR-FAILURE: a per-job processing timeout · provider error → failed-status (not a 500/crash) · the in-process task is wrapped + tracked + cancelled on shutdown · terminal-status transitions are idempotent · one transaction per status write.
- REUSE: the result is a v45 ARTIFACT (BYTEA on the existing Postgres) downloaded via the existing `/v1/artifacts/{id}`; processing is an in-process asyncio task (existing event loop); no new dependency.
- FE honors WCAG-AA + v23/v24 tokens + the four states + best-effort error handling; all gateway calls via the BFF; the result download reuses the v45 BFF binary-passthrough.

## Shared / risky contracts (freeze these first)
- The `video_generation_jobs` schema + the `/v1/video` REST (create/poll/list) + the status machine + the provider seam + honest no-provider degradation + tenant isolation -> owning task `video-jobs-backend`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] video-jobs-backend   depends-on: none                — `gateway/video/` domain: ORM + migration (on b3e5f9a7c1d4) + repository + `/v1/video/generations` (create/poll/list) auth'd via KeyAuthenticator; in-process async processing via a pluggable provider seam; result stored as a v45 artifact; honest no-provider degradation; per-job timeout; STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + status-machine + provider-seam contract. (gate PASS, 16 tests)
- [x] video-jobs-ui        depends-on: video-jobs-backend  — dashboard `/app/video` (submit a prompt + list jobs + poll status + download the result via the artifacts path) over the BFF; role-open nav entry. (gate PASS, 10 tests)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An API key holder can POST /v1/video/generations with a prompt+model and get a job id + status=queued back immediately; poll GET /v1/video/generations/{id} to watch it move to a terminal status; on success download the result video via /v1/artifacts/{result_artifact_id}; with no provider configured the job ends status=failed/error="no_video_provider_configured" (never a fake video); all tenant-scoped (cross-tenant id → 404); a provider error/timeout ends the job failed, never a crash   (← video-jobs-backend)
- [x] A signed-in user can, in `/app/video`, submit a prompt, see the job appear and update its status, and download the result when it succeeds (or see an honest failure reason)   (← video-jobs-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway : NEW `gateway/video/` domain — a tenant-scoped, API-key-authenticated `/v1/video/generations` ASYNC job lifecycle (create→poll→list). POST creates a `video_generation_jobs` row (status=queued) + spawns a tracked in-process asyncio task; the task drives running → a pluggable `VideoGenerator` seam (app.state.video_generator) under a per-job timeout → on success stores the video bytes as a v45 ARTIFACT and sets result_artifact_id (downloaded via the existing `/v1/artifacts/{id}`). HONEST DEGRADATION: no provider → status=failed/error="no_video_provider_configured" (never a fake video; the real Sora/Veo/Runway adapter is a credential-gated delta). Design-for-failure: per-job timeout, a fully-wrapped task (a raise never kills the loop), a fresh sessionmaker() session per status write, tasks cancelled on shutdown, IDEMPOTENT terminal-status transitions (a status guard). STRICT tenant isolation (cross-tenant id → 404; result artifact = job-owner tenant). Migration c1d4f7a9e2b5 (on the v45 head b3e5f9a7c1d4); `video_generation_jobs` registered in EXPECTED_TABLES. NEW error code ERR_VIDEO_JOB_NOT_FOUND; config GATEWAY_VIDEO_JOB_TIMEOUT_SECONDS (default 300). 16 DB-backed tests. ZERO new dependency.
- dashboard : NEW `/app/video` workspace — a model+prompt form → submit → a job list that POLLS (~2s) ONLY while a job is non-terminal (idempotent start, stop-on-terminal, clear-on-unmount, soft-error-no-storm) → Download the result on success (reusing v45 downloadArtifact) → an honest failure reason (friendly note for "no_video_provider_configured"). Four states, WCAG-AA; a role-open "Video" nav entry; lib/video.ts BFF client (no tenant id from the FE). NO BFF change (reuses the v45 binary-passthrough). vitest 600 → 610; tsc 0; eslint 0.
- tooling / skill / book : untouched (only `.add/` bookkeeping + the sanctioned EXPECTED_TABLES manifest edit).

### Cross-task evidence   (one row per task)
- video-jobs-backend : gate=PASS · tests=16 green (DB-backed; 6 migration tests green with the table registered; single linear head c1d4f7a9e2b5, offline --sql renders; make test-fast 206 unchanged — video tests are DB-backed) · residue=tenant-isolation + honest-degradation + design-for-failure (timeout / wrapped-task / fresh-session / idempotency-guard) verified by a full manual read of router + repository. KNOWN DEVIATION: missing model/prompt → 422 ERR_PAYLOAD_INVALID (Pydantic validators) vs the contract's named codes (which don't exist) — observable 422 preserved. Deltas: the real provider adapter (credential-gated), a durable external queue/worker, cancellation, webhooks, cost-estimation.
- video-jobs-ui : gate=PASS · tests=10 green (full dashboard 610, +10; tsc 0; eslint 0) · residue=the poll design-for-failure (idempotent start / stop-on-terminal / clear-on-unmount / soft-error) reviewed directly by me. Deltas: a richer media preview, a model picker, streaming progress.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (key holder submits → job id → polls to terminal → downloads on success; no-provider honest failure; tenant-scoped; error/timeout → failed never crash): video-jobs-backend — 16 DB-backed tests incl. submit-queued / succeeded-downloadable-exact-bytes / no-provider-honest-failure / provider-error / timeout / tenant-isolation-404 / idempotency.
  - EC2 (user submits in /app/video, watches status, downloads on success or sees honest failure): video-jobs-ui — 10 tests over the EC1 API via the BFF, incl. submit / succeeded-download / failed-friendly-message / disabled / soft-error.
- goal: an API key holder (and a dashboard user) can submit a text-to-video request, get a job id, poll it to a terminal status, and download the result video on success — reusing the v45 artifacts store + the existing event loop with ZERO new dependency, degrading HONESTLY when no provider is configured. Proven by 16 gateway + 10 dashboard tests green (206 no-DB gateway unchanged, 610 dashboard), strict tenant isolation, design-for-failure, and the real text-to-video provider adapter cleanly deferred as the credential-gated delta.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] v48 commits land on the v40→v48 task stack (committed locally): t1 video-jobs-backend → t2 video-jobs-ui → .add close. PUSH/PR await Tin's go-ahead (outward act).
- [ ] open a PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]]); v40–v48 are a stack — merge in order or retarget.
- [ ] deploy note: run `alembic upgrade head` to apply c1d4f7a9e2b5 (creates video_generation_jobs). NO new infra/dep. Optionally set GATEWAY_VIDEO_JOB_TIMEOUT_SECONDS (default 300). ⚠ HONEST DEGRADATION: until a real text-to-video provider is wired into app.state.video_generator, every job ends "no_video_provider_configured" — the real adapter (Sora/Veo/Runway/Pika) needs an external API key + is the documented credential-gated delta. The /app/video surface works end-to-end against the lifecycle today.
- [ ] v48 joins the releasable set (v33–v47 already pending); bundle into the next release cut when Tin calls it (release.md).
