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
- [ ] video-jobs-backend   depends-on: none                — `gateway/video/` domain: ORM + migration (on b3e5f9a7c1d4) + repository + `/v1/video/generations` (create/poll/list) auth'd via KeyAuthenticator; in-process async processing via a pluggable provider seam; result stored as a v45 artifact; honest no-provider degradation; per-job timeout; STRICT tenant isolation; DB-backed tests. FREEZES the schema + REST + status-machine + provider-seam contract.
- [ ] video-jobs-ui        depends-on: video-jobs-backend  — dashboard `/app/video` (submit a prompt + list jobs + poll status + download the result via the artifacts path) over the BFF; role-open nav entry.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can POST /v1/video/generations with a prompt+model and get a job id + status=queued back immediately; poll GET /v1/video/generations/{id} to watch it move to a terminal status; on success download the result video via /v1/artifacts/{result_artifact_id}; with no provider configured the job ends status=failed/error="no_video_provider_configured" (never a fake video); all tenant-scoped (cross-tenant id → 404); a provider error/timeout ends the job failed, never a crash   (← video-jobs-backend)
- [ ] A signed-in user can, in `/app/video`, submit a prompt, see the job appear and update its status, and download the result when it succeeds (or see an honest failure reason)   (← video-jobs-ui)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
