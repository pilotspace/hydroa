# MILESTONE: Batch-discounted chat completions: provider integration

goal: a tenant can process a set of chat-completion requests as one discounted batch job instead of many synchronous calls, with the existing response cache still applied per item — real OpenAI/Anthropic Batch API submission, cache pre-filtering, and batch-discount billing
rationale: Split from v57 ("Batch-discounted chat completions") on 2026-07-03 — Tin's decision, via
  AskUserQuestion, after milestone-close housekeeping found `add.py milestone-done v57` refusing with
  0/8 exit criteria met. Of v57's original 8-task plan, only 3 slots were ever built (job store,
  request-side auto/window-grouping, read-only dashboard); the entire real-provider-integration half —
  the 5 tasks and exit criteria below — was never started. v57 closed narrowed to what it actually
  shipped (inert-until-wired batching infrastructure, verified zero-added-latency no-op today via
  `main.py`'s hardcoded `app.state.batch_processor = None`); this milestone carries the ORIGINAL,
  larger goal forward unchanged. See `.add/milestones/v57/MILESTONE.md`'s SCOPE CHANGE note
  (2026-07-03, "split into v58") for the full accounting. Queued, not yet activated — Tin split the
  scope but has not yet asked to start building it; promote with `add.py activate v58` when ready.
stage: production · status: queued · created: 2026-07-03T16:30:16+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  real OpenAI `/v1/batches` and Anthropic `/v1/messages/batches` integration (submit, poll,
     retrieve) against v57's existing `batch_jobs`/`BatchUpstream` seam; the cache pre-filter
     (exact→semantic→vector, reusing v19's existing cache verbatim) applied per line item before it
     enters a batch; per-line-item usage records billed at the provider's real batch-discount rate
     (`list_price_usd` + `cost_usd` + `usage_source="batch"`); live double-pass verify against both
     real provider Batch APIs with a zero-regression floor on the sync path. Wiring a real
     `BatchProcessor` into `app.state.batch_processor` (today hardcoded `None`, see v57) is what turns
     v57's already-shipped, already-tested accumulation mechanism from inert into live.
Out: OpenRouter/Gemini/Bedrock/Azure/MiniMax batching (no confirmed native batch API for OpenRouter;
     others unverified — candidate follow-up milestone); cross-tenant request pooling; any change to
     v57's request-accumulation mechanism itself (BatchWindowBuffer/BatchWindowFlusher/the SSE
     lifecycle) beyond wiring a real processor into it — that mechanism is FROZEN, already
     gate=PASS'd, and out of this milestone's scope to redesign.

> Two open SPEC deltas from v57 are DIRECTLY relevant here and must be re-checked before either
> provider adapter ships (not folded away — see v57's RETRO.md / `add.py deltas`):
> 1. [batch-window-grouping] the claim/abandon/drain-DEL lifecycle has only ever been proven by racing
>    two in-process `BatchWindowBuffer` instances over a real Redis (`asyncio.gather`) — never a
>    genuinely deployed multi-replica gateway under load. Validate against real multi-replica traffic
>    once a processor exists to generate that traffic.
> 2. [batch-claim-drain-del] `_CLAIM_DUE_LUA`'s abandoned-marker DEL branch has no internal `kind`-
>    check — safe today only because `custom_id` is guaranteed fresh-per-attempt externally
>    (`batch_diversion.py`, one `append()` per custom_id). Whichever adapter task lands first here
>    must reconfirm that invariant still holds against its own retry/dedup behavior, or add the
>    `kind`-check then.

## Shared decisions & glossary deltas   (living — every task must honor these)
- OPT-IN / ADDITIVE ONLY — carried from v57: every new knob ships default-off; the existing
  /v1/chat/completions endpoint stays byte-identical for any tenant that hasn't opted into batching.
- ONE usage record per line item, never one per batch job — each item bills on its own outcome; a
  failed line bills $0. Matches the append-only, always-recomputable usage ledger invariant.
- Batch discount applied as a flat multiplier at record time (usage_source="batch"), not a catalog/
  rate-card schema change — keeps the tiered-rate-cards resolver (58f0008) untouched.
- SAVINGS COMPUTATION: every batch-sourced usage record additionally stores list_price_usd (what the
  line would have cost at the standard sync rate) alongside the real cost_usd. Savings shown to the
  tenant = sum(list_price_usd − cost_usd) over the period — auditable, recomputable, no separate
  estimation logic. batch-dashboard-surface's `savings_usd` placeholder (currently the constant
  "0.00") swaps to this real query the moment this milestone's billing-accuracy task lands the
  list_price_usd column — that task's own contract already promises this (v57 SPEC delta).
- TENANT ISOLATION: a batch job is submitted under ONE tenant's credential only (gateway key or their
  own BYOK key) — never mixed across tenants, mirroring every other per-tenant isolation invariant in
  this codebase.
