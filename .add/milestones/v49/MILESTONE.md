# MILESTONE: Durable video-job processing (Redis queue + restart recovery)

goal: A video-generation job survives a gateway restart: jobs are enqueued to the existing Redis, processed by an in-process worker, and any job orphaned by a restart is recovered and re-driven — with bounded retries and at-least-once idempotency — reusing the existing Redis with zero new infra.
rationale: platform-hardening — the concrete realization of program tracker item #9, chosen by Tin via AskUserQuestion ("wire a deferred delta" → "Durable job queue (v48)"). v48 shipped the video-job lifecycle with IN-PROCESS asyncio processing: on a gateway restart the lifespan cancels in-flight tasks, so a queued/running job is ORPHANED forever (never re-driven). This milestone makes processing DURABLE by reusing the EXISTING Redis (already a gateway dependency — rate-limit/budget/bandwidth) as a job queue + an in-process worker (mirrors the v29/v30 flusher/dispatcher/health-checker/drift-checker run_forever pattern) + a startup RECOVERY sweep that re-enqueues orphaned non-terminal jobs. ZERO new infra, ZERO new external key (Tin's reuse-only pick). A durable EXTERNAL queue (Celery/RQ/SQS) + multi-process visibility-timeout semantics remain a scale delta.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A Redis-backed durable queue for video jobs (reuse `app.state.redis_client`): on create, the job id is enqueued (LPUSH `video:jobs:pending`) instead of an inline asyncio.create_task; an in-process `VideoJobWorker.run_forever()` (BRPOP loop, started in the lifespan via a `should_start_video_worker` predicate, cancelled on shutdown — mirrors the existing background workers) claims + processes each job with the EXISTING `_process_video_job` logic (status machine + the pluggable provider seam + honest no-provider degradation, all unchanged).
  - RESTART RECOVERY: on startup (when the queue is enabled), a recovery sweep finds jobs left in a non-terminal status (queued/running) by a previous process and RE-ENQUEUES them (single-process model — a just-started process has no live in-flight worker, so a non-terminal row is by definition orphaned). At-least-once: a re-driven job that actually completed is protected by the v48 idempotent terminal-status guard.
  - BOUNDED RETRIES: a new `retry_count` column (migration on c1d4f7a9e2b5); each (re)drive increments it; over `GATEWAY_VIDEO_JOB_MAX_RETRIES` (default 3) → status=failed, error="max_retries_exceeded" (a poison job cannot loop forever).
  - A feature knob `GATEWAY_VIDEO_DURABLE_QUEUE_ENABLED` (default OFF → the v48 in-process path is UNCHANGED; ON → the Redis queue + worker + recovery). FAIL-OPEN: if the queue is enabled but Redis is unreachable at enqueue time, fall back to the v48 in-process task (video-gen still works; log a WARN) — never drop a job.
  - DB+Redis-backed tests (Postgres :5433 + Redis :6380): enqueue puts the id on the queue · the worker drains it to a terminal status (stub provider) · the recovery sweep re-enqueues an orphaned running job · the retry cap → max_retries_exceeded · the OFF default keeps the v48 in-process path (the existing 16 video tests stay green).
Out:
  - A durable EXTERNAL queue / dedicated worker process (Celery/RQ/SQS/Arq) + multi-process visibility-timeout / lease semantics — a scale delta (the MVP is single-process, in-the-gateway).
  - Priority queues, scheduled/delayed jobs, dead-letter queues (beyond the simple retry cap), exactly-once (at-least-once + idempotency is the contract).
  - Any change to the `/v1/video` REST shape, the provider seam, or the dashboard — processing-internal only; the create/poll/list contract + `/app/video` are UNCHANGED (jobs simply now survive restarts).
  - Wiring a real video-gen provider (still the credential-gated delta) — the worker drives whatever seam is configured, honest-degrading when none.

