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
     the existing synchronous /v1/chat/completions behavior (stays byte-identical); a per-key batch
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
  batch-dashboard-surface (READ side only, 2026-07-03 — the toggle's CRUD + enforcement moved to
  batch-auto-grouping)
- automatic batch-eligibility policy + whatever it implies for the sync /v1/chat/completions
  byte-identical guarantee (UNRESOLVED, see SCOPE CHANGE note) -> owning task batch-auto-grouping

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] batch-job-store         depends-on: none                                    — job entity/table/repository + durable Redis queue + worker + POST/GET /v1/batches endpoints, structurally copied from video/
- [ ] batch-cache-prefilter   depends-on: batch-job-store                         — wire the existing exact/semantic/vector response cache as a pre-filter before a line item is queued
- [ ] openai-batch-adapter    depends-on: batch-job-store                         — real OpenAI /v1/batches: JSONL build, upload, submit, poll, parse results back to custom_id
- [ ] anthropic-batch-adapter depends-on: batch-job-store                         — real Anthropic /v1/messages/batches: same shape
- [ ] batch-billing-accuracy  depends-on: openai-batch-adapter, anthropic-batch-adapter — per-line usage records at the batch-discount rate + list_price_usd + partial-failure billing
- [ ] batch-verify            depends-on: batch-billing-accuracy                  — live double-pass against real OpenAI + Anthropic batch endpoints; zero-regression floor on the sync path
- [ ] batch-auto-grouping     depends-on: batch-job-store                         — NEW 2026-07-03: the mechanism that automatically groups a tenant's ordinary chat-completion requests into a batch job per a tenant-level policy, with no explicit per-request submission step; owns the tenant enable/disable toggle (real enforcement) and must resolve its interaction with the sync /v1/chat/completions byte-identical guarantee below (UNRESOLVED — see SCOPE CHANGE note)
- [ ] batch-dashboard-surface depends-on: batch-job-store                         — NARROWED 2026-07-03: a READ-ONLY admin statistics page (savings/value display + request volume + status breakdown), honest empty-state until batch-billing-accuracy/batch-auto-grouping land; no composer/submission UI, no toggle (moved to batch-auto-grouping — see SCOPE CHANGE note)

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A tenant can POST many chat-completion requests to the new batch endpoint, get a job id back immediately (queued), poll it, and read per-line results once processing completes (← batch-job-store) (verify: batch-job-store §4 suite + a live create→poll→list smoke)
- [ ] A line item already served by the existing response cache resolves inside the batch at $0 without ever reaching the provider; only genuine misses are queued upstream (← batch-cache-prefilter) (verify: batch-cache-prefilter §4 suite — cache-hit item never queued assertion)
- [ ] A batch job targeting an OpenAI model is genuinely submitted to OpenAI's native Batch API and results come back mapped per custom_id (← openai-batch-adapter) (verify: live-verify script against the real OpenAI Batch API, openai-batch-adapter §6)
- [ ] Same for a batch job targeting an Anthropic model against Anthropic's native Batches API (← anthropic-batch-adapter) (verify: live-verify script against the real Anthropic Batches API, anthropic-batch-adapter §6)
- [ ] Every completed line item produces exactly one usage record billed at the provider's real batch-discount rate with list_price_usd recorded; a failed line bills $0 (← batch-billing-accuracy) (verify: batch-billing-accuracy §4 suite + a usage-ledger reconciliation check)
- [ ] Live double-pass against real OpenAI + Anthropic batch endpoints is green twice, and the existing sync /v1/chat/completions floor shows zero regression (← batch-verify) (verify: scripts/live_v57_verify.py double-pass log, mirroring the v19 reliability-verify pattern)
- [ ] A tenant with the batch policy enabled has eligible ordinary chat-completion requests automatically grouped and processed as a batch job with no explicit submission step, its interaction with the sync /v1/chat/completions byte-identical guarantee explicitly resolved and documented, and toggling the policy off is real enforcement (← batch-auto-grouping) (verify: batch-auto-grouping §4 suite + the resolved scope note in this file)
- [ ] An owner/admin can view a read-only statistics page showing dollars saved, request volume processed via batching, and a status breakdown (succeeded/errored/in-progress) — honest empty/zero state until real batch usage accrues (← batch-dashboard-surface) (verify: batch-dashboard-surface e2e test + manual dashboard review)

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
