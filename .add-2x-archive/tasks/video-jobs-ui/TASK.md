# TASK: Dashboard /app/video: submit a prompt, poll status, download the result

slug: video-jobs-ui · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/video.ts` (NEW) — bff-client wrappers: `createVideoJob({model,prompt})` (bffPost /v1/video/generations) · `getVideoJob(id)` (bffGet) · `listVideoJobs()` (bffGet) · reuse v45 `downloadArtifact(id)` from lib/artifacts.ts for the result.
  - `apps/dashboard/components/video/VideoWorkspace.tsx` (NEW) — submit (model + prompt) → list with live status (poll every ~2s while any job is queued/running) → download the result when succeeded (via downloadArtifact(result_artifact_id)) → show the honest failure reason when failed. Four states (empty/loading/error/data); WCAG-AA; best-effort error handling.
  - `apps/dashboard/app/(app)/app/video/page.tsx` (NEW) — renders VideoWorkspace.
  - `apps/dashboard/components/ui/app-shell.tsx` (MODIFY) — add a role-open "Video" nav entry (an icon, e.g. Clapperboard/Film); bump nav-role-filter test counts.
  - `apps/dashboard/tests-bff/video-workspace.test.tsx` (NEW) — vitest.
Context (working folder):
  - REUSE the v45 artifacts UI as the template: lib/artifacts.ts (bff-client usage + downloadArtifact raw-fetch→blob) + components/artifacts/ArtifactsWorkspace.tsx (four states, upload/list/download patterns). The result download goes through the EXISTING BFF binary-passthrough (v45) — no BFF change needed.
  - bff-client: bffGet/bffPost throw BffError with .status; credentials:"include"; cookie→Bearer in the BFF catch-all.
  - The backend job shape: {id, status, model, prompt, result_artifact_id, error, created_at, updated_at}; status ∈ queued|running|succeeded|failed. A model picker can reuse ModelPicker (/v1/models) or a plain text input (the MVP — any model id; the backend validates).
Honors (patterns / conventions):
  - POLLING (design-for-failure): poll only while a job is non-terminal; stop when all jobs are terminal; a poll error degrades gracefully (keep the last list, show a soft error), never a crash loop.
  - HONEST STATUS: render the real status + the failure error verbatim (incl. "no_video_provider_configured" → a friendly "video generation isn't configured" note); never imply success.
  - FE never sends a tenant id (the BFF derives it); all calls via the BFF.
Anchors the contract cites:
  - `createVideoJob` / `getVideoJob` / `listVideoJobs` (lib/video.ts) · `VideoWorkspace` · the `/app/video` route · the role-open "Video" nav entry · reused `downloadArtifact`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a dashboard `/app/video` workspace to submit a text-to-video job, watch its status update, and download the result.
Framings weighed: a poll-based job list reusing the v45 artifacts download (chosen — reuse-only, matches the async backend) · a websocket/SSE live feed (rejected — the backend is poll-based; over-engineered for the MVP) · a blocking submit (rejected — jobs are long-running).
Must:
<must>
  - M1 — the user enters a model + prompt and clicks Generate → POST creates the job → it appears in the list with status "queued".
  - M2 — while any job is queued/running the list polls (~2s) and updates each job's status live; polling stops once all jobs are terminal.
  - M3 — a succeeded job shows a Download action → downloads the result video (via downloadArtifact(result_artifact_id)).
  - M4 — a failed job shows its honest error reason (incl. a friendly note for "no_video_provider_configured").
  - M5 — four states (empty list / submitting / error / data); WCAG-AA; a role-open "Video" nav entry.
</must>
Reject:
<reject>
  - empty model or prompt -> the Generate button is disabled (no request).
  - a create/poll/list BFF error -> a soft inline error; the last good list stays; no crash/poll-storm.
  - a succeeded job with no result_artifact_id -> no Download action (defensive).
</reject>
After:
<after>
  - The user submits a prompt, sees the job appear + update, and downloads the result on success (or reads the honest failure); all via the BFF.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ polling cadence / stop-condition in jsdom tests — lowest confidence because the test must drive fake timers or await a state with the poll mocked; if the stop-on-terminal logic is wrong it could poll forever. Mitigation: poll only when `jobs.some(non-terminal)`; tests mock the BFF + assert the list renders the terminal state + that no further poll fires once terminal. Cost if wrong: a flaky/spinning test, not a prod bug (the guard is simple).
  - [x] the result download reuses the v45 BFF binary-passthrough + downloadArtifact — CONFIRMED (v45 shipped it).
  - [ ] the "Video" nav entry bumps the nav-role-filter counts (member +1, admin/owner/unknown +1) — the subagent updates those asserts (as v45/v46 did).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Submit a job
  Given the user is on /app/video with a model + prompt entered
  When they click Generate
  Then a job appears in the list with status "queued"

Scenario: Status updates while running, then download
  Given a queued job and the BFF poll returns succeeded with a result_artifact_id
  When the poll fires
  Then the job row shows "succeeded" and a Download action
  And clicking Download fetches the result via downloadArtifact

Scenario: Failed job shows honest reason
  Given the poll returns a failed job with error "no_video_provider_configured"
  When the list renders
  Then the row shows a friendly "video generation isn't configured" message and NO Download

Scenario: Empty model/prompt (rejection)
  Given the model or prompt field is empty
  Then the Generate button is disabled and no request is sent

Scenario: BFF error (rejection)
  Given listVideoJobs throws a BffError
  Then a soft inline error renders, the page does not crash, and no poll-storm occurs
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
lib/video.ts (BFF wrappers; FE never sends a tenant id):
  createVideoJob({model, prompt}) -> bffPost("/v1/video/generations", {model, prompt}) -> VideoJob
  getVideoJob(id)                 -> bffGet(`/v1/video/generations/${id}`)             -> VideoJob
  listVideoJobs()                 -> bffGet("/v1/video/generations")                   -> { jobs: VideoJob[] }
  (result download REUSES v45 downloadArtifact(result_artifact_id) — raw fetch → blob → object URL)
  VideoJob = { id, status:"queued"|"running"|"succeeded"|"failed", model, prompt, result_artifact_id|null, error|null, created_at, updated_at }

components/video/VideoWorkspace.tsx:
  - a form (model input/picker + prompt textarea) → Generate (disabled until both non-empty) → createVideoJob → prepend to the list
  - a list of jobs (newest first) showing status; a Download action only when status==="succeeded" && result_artifact_id
  - POLL: setInterval(~2000ms) ACTIVE only while jobs.some(j => j.status==="queued"||j.status==="running"); each tick re-fetches listVideoJobs (or per-job getVideoJob); clears the interval when all terminal; a poll error → soft error, keep last list (no storm)
  - four states (empty/submitting/error/data); WCAG-AA; failed → friendly message (special-case "no_video_provider_configured")

app/(app)/app/video/page.tsx renders <VideoWorkspace/>.
app-shell.tsx: a role-open "Video" NAV_ITEMS entry (icon e.g. Clapperboard); bump nav-role-filter test counts (member +1; admin/owner/unknown +1).
```

