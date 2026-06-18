# MILESTONE: Billing precision — true per-tier cost on every call

goal: every proxied call is billed at the provider's true, per-tier cost: cached and reasoning tokens priced distinctly, provider-reported cost preferred when present, audio and streaming calls never silently under-billed
rationale: new-major (Tin, 2026-06-17). A coherent billing-fidelity theme that no active milestone covered (none was active); it serves the standing production goal's "accurate, billable cost tracking" half. The Decimal arithmetic is already exact (v12) — this milestone closes the four remaining *count/price-source* gaps that flat per-token billing still misses. Took the v27 slot ahead of the UI↔BE coverage program (renumbered to v28).
stage: production · status: active · created: 2026-06-17

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  bill cached-input and reasoning tokens at their own rates (not flat prompt/completion price);
     capture upstream-reported cost and PREFER it as the billed basis when present, falling back to
     catalog math when absent; derive STT audio duration server-side so per_second billing is accurate
     without `verbose_json`; guarantee exactly one billed usage record per stream even when the
     terminal usage frame is missing/partial.
Out: NO change to the exact Decimal arithmetic or the `numeric(14,8)` cost column (already exact, v12);
     NO new provider or modality; NO dashboard surface for the new cost/tier fields (that is the v28
     UI↔BE program) — backend ledger + raw payload only; NO retroactive re-billing of historical rows;
     NO change to the pre-flight TPM/budget *estimate* gating (`estimated_tokens` stays as-is — this is
     billing-only, not enforcement); NO model-side token re-counting when a provider already reports usage.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Cost basis = prefer-provider-fallback-catalog** (Tin, 2026-06-17 intake): when the upstream usage
  payload carries its own cost, the billed `cost_usd` is `provider_reported_cost × (1 + markup)`; when it
  does not, fall back to catalog math (price × tiered-tokens × markup). To keep the mixed-basis **auditable**,
  every ledger row records which basis produced it via a new `cost_basis` field (`provider` | `catalog`).
- **Byte-identical floor (v6/v9/v10/v11 invariant, preserved):** a usage payload WITHOUT token-tier
  details (`prompt_tokens_details` / `completion_tokens_details`) and WITHOUT a provider-reported cost
  bills exactly as it does today — same operand order, no new intermediate rounding. Tiering and
  provider-cost are additive branches gated on the presence of the new fields.
- **Billing keys on the SERVED model id** (v6, preserved): tiering, provider-cost, and duration all key
  on the served deployment's catalog id; nothing reads `response_body["model"]`.
- **Accuracy is never an availability gate** (v12, preserved): a missing tier price, an undecodable
  audio header, or an absent stream usage frame DEGRADES to a documented, logged fallback — it never
  fails the request or trips a circuit breaker. The product (the completion/embedding/transcription)
  ships; the bill is best-effort-exact with a recorded fallback marker.
- Glossary deltas (new terms): **cached-input token** · **reasoning token** · **provider-reported cost**
  · **cost basis** · **derived duration**.

## Shared / risky contracts (freeze these first)
- **Extended pricing-snapshot + usage-ledger shape** (token-tier prices + `cost_basis`/`provider_cost`
  columns + the richer `usage` dict the recorder consumes) -> owning task `tiered-token-billing`.
  This is the seam `provider-cost-reconciliation` and `stream-usage-completeness` both build on, so it
  freezes first and they depend on it (serializes the migration + recorder changes, no parallel conflict).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] tiered-token-billing        depends-on: none                  — extend the pricing snapshot with cached-input + reasoning prices; bill `prompt_tokens_details.cached_tokens` at the cached rate and `completion_tokens_details.reasoning_tokens` at the reasoning rate; non-tiered usage stays byte-identical. Owns the ledger/recorder schema seam.  ✅ DONE 2026-06-17 (gate PASS; full suite 1134 green; migration f3c8d1a6b9e4).
- [x] provider-cost-reconciliation depends-on: tiered-token-billing — capture upstream-reported cost (OpenRouter `usage.cost`, Bedrock, …); bill `provider_cost × (1+markup)` when present, else catalog math; stamp `cost_basis` on every row for audit.  ✅ DONE 2026-06-17 (gate PASS; full suite 1148 green; migration a7d2e9c4f1b6; default-off knob GATEWAY_OPENROUTER_USAGE_ACCOUNTING; fixed pre-existing Alembic env.py logger-clobber flake).
- [x] stt-duration-derivation     depends-on: none                  — derive audio duration server-side (decode the uploaded file header; no heavy dependency) so `per_second` STT billing is accurate even when the caller omits `verbose_json`; closes the silent-$0 leak.  ✅ DONE 2026-06-18 (gate PASS; full suite 1162 green; new `audio_duration.derive_duration_seconds` via tinytag, no migration; inf-harden change-request re-froze §3 @ v2). 2 follow-ups (duration magnitude cap; inf/nan response-passthrough) deferred → TASK.md §7.
- [ ] stream-usage-completeness   depends-on: tiered-token-billing  — guarantee exactly one billed usage record per stream even when the upstream omits/partials the terminal usage frame (documented fallback count + a flagged record); the richer tiered usage survives the SSE extractor.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A prompt-cached request is billed strictly less than the identical request with no cache hit, because `cached_tokens` are priced below fresh input.   (verify: pytest apps/gateway/tests/tiered_token_billing)   (← tiered-token-billing)
- [x] A reasoning-model response bills `reasoning_tokens` at the configured reasoning rate, visible on the usage ledger row.   (verify: pytest apps/gateway/tests/tiered_token_billing)   (← tiered-token-billing)
- [x] When an upstream returns its own cost, the ledger row's `cost_usd` is `provider_cost × (1+markup)` and `cost_basis = provider`; when it does not, `cost_basis = catalog` and the catalog math is used.   (verify: pytest apps/gateway/tests/provider_cost_reconciliation)   (← provider-cost-reconciliation)
- [x] An STT transcription submitted WITHOUT `verbose_json` produces a non-zero `per_second` cost matching the audio's true duration (no more silent $0).   (verify: pytest apps/gateway/tests/stt_duration_derivation)   (← stt-duration-derivation)
- [ ] A chat stream whose upstream omits the terminal usage frame still produces exactly one billed usage record with a recorded fallback marker (never a silent $0).   (verify: pytest apps/gateway/tests/stream_usage_completeness)   (← stream-usage-completeness)
