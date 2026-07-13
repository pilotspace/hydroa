# MILESTONE: Batch-discounted chat completions

goal: a tenant's eligible chat-completion requests are automatically accumulated into durable per-tenant batch jobs (fixed-tick windowed grouping, atomic claim/drain, zero-added-latency when disabled or unwired) with a read-only admin view of batch activity — NARROWED 2026-07-03, see SCOPE CHANGE note below. The original goal (real provider-native batch-discount submission + billing + live verify) carries forward unchanged as v58's goal.
rationale: Intake → new-major (confirmed by Tin 2026-07-02). No existing milestone's goal covers bulk
  discounted processing — a new capability axis (cost optimization via provider-native batch endpoints)
  that EXTENDS several existing surfaces rather than opening an unrelated one: reuses v19's exact/
  semantic/vector response cache verbatim as a pre-filter, reuses v25's BYOK per-tenant credential
  resolution, structurally copies the video-generation async-job pattern (queued→running→succeeded/
  failed + durable Redis queue + worker + orphan recovery), and extends v27's usage_source
  discriminator + the tiered-rate-cards billing (58f0008) with a batch-discount dimension. Provider
  scope and pooling boundary decided at intake (2026-07-02): OpenAI + Anthropic only (both have
  confirmed native Batch APIs — /v1/batches and /v1/messages/batches — and mature adapters here
  already; OpenRouter, this gateway's default provider, has no native batch API); per-tenant batches
  only (matches every tenant-isolation invariant in this codebase and the BYOK model; the provider
  discount is flat ~50% regardless of job size, so cross-tenant pooling would add complexity without
  increasing the savings rate). Tenant-admin enable/disable + a savings/value display added to scope
  by Tin 2026-07-02, folded into batch-dashboard-surface.
stage: production · status: active · created: 2026-07-02T15:00:40+00:00
release: 0.8.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  new async `POST /v1/batches` · `GET /v1/batches/{id}` · `GET /v1/batches` job surface (structurally
     copied from `video/`); per-tenant automatic accumulation of eligible chat-completion requests into
     a batch job (fixed-tick windowed grouping, atomic Redis claim/drain, tenant enable/disable toggle);
     a read-only admin statistics page (savings/value display + request volume + status breakdown).
Out (NARROWED 2026-07-03, see SCOPE CHANGE note): real OpenAI `/v1/batches` and Anthropic
     `/v1/messages/batches` integration; the cache pre-filter (exact→semantic→vector) applied per line
     item before it enters a batch; per-line-item usage records billed at the provider's real
     batch-discount rate; live double-pass verify — ALL deferred to the new v58 milestone, which the
     original goal above (discounted batch processing) now belongs to unchanged.
Out (original, unchanged): OpenRouter/Gemini/Bedrock/Azure/MiniMax batching (no confirmed native batch
     API for OpenRouter; others unverified — candidate follow-up milestone); cross-tenant request
     pooling; any change to the existing synchronous /v1/chat/completions behavior for a tenant that
     has not opted in (stays byte-identical — see UNRESOLVED note below, now RESOLVED); a per-key batch
     toggle (tenant-level only, mirroring the ask — cache's per-key layer is NOT assumed here unless a
     task reveals a need).

> SCOPE CHANGE (Tin, 2026-07-03): batch-dashboard-surface widened from "toggle + monitor + retrieve +
> savings display, not a job-authoring UI" to a full batches workspace — job composition/submission
> included, mirroring the existing chat/voice/memory/artifacts/vision/video playground pattern (see
> memory `aifeature-pages-usable-bar` — thin CRUD reskins are rejected; pages must be genuinely usable
> product surfaces). Ships now against the job-store shell with an honest empty/zero savings state
> (openai-batch-adapter/anthropic-batch-adapter/batch-billing-accuracy haven't landed real numbers
> yet) rather than waiting for those tasks to reorder ahead of it.
>
> SCOPE CHANGE (Tin, 2026-07-03, correction — REVERSES the note above): "we no need a playground for
> batch request, we just provide for admin to view statistics of their tenant's user request then
> system will process batch by group user's request as batch." No composer/job-authoring UI of any
> kind. batch-dashboard-surface narrows to a READ-ONLY admin statistics page (savings + volume +
> status breakdown, picked via AskUserQuestion). Separately, Tin confirmed (AskUserQuestion) batching
> is triggered by the system AUTOMATICALLY grouping ordinary requests via a per-tenant policy — NOT
> the already-shipped explicit `POST /v1/batches` submission (batch-job-store stays the underlying
> job store/processor, now consumed by this new layer instead of called directly by tenants). This is
> a new backend mechanism with no owning task before today — added as `batch-auto-grouping`.
>
> UNRESOLVED (carried as batch-auto-grouping's top ⚠-flagged open question — an AskUserQuestion round
> on this exact point timed out TWICE with no reply, once after Tin explicitly asked to be re-asked;
> proceeding per AUTO MODE fallback, NOT silently decided): this milestone's own Scope/Out line below
> says /v1/chat/completions "stays byte-identical" for any tenant, no exception — but automatic
> grouping only means something if some request's synchronous behavior changes for an opted-in
> tenant. Candidate reconciliations, neither picked yet: (a) opt-in amends byte-identical to "...for
> any tenant that hasn't opted in" — sync becomes async-shaped ONLY for a tenant that deliberately
> enables the policy; (b) byte-identical stays absolute with zero exceptions, and the policy instead
> governs a genuinely separate, always-async traffic path, not literal /v1/chat/completions traffic.
> Resolve at batch-auto-grouping's own specify phase before its Must/Reject rules are written — this
> decides whether a live, already-integrated API's contract can ever change for a tenant, not a detail.
>
> RESOLVED (batch-window-grouping §2 SCENARIOS, 2026-07-03): option (a) — byte-identical now reads "for
> any tenant that hasn't opted in" (G12: `batch_grouping_enabled=false`, the default, sees zero change,
> no Redis key ever created). An opted-in tenant's eligible requests instead get an immediate 200
> text/event-stream SSE lifecycle (G8-G11) — a deliberate, documented, opt-in-only behavior change, not
> a silent one.
>
> SCOPE CHANGE (Tin, 2026-07-03, split into v58): housekeeping at milestone-close found `add.py
> milestone-done v57` refusing — 0/8 exit criteria met. Of the 8 originally planned tasks, only
> batch-job-store, batch-dashboard-surface, and the auto-grouping mechanism (batch-auto-grouping,
> superseded same-day by batch-window-grouping once its one-job-per-request shape was found not to
> match this milestone's own goal language — see that task's `[SPEC · seeded]` delta) were ever built,
> plus one unplanned residue-closing task (batch-claim-drain-del). The other 5 —
> batch-cache-prefilter, openai-batch-adapter, anthropic-batch-adapter, batch-billing-accuracy,
> batch-verify — were never started: the entire real-provider-integration half of this milestone,
> without which no discount is actually realized (the milestone's own name is "batch-discounted").
> Verified safe to close v57 now regardless: `app.state.batch_processor` is hardcoded `None` in
> production (`main.py`), and `BatchDiversionAdapter.try_divert`'s first check is `if batch_processor
> is None: return None` — before anything ever touches the Redis buffer. Every request today falls
> through to the existing synchronous path, byte-identical, zero added latency, double-gated by the
> per-tenant `batch_grouping_enabled` toggle above it — nothing shipped in v57 can hang, error, or
> silently misbill. Decision: narrow v57's own goal (above) to what's actually delivered — durable
> per-tenant request accumulation infrastructure, inert until wired to a real adapter — and carry the
> original goal + all 5 unstarted tasks forward verbatim into a new v58 milestone. Precedent: this is
> the third scope narrowing recorded in this file (see the two batch-dashboard-surface/batch-auto-
> grouping notes above); the mechanism is the same, just applied at the whole-milestone level instead
> of one task.

## Shared decisions & glossary deltas   (living — every task must honor these)
- OPT-IN / ADDITIVE ONLY — the existing /v1/chat/completions endpoint is byte-identical at default
  settings; every new knob (batch enabled, provider adapters) ships default-off.
- ONE usage record per line item, never one per batch job — each item bills on its own outcome; a
  failed line bills $0. Matches the append-only, always-recomputable usage ledger invariant.
- Batch discount applied as a flat multiplier at record time (usage_source="batch"), not a catalog/
  rate-card schema change — keeps the tiered-rate-cards resolver (58f0008) untouched.
- SAVINGS COMPUTATION: every batch-sourced usage record additionally stores list_price_usd (what the
  line would have cost at the standard sync rate) alongside the real cost_usd. Savings shown to the
  tenant = sum(list_price_usd − cost_usd) over the period — auditable, recomputable, no separate
  estimation logic.
- TENANT ISOLATION: a batch job is submitted under ONE tenant's credential only (gateway key or their
  own BYOK key) — never mixed across tenants, mirroring every other per-tenant isolation invariant in
  this codebase.
- DESIGN-FOR-FAILURE: every new outbound IO path (batch submit / poll / retrieve, per provider) gets
  the same timeout + bounded retry + circuit-breaker treatment as every existing upstream call.
- GLOSSARY gains: batch_job (queued/running/succeeded/failed/partially_failed), batch line item,
  list_price_usd, usage_source="batch".

## Shared / risky contracts (freeze these first)
- batch_jobs table + status machine + durable-queue/worker shape (copied from video_generation_jobs)
  -> owning task batch-job-store
- BatchUpstream port — the submit/poll/retrieve seam each provider adapter implements -> owning task
  batch-job-store (seam) / openai-batch-adapter + anthropic-batch-adapter (implementations — MOVED TO
  v58, 2026-07-03, see SCOPE CHANGE note; the seam itself shipped in batch-job-store, unimplemented)
- cache pre-filter ordering — WHERE in the submit path a cache hit short-circuits a line item before
  it's ever written into the batch payload -> owning task batch-cache-prefilter (MOVED TO v58,
  2026-07-03)
- per-line-item billing shape (list_price_usd + cost_usd + usage_source="batch") + partial-failure
  handling -> owning task batch-billing-accuracy (MOVED TO v58, 2026-07-03)
- /admin/batch config + savings-read shape (RBAC mirrors /admin/cache) -> owning task
  batch-dashboard-surface (READ side only, 2026-07-03 — the toggle's CRUD + enforcement moved to
  batch-auto-grouping)
- automatic batch-eligibility policy + whatever it implies for the sync /v1/chat/completions
  byte-identical guarantee (RESOLVED, see SCOPE CHANGE note) -> owning task batch-auto-grouping,
  superseded by batch-window-grouping (same-day correction) + batch-claim-drain-del (residue fix)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] batch-job-store         depends-on: none                                    — job entity/table/repository + durable Redis queue + worker + POST/GET /v1/batches endpoints, structurally copied from video/. DONE, gate=PASS.
- [ ] batch-cache-prefilter   depends-on: batch-job-store                         — wire the existing exact/semantic/vector response cache as a pre-filter before a line item is queued. MOVED TO v58, 2026-07-03 (never started).
- [ ] openai-batch-adapter    depends-on: batch-job-store                         — real OpenAI /v1/batches: JSONL build, upload, submit, poll, parse results back to custom_id. MOVED TO v58, 2026-07-03 (never started).
- [ ] anthropic-batch-adapter depends-on: batch-job-store                         — real Anthropic /v1/messages/batches: same shape. MOVED TO v58, 2026-07-03 (never started).
- [ ] batch-billing-accuracy  depends-on: openai-batch-adapter, anthropic-batch-adapter — per-line usage records at the batch-discount rate + list_price_usd + partial-failure billing. MOVED TO v58, 2026-07-03 (never started).
- [ ] batch-verify            depends-on: batch-billing-accuracy                  — live double-pass against real OpenAI + Anthropic batch endpoints; zero-regression floor on the sync path. MOVED TO v58, 2026-07-03 (never started).
- [x] batch-auto-grouping     depends-on: batch-job-store                         — NEW 2026-07-03: the mechanism that automatically groups a tenant's ordinary chat-completion requests into a batch job per a tenant-level policy, with no explicit per-request submission step; owns the tenant enable/disable toggle (real enforcement) and must resolve its interaction with the sync /v1/chat/completions byte-identical guarantee below (RESOLVED — see SCOPE CHANGE note). DONE, gate=PASS — SUPERSEDED same-day by batch-window-grouping (shipped one-job-per-request instead of the milestone's own "group requests as batch" language; see its `[SPEC · seeded]` delta).
- [x] batch-dashboard-surface depends-on: batch-job-store                         — NARROWED 2026-07-03: a READ-ONLY admin statistics page (savings/value display + request volume + status breakdown), honest empty-state until batch-billing-accuracy/batch-auto-grouping land; no composer/submission UI, no toggle (moved to batch-auto-grouping — see SCOPE CHANGE note). DONE, gate=PASS.
- [x] batch-window-grouping   depends-on: batch-auto-grouping                     — NEW 2026-07-03 (unplanned, not on the original list): replaces batch-auto-grouping's size-1-job-per-request shape with real time-windowed multi-request accumulation (BatchWindowBuffer, atomic Redis Lua claim/drain, SSE lifecycle response). DONE, gate=PASS.
- [x] batch-claim-drain-del   depends-on: batch-window-grouping                   — NEW 2026-07-03 (unplanned, not on the original list): closes batch-window-grouping's own documented VERIFY residual — DELs an abandoned-claim's result marker at drain time instead of relying solely on its 4h TTL. DONE, gate=PASS (1 non-blocking concurrency residue tracked as an open SPEC delta, revisit before any live BatchProcessor adapter ships — i.e. before v58's openai-batch-adapter/anthropic-batch-adapter land).

## Exit criteria (observable; map each to the task that delivers it)
> NARROWED 2026-07-03 (see SCOPE CHANGE note above): the 5 criteria covering real provider submission,
> cache pre-filtering, discount billing, and live-verify were REMOVED from this list and carried
> forward VERBATIM, unchanged, into the new v58 MILESTONE.md's own Exit criteria — nothing was dropped,
> only relocated to the milestone that now owns delivering it. The 3 remaining below are this
> (narrowed) milestone's complete, genuinely-met goal.
- [x] A tenant can POST many chat-completion requests to the new batch endpoint, get a job id back immediately (queued), poll it, and read per-line results once processing completes (← batch-job-store) (verify: batch-job-store §4 suite + a live create→poll→list smoke)
- [x] REVISED 2026-07-03 (mechanism corrected from one-job-per-request to real time-windowed accumulation): A tenant with the batch policy enabled has eligible ordinary chat-completion requests automatically accumulated into a per-tenant windowed batch job with no explicit submission step (SSE lifecycle response), its interaction with the sync /v1/chat/completions byte-identical guarantee explicitly resolved (byte-identical for any tenant that has NOT opted in) and documented, toggling the policy off is real enforcement, and the mechanism is a verified zero-added-latency no-op end to end today because no real BatchProcessor adapter is wired yet (← batch-auto-grouping, superseded by batch-window-grouping, hardened by batch-claim-drain-del) (verify: batch-window-grouping §4 suite (2285 tests) + batch-claim-drain-del §4 suite (2287 tests) + the resolved scope note in this file + main.py:717/batch_diversion.py's None-gate confirming today's inertness)
- [x] An owner/admin can view a read-only statistics page showing dollars saved, request volume processed via batching, and a status breakdown (succeeded/errored/in-progress) — honest empty/zero state until real batch usage accrues (← batch-dashboard-surface) (verify: batch-dashboard-surface e2e test + manual dashboard review)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched by this milestone's own work (a separate, unrelated ADD engine version bump 1.14.0→1.15.0 landed in the same commit sequence, commit `16f726f` — pure infra, not part of v57's scope)
- skill   : untouched by this milestone (same caveat as tooling above — the version bump refreshed `.claude/skills/add/*` too, unrelated to v57)
- book    : untouched by this milestone (same caveat — `.add/docs/*` re-synced by the same unrelated version bump)
- gateway (backend) : `batch_jobs`/`batch_job_items` tables + migration + durable Redis queue/worker + `POST`/`GET /v1/batches` (batch-job-store); `BatchWindowBuffer` + `BatchWindowFlusher` + `_CLAIM_DUE_LUA`/`_TRY_ABANDON_LUA` atomic Redis scripts + `BatchDiversionAdapter` SSE lifecycle, superseding batch-auto-grouping's size-1 shape (batch-window-grouping); drain-time result-marker DEL hardening (batch-claim-drain-del); read-only `/admin/batch` statistics API (batch-dashboard-surface)
- dashboard (frontend) : read-only batch statistics admin page — savings/value display, request volume, status breakdown (batch-dashboard-surface)

### Cross-task evidence   (one row per task)
- batch-job-store : gate=PASS · tests=31/31 batches-module + full-suite 2244 passed/0 failed/0 error (`biuyvqx1w`) · residue=none
- batch-auto-grouping : gate=PASS · tests=107/107 touched-scope + full-suite 2255 passed, 89.28% coverage (11 pre-existing unrelated flakes confirmed non-regressions on solo re-run) · residue=SUPERSEDED same-day by batch-window-grouping (shipped one-job-per-request didn't match this milestone's own "group requests as batch" goal language) + 3 open SPEC deltas (dispatch_batch_job layering smell, shared-Redis `usage:events` test-isolation gap, 4 ADD-process lessons)
- batch-dashboard-surface : gate=PASS · tests=backend 9/9 + frontend 6/6+5/5 + full dashboard suite 916/916 (109 files) + `tests/batches/` 42/42 across 3 reruns · residue=2 open SPEC deltas (`savings_usd` placeholder pending v58's batch-billing-accuracy; `KEYS_MANAGE` permission scoping oddity, non-blocking)
- batch-window-grouping : gate=PASS · tests=2285 passed, 7 skipped, 0 failed (809.98s), 89.23% coverage · residue=1 open SPEC delta (validate the full claim/abandon/drain lifecycle against real multi-replica traffic once v58's first live BatchProcessor adapter ships — every proof to date races two in-process instances over a real Redis, never a deployed multi-replica gateway)
- batch-claim-drain-del : gate=PASS · tests=21/21 file + 75/75 `tests/batches/` + 2287/2287 gateway-wide, 89.26% coverage · residue=1 open SPEC delta (the DEL branch's no-`kind`-check gap — safe today only because `custom_id` is externally guaranteed fresh; revisit before v58's first live BatchProcessor adapter ships), Tin's gate decision "PASS as-frozen, residue tracked"

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — criterion 1 (job surface) ← batch-job-store row; criterion 2 (automatic accumulation, resolved byte-identical guarantee, verified inert-until-wired) ← batch-auto-grouping + batch-window-grouping + batch-claim-drain-del rows, cross-checked against `main.py`'s hardcoded `app.state.batch_processor = None` + `batch_diversion.py`'s `try_divert` None-gate; criterion 3 (read-only statistics) ← batch-dashboard-surface row
- goal: a tenant's eligible chat-completion requests are automatically accumulated into durable per-tenant batch jobs, with a read-only admin view of batch activity — proven by durable job-store infrastructure + real time-windowed accumulation with atomic Redis claim/drain (hardened against its own documented residual) + a read-only statistics surface, all 5 tasks gate=PASS, and independently verified TODAY to be a zero-added-latency no-op end-to-end (no live BatchProcessor adapter is wired in production yet) — so shipping this now, inert, is safe. The original, larger goal (real OpenAI/Anthropic batch-discount submission + billing + live-verify) was NOT dropped — it carries forward unchanged as the new v58 milestone's own goal (see SCOPE CHANGE note above).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] fold this milestone's open lessons (`add.py fold --task <slug>` per v57 task with an open lesson: batch-auto-grouping, batch-dashboard-surface, batch-claim-drain-del — batch-job-store and batch-window-grouping had none) — done, foundation-version 42 -> 45. Open SPEC deltas deliberately left open (not folded — that mechanism only consolidates DDD/SDD/UDD/TDD/ADD lessons), most notably batch-window-grouping's + batch-claim-drain-del's residues re: validating against a real multi-replica deployment and the DEL branch's kind-check gap — both explicitly to revisit when v58's provider adapters land.
- [x] archive this milestone (`add.py archive-milestone v57`) — done. `compact` (the heavy archive-to-`.add/archive/` move) was NOT run — no milestone in this project's history has ever used it (`.add/archive/` doesn't exist), so skipping it matches actual precedent rather than the v56-mirroring assumption originally written here.
- [ ] open a PR from this milestone's 4 commits (dashboard-surface, auto-grouping, window-grouping + drain-del, plus the unrelated tooling bump) — Tin reviews + merges. Explicit push/PR permission required first (not yet granted as of this housekeeping pass).
- [ ] once merged, v58 (the real provider-integration half) can proceed independently — no dependency on this milestone's own release timing beyond the merge itself.
- [ ] include in the next release cut whenever one is bundled (`release.md`) — no urgency to cut a release solely for this milestone.