- DESIGN-FOR-FAILURE: every new outbound IO path (batch submit / poll / retrieve, per provider) gets
  the same timeout + bounded retry + circuit-breaker treatment as every existing upstream call.
- GLOSSARY (from v57, unchanged): batch_job (queued/running/succeeded/failed/partially_failed), batch
  line item, list_price_usd, usage_source="batch".

## Shared / risky contracts (freeze these first)
- BatchUpstream port implementations — the submit/poll/retrieve seam itself shipped in v57's
  batch-job-store; this milestone implements it for real -> owning tasks openai-batch-adapter +
  anthropic-batch-adapter
- cache pre-filter ordering — WHERE in the submit path a cache hit short-circuits a line item before
  it's ever written into the batch payload -> owning task batch-cache-prefilter
- per-line-item billing shape (list_price_usd + cost_usd + usage_source="batch") + partial-failure
  handling -> owning task batch-billing-accuracy
- wiring `app.state.batch_processor` away from its v57-default `None` — the exact moment v57's
  diversion mechanism stops being inert; freeze which adapter task does this first and how the two
  provider adapters compose behind one seam -> owning task TBD at this milestone's own specify phase

## Tasks (breadth-first decomposition; detail lives in each TASK.md — carried forward verbatim from v57)
- [ ] batch-cache-prefilter   depends-on: none (v57's batch-job-store already shipped)      — wire the existing exact/semantic/vector response cache as a pre-filter before a line item is queued
- [ ] openai-batch-adapter    depends-on: none (v57's batch-job-store already shipped)      — real OpenAI /v1/batches: JSONL build, upload, submit, poll, parse results back to custom_id
- [ ] anthropic-batch-adapter depends-on: none (v57's batch-job-store already shipped)      — real Anthropic /v1/messages/batches: same shape
- [ ] batch-billing-accuracy  depends-on: openai-batch-adapter, anthropic-batch-adapter — per-line usage records at the batch-discount rate + list_price_usd + partial-failure billing
- [ ] batch-verify            depends-on: batch-billing-accuracy                  — live double-pass against real OpenAI + Anthropic batch endpoints; zero-regression floor on the sync path

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A line item already served by the existing response cache resolves inside the batch at $0 without ever reaching the provider; only genuine misses are queued upstream (← batch-cache-prefilter) (verify: batch-cache-prefilter §4 suite — cache-hit item never queued assertion)
- [ ] A batch job targeting an OpenAI model is genuinely submitted to OpenAI's native Batch API and results come back mapped per custom_id (← openai-batch-adapter) (verify: live-verify script against the real OpenAI Batch API, openai-batch-adapter §6)
- [ ] Same for a batch job targeting an Anthropic model against Anthropic's native Batches API (← anthropic-batch-adapter) (verify: live-verify script against the real Anthropic Batches API, anthropic-batch-adapter §6)
- [ ] Every completed line item produces exactly one usage record billed at the provider's real batch-discount rate with list_price_usd recorded; a failed line bills $0 (← batch-billing-accuracy) (verify: batch-billing-accuracy §4 suite + a usage-ledger reconciliation check)
- [ ] Live double-pass against real OpenAI + Anthropic batch endpoints is green twice, and the existing sync /v1/chat/completions floor shows zero regression (← batch-verify) (verify: scripts/live_v58_verify.py double-pass log, mirroring the v19 reliability-verify pattern)

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
