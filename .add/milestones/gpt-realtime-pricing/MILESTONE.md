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
- [ ] gpt-realtime-schema-migration   depends-on: none                          — additive
      audio-stream price/count columns on pricing_snapshots + usage_records.
- [ ] gpt-realtime-pricing-fields     depends-on: gpt-realtime-schema-migration — seed GPT-Realtime
      into the catalog with all 6 real prices, correct model id.
- [ ] gpt-realtime-relay-billing      depends-on: gpt-realtime-pricing-fields  — parse the relay's
      discarded usage object, compute dual-stream cost, record exactly one usage_records row.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] pricing_snapshots/usage_records carry additive audio-stream columns; every pre-existing
      single-stream model's billing is byte-identical (verify: full regression suite green)   (← gpt-realtime-schema-migration)
- [ ] GET /v1/models and GET /admin/catalog/models list GPT-Realtime with all 6 real prices,
      model id matching what the relay actually dials   (verify: catalog scenario tests)   (← gpt-realtime-pricing-fields)
- [ ] A real GPT-Realtime session through the relay produces exactly one usage_records row whose
      cost_usd reflects the real text+audio+cached blend, not a flat/placeholder number   (verify: relay billing scenario tests + live-verify)   (← gpt-realtime-relay-billing)

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