## Shared decisions & glossary deltas   (living — every task must honor these)
- DURABLE QUEUE (NEW glossary): a Redis list `video:jobs:pending` of job ids; producers LPUSH on create, the in-process worker BRPOPs; survives a gateway restart because the row + the queue entry persist independently of any asyncio task.
- RECOVERY (HARD): on startup the worker re-enqueues every non-terminal job (single-process model). At-least-once delivery; the v48 idempotent terminal-status guard makes a redundant re-drive safe.
- RETRY CAP (design-for-failure): retry_count over the max → failed/"max_retries_exceeded"; no infinite poison-job loop.
- REUSE / ZERO NEW INFRA: the EXISTING `app.state.redis_client` + the EXISTING `_process_video_job` + the established run_forever lifespan-worker pattern; no new dependency, no new external key.
- REVERSIBLE + FAIL-OPEN: default-OFF knob (v48 behavior preserved); Redis-down at enqueue → fall back to the in-process task (never drop a job).
- TENANT-ISOLATION unchanged (the worker loads jobs by id from trusted queue entries; the REST read path still filters tenant_id).

## Shared / risky contracts (freeze these first)
- The Redis queue protocol (key + enqueue/claim) + the worker lifecycle + the recovery sweep + the retry-cap + the at-least-once/idempotency guarantee + the default-OFF fail-open knob -> owning task `durable-video-queue`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] durable-video-queue   depends-on: none   — Redis enqueue (LPUSH) on create + an in-process BRPOP `VideoJobWorker.run_forever()` (lifespan-managed) + a startup recovery sweep (re-enqueue non-terminal jobs) + a `retry_count` column/migration + a retry cap + a default-OFF fail-open knob; reuses the v48 `_process_video_job` + the existing Redis. DB+Redis tests. FREEZES the queue/worker/recovery/retry contract.

## Exit criteria (observable; map each to the task that delivers it)
- [x] With the durable queue enabled, a submitted video job is processed to a terminal status by the worker (not an inline task); a job left non-terminal by a simulated restart is recovered on startup and re-driven to terminal; a job that keeps failing stops at status=failed/error="max_retries_exceeded" after the cap; with the knob OFF the v48 in-process behavior is unchanged; Redis-down at enqueue falls back to in-process (no dropped job)   (← durable-video-queue · gate PASS · 34 tests green)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/video : NEW `application/worker.py` (RedisVideoJobQueue · VideoJobWorker.run_forever/process_once/_drive · recover_orphans · should_start_video_worker) · `infrastructure/orm.py`+`repository.py` (retry_count column + increment_retry + list_nonterminal_ids) · `api/router.py` (create_video_job enqueue-when-enabled + fail-open fallback) · migration d1e3f5a7c9b2.
- gateway/core  : config.py — additive knobs `video_durable_queue_enabled` (default False) + `video_job_max_retries` (default 3, **0 = unlimited**).
- gateway/main  : lifespan — recover_orphans() then VideoJobWorker.run_forever() as app.state.video_worker_task when the knob is on; cancelled + CancelledError-suppressed on shutdown (mirrors drift_checker_task).
- tooling/skill/book : untouched.

### Cross-task evidence   (one row per task)
- durable-video-queue : gate=PASS · tests=34 green (11 new durable + 16 v48 unchanged + 6 migration + 1 orchestrator-added 0=unlimited footgun test); make test-fast 228 no-regression; pyright 0; single head d1e3f5a7c9b2 · residue=none (the multi-replica visibility-timeout model + DLQ are documented SPEC deltas).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — the single Exit criterion is delivered by `durable-video-queue` (gate PASS): enabled→worker-driven-to-terminal (test_enabled_enqueue_then_drain), restart-recovery (test_recovery_redrives_orphan), retry-cap (test_retry_cap_max_retries), OFF-unchanged (16 v48 tests + test_knob_off_in_process), Redis-down→fallback (test_redis_down_enqueue_fallback).
- goal: a video-generation job survives a gateway restart — proven by `test_recovery_redrives_orphan` (a row left non-terminal by a simulated restart is re-enqueued on startup and driven to a terminal status by the worker), with at-least-once safety from the v48 idempotent terminal-status guard.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
