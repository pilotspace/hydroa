# MILESTONE: GPT-Realtime cache-discount pricing

goal: A GPT-Realtime call through the proxy produces an accurate, billed usage_records row reflecting OpenAI's real dual-stream pricing (text $4/$16/$0.40-cached, audio $32/$64/$0.40-cached per 1M), and both GET /v1/models and GET /admin/catalog/models list GPT-Realtime with all 6 real prices
rationale: change-request — Tin asked to reuse catalog-pricing-fields' cached_input_usd_per_token
  infra for GPT-Realtime. GROUND-phase research (2026-07-01) found this is NOT a small additive
  reuse like MiniMax was: the realtime relay (proxy/api/realtime_relay_ws.py) does ZERO catalog
  lookups and ZERO usage recording today — a catalog price row alone would be an orphaned data
  point. GPT-Realtime also has TWO independently-priced token streams (text vs audio), which
  pricing_snapshots/usage_records cannot represent (one scalar price/count per stream today) —
  faithfully billing it requires a schema migration, not just a new nullable field. Tin explicitly
  chose the full-scope option (schema migration + relay billing wiring + catalog seed) over a
  smaller display-only or partial alternative.
stage: production · status: active · created: 2026-07-01T15:44:18+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Schema migration: pricing_snapshots gains audio-stream price columns (audio_prompt_usd_per_token/
    audio_completion_usd_per_token/audio_cached_usd_per_token, or equivalent); usage_records gains
    matching audio token-count columns, additive/nullable, zero impact on any existing text-only row.
  - GPT-Realtime seeded into the catalog (correct model id reconciled against
    Settings.realtime_relay_openai_model, currently "gpt-4o-realtime-preview") with all 6 real
    prices from OpenAI's published Realtime API pricing.
  - The realtime relay (currently discards OpenAI's response.done usage object entirely) parses
    input_token_details/output_token_details (text/audio/cached breakdowns) and records exactly
    one accurately-costed usage_records row per session/turn (design TBD at SPECIFY), using the
    new dual-stream schema + catalog price.
Out:
  - Any OTHER realtime provider (e.g. Gemini Live) — OpenAI GPT-Realtime only.
  - Full-duplex/barge-in relay semantics changes — this milestone only adds billing, it does not
    change the relay's turn-based/duplex behavior.
  - Deploy-time Envoy edge WS exposure — pre-existing, separate, already-known gap (see the
    realtime-voice milestone's own recorded delta); irrelevant to billing correctness.

## Shared decisions & glossary deltas   (living — every task must honor these)
- risk: high on every task in this milestone — this touches money-handling billing math AND a
  schema migration, both HARD-STOP-worthy categories per CLAUDE.md's design-for-failure rule;
  autonomy is lowered from the project default (auto) to conservative for every task here, so each
  major gate (contract freeze, verify) gets explicit human review, not auto-resolve.
- Additive-only schema discipline (matches every prior migration in this codebase) — new nullable
  columns, zero impact on any existing row/query; the existing single-stream (text-only) billing
  path must stay byte-identical for every model that ISN'T dual-stream.

## Shared / risky contracts (freeze these first)
- pricing_snapshots / usage_records dual-stream schema shape -> owning task
  gpt-realtime-schema-migration
- Realtime relay usage-parsing + billing-recording contract -> owning task
  gpt-realtime-relay-billing

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] gpt-realtime-schema-migration   depends-on: none                          — additive
      audio-stream price/count columns on pricing_snapshots + usage_records.
- [x] gpt-realtime-pricing-fields     depends-on: gpt-realtime-schema-migration — seed GPT-Realtime
      into the catalog with all 6 real prices, correct model id.
- [x] gpt-realtime-relay-billing      depends-on: gpt-realtime-pricing-fields  — parse the relay's
      discarded usage object, compute dual-stream cost, record exactly one usage_records row.

## Exit criteria (observable; map each to the task that delivers it)
- [x] pricing_snapshots/usage_records carry additive audio-stream columns; every pre-existing
      single-stream model's billing is byte-identical (verify: full regression suite green)   (← gpt-realtime-schema-migration)
- [x] GET /v1/models and GET /admin/catalog/models list GPT-Realtime with all 6 real prices,
      model id matching what the relay actually dials   (verify: catalog scenario tests)   (← gpt-realtime-pricing-fields)
- [~] A real GPT-Realtime session through the relay produces exactly one usage_records row whose
      cost_usd reflects the real text+audio+cached blend, not a flat/placeholder number   (verify: relay billing scenario tests + live-verify)   (← gpt-realtime-relay-billing)
      — PARTIALLY MET: the "relay billing scenario tests" half is done (18/18 green + adversarial
      review); the "live-verify" half was NOT performed in any of the 3 tasks — no task had a live
      OpenAI Realtime credential in its test environment. gpt-realtime-pricing-fields explicitly
      scoped a live smoke test OUT and flagged it forward as a SPEC delta. See Goal-met? below.

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched — no `add.py` / state.json / template changes in this milestone.
- skill   : untouched.
- book    : untouched (no docs/runbook changes shipped by any of the 3 tasks).
- gateway·catalog : `orm.py` (+6 additive Numeric/Integer columns), new Alembic migration
  `a4c6e8b0d2f3_gpt_realtime_audio_columns.py`, `gpt_realtime_seed.py`, `repository.py` (6-field
  pricing shape), `main.py` (CompositeCatalogSource wiring), `CatalogModel`/`MarkedUpModel`/
  `ModelItem`/`AdminCatalogModelItem` (+3 audio fields each), `Settings.realtime_relay_openai_model`
  default changed `gpt-4o-realtime-preview` → `gpt-realtime` (a real runtime behavior change).
- gateway·proxy : `openai_realtime.py` (`on_usage` capture seam + `_translate_realtime_usage`),
  `realtime_relay_ws.py` (`_make_relay_usage_callback` wiring). `gemini_live.py` untouched
  (confirmed byte-identical).
- gateway·usage : `recorder.py` (3 audio-tier `_safe_tier` reads, subset-based cost formula
  extension), `flusher.py` (mirrored INSERT extension).

### Cross-task evidence   (one row per task)
- gpt-realtime-schema-migration : gate=PASS · tests=2110 passed/7 skipped/0 failed (full suite,
  clean run) · residue=none — storage-only, no billing/read code wired yet at this task's scope.
- gpt-realtime-pricing-fields : gate=PASS · tests=55 targeted + 2108 full-suite passed · residue=
  note — live smoke test explicitly scoped OUT (no live OpenAI credential in test env), flagged
  forward as a SPEC delta; the model-id default change was NOT live-verified against OpenAI.
- gpt-realtime-relay-billing : gate=PASS · tests=18/18 new + 101 sibling passed · residue=note —
  adversarial review (spawned per standing review policy) found + fixed 1 CONFIRMED HIGH-severity
  bug (cached-token combined-vs-split double-count) and 1 LOW-severity doc-accuracy bug before
  this gate; no live smoke test performed (same no-credential constraint as above).

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] Exit criterion 1 (additive dual-stream schema, byte-identical single-stream billing) —
      satisfied by gpt-realtime-schema-migration's Cross-task evidence row (2110/2110 full
      regression green, additive-only columns).
