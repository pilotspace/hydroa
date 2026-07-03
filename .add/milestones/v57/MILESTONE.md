# MILESTONE: Batch-discounted chat completions

goal: a tenant can process a set of chat-completion requests as one discounted batch job instead of many synchronous calls, with the existing response cache still applied per item
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

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  new async `POST /v1/batches` · `GET /v1/batches/{id}` · `GET /v1/batches` job surface (structurally
     copied from `video/`); cache pre-filter (exact→semantic→vector) applied per line item before it
     enters a batch; real OpenAI `/v1/batches` and Anthropic `/v1/messages/batches` integration (submit,
     poll, retrieve); per-line-item usage records billed at the provider's real batch-discount rate;
     tenant-admin enable/disable toggle + a savings/value display + explainer in the dashboard.
Out: OpenRouter/Gemini/Bedrock/Azure/MiniMax batching (no confirmed native batch API for OpenRouter;
     others unverified — candidate follow-up milestone); cross-tenant request pooling; any change to
     the existing synchronous /v1/chat/completions behavior (stays byte-identical); a full batch-
     submission composer UI (v1 dashboard is toggle + monitor + retrieve + savings display, not a
     job-authoring UI); a per-key batch toggle (tenant-level only, mirroring the ask — cache's per-key
     layer is NOT assumed here unless a task reveals a need).

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
  batch-job-store (seam) / openai-batch-adapter + anthropic-batch-adapter (implementations)
- cache pre-filter ordering — WHERE in the submit path a cache hit short-circuits a line item before
  it's ever written into the batch payload -> owning task batch-cache-prefilter
- per-line-item billing shape (list_price_usd + cost_usd + usage_source="batch") + partial-failure
  handling -> owning task batch-billing-accuracy
- /admin/batch config + savings-read shape (RBAC mirrors /admin/cache) -> owning task
  batch-dashboard-surface

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] batch-job-store         depends-on: none                                    — job entity/table/repository + durable Redis queue + worker + POST/GET /v1/batches endpoints, structurally copied from video/
- [ ] batch-cache-prefilter   depends-on: batch-job-store                         — wire the existing exact/semantic/vector response cache as a pre-filter before a line item is queued
- [ ] openai-batch-adapter    depends-on: batch-job-store                         — real OpenAI /v1/batches: JSONL build, upload, submit, poll, parse results back to custom_id
- [ ] anthropic-batch-adapter depends-on: batch-job-store                         — real Anthropic /v1/messages/batches: same shape
- [ ] batch-billing-accuracy  depends-on: openai-batch-adapter, anthropic-batch-adapter — per-line usage records at the batch-discount rate + list_price_usd + partial-failure billing
- [ ] batch-verify            depends-on: batch-billing-accuracy                  — live double-pass against real OpenAI + Anthropic batch endpoints; zero-regression floor on the sync path
- [ ] batch-dashboard-surface depends-on: batch-job-store                         — tenant-admin enable/disable toggle + savings/value display + explainer, mirrors /admin/cache RBAC + CacheSettings.tsx

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A tenant can POST many chat-completion requests to the new batch endpoint, get a job id back immediately (queued), poll it, and read per-line results once processing completes (← batch-job-store) (verify: batch-job-store §4 suite + a live create→poll→list smoke)
- [ ] A line item already served by the existing response cache resolves inside the batch at $0 without ever reaching the provider; only genuine misses are queued upstream (← batch-cache-prefilter) (verify: batch-cache-prefilter §4 suite — cache-hit item never queued assertion)
- [ ] A batch job targeting an OpenAI model is genuinely submitted to OpenAI's native Batch API and results come back mapped per custom_id (← openai-batch-adapter) (verify: live-verify script against the real OpenAI Batch API, openai-batch-adapter §6)
- [ ] Same for a batch job targeting an Anthropic model against Anthropic's native Batches API (← anthropic-batch-adapter) (verify: live-verify script against the real Anthropic Batches API, anthropic-batch-adapter §6)
- [ ] Every completed line item produces exactly one usage record billed at the provider's real batch-discount rate with list_price_usd recorded; a failed line bills $0 (← batch-billing-accuracy) (verify: batch-billing-accuracy §4 suite + a usage-ledger reconciliation check)
- [ ] Live double-pass against real OpenAI + Anthropic batch endpoints is green twice, and the existing sync /v1/chat/completions floor shows zero regression (← batch-verify) (verify: scripts/live_v57_verify.py double-pass log, mirroring the v19 reliability-verify pattern)
- [ ] A tenant owner/admin can toggle batching on/off from the dashboard and see actual dollars saved from batch processing, with a clear explanation of what the feature does (← batch-dashboard-surface) (verify: batch-dashboard-surface e2e test + manual dashboard review)

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
