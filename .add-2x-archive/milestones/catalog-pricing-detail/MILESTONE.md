# MILESTONE: Catalog pricing detail (OpenAI-compatible per-1M cost fields)

goal: Both GET /v1/models (client) and GET /admin/catalog/models (admin) expose full OpenAI-compatible per-token cost detail (input/output/cache) normalized to a familiar per-1M-token display, using MiniMax's real cached_tokens usage as the driving example
rationale: change-request — Tin asked "are /models api both admin and client show model's cost per
  token (1M - in-out-cache)???"; research confirmed neither endpoint showed per-1M units nor a cache
  price field. Tin then explicitly overrode the initially-recommended display-only scope
  ("fix that billing math also"), expanding this into both a display fix AND a real billing-math
  correctness fix (MiniMax's genuine cache-hit discount was never actually applied to cost_usd).
stage: production · status: active · created: 2026-07-01T13:10:38+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - GET /v1/models and GET /admin/catalog/models both gain prompt_usd_per_1m /
    completion_usd_per_1m / cached_input_usd_per_1m (additive, existing fields byte-identical).
  - MiniMax's real $0.06/1M cache-hit price is persisted on sync (pricing_snapshots
    .cached_input_usd_per_token, pre-existing column, never written before this milestone).
  - The pre-existing tiered-billing math in recorder.py (from tiered-token-billing /
    prompt-cache-passthrough) genuinely applies the cache discount to real usage_records.cost_usd
    — wiring-only, zero changes to recorder.py itself.
Out:
  - Seeding cache/discount pricing for any OTHER provider (e.g. OpenAI's GPT-Realtime, which
    carries a structurally identical real cached-input discount) — deferred to a future
    milestone via an open SPEC delta.
  - A DB migration to widen usage_records.cost_usd's Numeric(14,8) precision — the existing
    scale rounds a 9-significant-digit exact cost to 8 decimal places, which is correct, expected
    behavior for this milestone's scope, not a defect to fix here.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Additive-only, byte-identical-default discipline (matches every prior catalog field:
  modality/provider/input_modalities) — no breaking rename or removal of an existing response field.
- pricing_snapshots stays APPEND-ONLY — a cache-price-only change must produce a NEW row via
  _price_changed's extended 3-way comparison, never mutate an existing row.

## Shared / risky contracts (freeze these first)
- ModelItem / AdminCatalogModelItem additive schema + CatalogModel/MarkedUpModel additive
  dataclass fields -> owning task catalog-pricing-fields

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] catalog-pricing-fields   depends-on: none   — per-1M cost fields on both catalog endpoints + real MiniMax cache-discount billing fix   (PASS, 2026-07-01)

## Exit criteria (observable; map each to the task that delivers it)
- [x] Both GET /v1/models and GET /admin/catalog/models return the 3 new per-1M price fields, null (never 0/masked) with no cache price, existing fields byte-identical (verify: test_v1_models_shows_per_1m_fields_additively + test_admin_catalog_models_mirrors_pricing_fields)   (← catalog-pricing-fields)
- [x] A real recorded usage_records.cost_usd for a MiniMax call with cached_tokens reflects the real $0.06/1M cache-hit discount, lower than the pre-fix flat-rate result (verify: test_cached_tokens_billed_at_cache_rate, cost_usd=$0.00010498 < flat-rate $0.00014184)   (← catalog-pricing-fields)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/catalog : CatalogModel/MarkedUpModel gained cached_input_usd_per_token/
  cached_input_per_token additive fields; SqlAlchemyCatalogRepository._insert_snapshot now writes
  the pre-existing pricing_snapshots.cached_input_usd_per_token column (previously always NULL);
  _fetch_latest_prices/_price_changed extended to a 3-way price comparison so a cache-price-only
  change still appends a snapshot; list_active_models_with_markup computes the marked-up cache
  price; MINIMAX_SEED_MODELS gained the real $0.06/1M cache-hit price for all 3 entries.
- gateway/catalog/api : ModelItem/AdminCatalogModelItem gained prompt_usd_per_1m/
  completion_usd_per_1m/cached_input_usd_per_1m; router.py populates them at both construction
  sites (list_models, list_catalog_models).
- gateway/usage : NO CHANGE — recorder.py's pre-existing tiered-billing math (from
  tiered-token-billing/prompt-cache-passthrough) already correctly consumes the newly-written
  cache price; this milestone is wiring-only at that layer.
- tooling : untouched.
- skill   : untouched.
- book    : untouched.

### Cross-task evidence   (one row per task)
- catalog-pricing-fields : gate=PASS · tests=44/44 (7 new CPF1-7 + 37 across catalog/
  catalog_sync_trigger/tiered_token_billing, uncontended run) · residue=none blocking (adversarial
  refute-read verdict EARNED post-remediation — one process near-miss found and fixed this
  session: a post-freeze test/contract correction had not been re-crossed, tripping build_tampered;
  remediated via add.py phase tests → advance before gating; 2 competency deltas + 2 spec deltas
  recorded in TASK.md §7)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by the catalog-pricing-fields Cross-task evidence row
      (gate=PASS) — both exit criteria cite their own passing test by name inline
- goal: Both GET /v1/models and GET /admin/catalog/models expose full OpenAI-compatible per-1M
  cost detail (input/output/cache), and MiniMax's real cache-hit discount now genuinely lowers a
  billed usage_records.cost_usd instead of silently no-op'ing — MET: test_cached_tokens_billed_at_
  cache_rate proves a real recorded cost of $0.00010498 vs. the pre-fix flat-rate $0.00014184
  (~26% reduction), matching minimax-live-verify's real-call evidence from earlier this session.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Review the diff (5 src files + 2 pre-existing test-fixture widenings + 1 new test dir),
      commit with the mandated message format, and ask Tin for PR-creation permission.
- [ ] Open a PR from this branch; Tin reviews + merges.
- [ ] `add.py milestone-done catalog-pricing-detail` if not already auto-closed by the engine.
- [ ] Bundle into the next release cut (release.md) — human decides timing/bundling.