Status: FROZEN @ v1 — auto-approved (reuse-only FE MVP; mirrors v45/v46 dashboard pattern; the result download reuses the v45 BFF binary-passthrough; no BFF change). 2026-06-26
Least-sure flag surfaced at freeze:
  - [test] polling stop-on-terminal in jsdom — a test must mock the BFF + assert the terminal render without an infinite poll; mitigated by gating the interval on a non-terminal predicate. Cost if wrong: a flaky test, not a prod defect.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral vitest (jsdom); mock lib/video.ts (+ downloadArtifact). Mirror tests-bff/artifacts-workspace + vision-workspace.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - submit_creates_job: type model+prompt, click Generate → createVideoJob called, the job renders "queued".
  - succeeded_shows_download: list returns a succeeded job w/ result_artifact_id → a Download action; click → downloadArtifact called.
  - failed_shows_reason: list returns failed/"no_video_provider_configured" → friendly message, no Download.
  - generate_disabled_when_empty: empty model or prompt → Generate disabled.
  - bff_error_soft: listVideoJobs rejects → inline error, no crash.
  - nav: a "Video" entry exists; nav-role-filter counts bumped.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/video-workspace.test.tsx` (+ the nav-role-filter bump) · MUST run red before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/video.ts` · `apps/dashboard/components/video/` · `apps/dashboard/app/(app)/app/video/` · `apps/dashboard/components/ui/app-shell.tsx` · `apps/dashboard/tests-bff/video-workspace.test.tsx` · `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (count bump only)
Strategy (ordered batches): 1. lib/video.ts wrappers (reuse downloadArtifact). 2. VideoWorkspace (form + list + poll-while-non-terminal + download + honest-failure). 3. page + nav entry + nav count bump. 4. vitest.
Safety rule (feature-specific): POLL only while a job is non-terminal; clear the interval on unmount AND when all terminal; a poll error degrades to a soft error (no storm). FE never sends a tenant id. HONEST status (no implied success).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; reuse the v45 downloadArtifact + BFF (no BFF change); run vitest/tsc/eslint via node_modules/.bin (NEVER npx). Ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard vitest 600→610 (+10 video-workspace + 5 nav re-asserts); I re-ran `vitest run` (610 passed), tsc --noEmit 0, eslint 0 on the 4 touched source files.
- [x] coverage did not decrease — 10 new behavioral tests across the 5 scenarios + the nav entry.
- [x] no test or contract was altered during build — additive new files + a NAV_ITEMS entry + the sanctioned nav-role-filter count bump.
- [x] the green was EARNED — I read VideoWorkspace.tsx (polling + download + failed-path) directly; tests mock lib/video.ts + downloadArtifact (the real BFF calls), assert the rendered status / Download presence / the friendly no-provider message / the disabled button / the soft error — behavior, not internals.
- [x] concurrency / timing of the risky operation is safe — the poll is design-for-failure: startPoll is IDEMPOTENT (`if (intervalRef.current !== null) return` — no double-interval storm), stops on allTerminal each tick, clears on unmount (useEffect cleanup), and a poll error sets a soft pollError WITHOUT changing the cadence. Verified by reading lines 104-177 + the polling_stops_when_all_jobs_terminal test.
- [x] no exposed secrets, injection openings, or unexpected dependencies — lib/video.ts sends only {model, prompt} via the BFF (no tenant id); the result download rides the v45 attachment-only artifact path; no new dep (Clapperboard is an existing lucide icon).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors the v45 artifacts / v46 vision workspace; reuses lib/bff-client + lib/artifacts.downloadArtifact; no BFF change.
- [x] a person reviewed and approved the change — full-auto self-approve (FE reuse task, no security/architecture decision); I reviewed the polling design-for-failure surface directly.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] submitting a model+prompt creates a job that renders "queued" — submit_creates_job.
- [x] a succeeded job offers a Download that calls downloadArtifact; a failed job shows the honest reason and NO Download — succeeded_shows_download / failed_shows_reason (+ the no_video_provider_configured friendly case).
- [x] Generate is disabled until both fields are non-empty — generate_disabled_when_empty.
- [x] a list/poll BFF error degrades to a soft inline error, no crash/storm — bff_error_soft + the idempotent startPoll guard.
- [x] a role-open "Video" nav entry exists — nav-role-filter (member 9→10, others 17→18).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — VideoWorkspace is rendered by app/(app)/app/video/page.tsx; lib/video.ts wrappers are called by it; the NAV_ITEMS "Video" entry routes to /app/video; downloadArtifact reused for the result.
- [x] DEAD-CODE (code) — no orphaned symbol; getVideoJob is exported for parity (the MVP polls via listVideoJobs; getVideoJob is available for a per-job refresh) — acceptable public API surface; tsc/eslint clean.
- [x] SEMANTIC — I read VideoWorkspace.tsx end-to-end; confirmed the four states, the honest status/failed rendering, and the bounded idempotent poll.

### GATE RECORD
Outcome: PASS
Full-auto self-approve (FE reuse task; no security/architecture/external-key decision). I reviewed the one risky surface — the polling loop — directly: idempotent start (no storm), stop-on-terminal + clear-on-unmount, soft-error-on-poll-failure; the failed path never offers Download; the FE sends no tenant id. Deltas: a richer media preview, a model picker (vs the text input), streaming progress.
Reviewed by: Tin Dang (full-auto self-approve) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