- [x] Exit criterion 2 (catalog lists GPT-Realtime, all 6 real prices, correct model id) —
      satisfied by gpt-realtime-pricing-fields' Cross-task evidence row (targeted + full-suite
      green) — NOTE the model-id correctness itself rests on a WebFetch of OpenAI's public
      pricing page, not a live API round-trip.
- [ ] Exit criterion 3 (real session → one accurately-costed usage_records row) — NOT fully
      satisfied. The unit/scenario-test half is solid (18/18 + adversarial review, evidenced
      above); the "live-verify" half the criterion itself names was never performed anywhere in
      this milestone — no task ever had a live OpenAI Realtime credential available. This is a
      genuine, disclosed gap, not a silent pass: the arithmetic and wiring are verified against a
      documented (not live-confirmed) usage-object shape.
- goal: "A GPT-Realtime call through the proxy produces an accurate, billed usage_records row
  reflecting OpenAI's real dual-stream pricing... and both GET /v1/models and GET
  /admin/catalog/models list GPT-Realtime with all 6 real prices" — the catalog half is fully
  evidenced (criteria 1-2); the billing-accuracy half is evidenced at the unit/adversarial-review
  level but NOT live-confirmed against real OpenAI infrastructure (criterion 3, partial).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] Decide how to close criterion 3's live-verify gap: live-smoke-test now (needs an OpenAI
      Realtime credential in a reachable test env), or RISK-ACCEPTED-style close with a tracked
      forward delta — Tin's call, presented alongside this milestone-close ask.
- [ ] Open a PR from this Close ship-review (3 squashed/individual commits, one per task) — human
      reviews + merges.
- [ ] Bundle into the next release (not yet published; 5 milestones already queued releasable).
