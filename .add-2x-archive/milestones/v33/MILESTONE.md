# MILESTONE: Reconciliation & disconnect-billing hardening

goal: Make the reconciliation and disconnect-billing pipeline trustworthy under bad config and partial streams: nonsense config fails loud at startup, the unbilled-upstream leak filter is explicitly provider-scoped, residual non-OpenRouter disconnect rows become recoverable, and no passthrough response can crash on non-finite numbers.
rationale: new-major — a coherent correctness/safety slice triaged from the 44-delta backlog after the 0.2.0 release. Cluster 1 (reconciliation & disconnect-billing correctness) + the Cluster-5 non-finite sanitization carry-over: all design-for-failure fixes that directly serve the project goal (accurate, billable cost tracking) and are self-contained (no infra/cert dependencies, unlike the deferred operator-control-plane and UX-polish clusters). 3 of 4 tasks were pre-scaffolded as stubs on 2026-06-18.
stage: production · status: active · created: 2026-06-23

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  fail-loud validation of the reconciliation drift knobs at startup; an explicit `cost_basis='provider'` clause on the unbilled-upstream leak filter (+ one-time data audit); a recoverable `provider_cost` / recovery path on residual non-OpenRouter & no-frame client-disconnect rows; non-finite (inf/nan) sanitization on the images, embeddings, and proxy passthrough routers (matching the shipped STT fix).
Out: operator control plane (Envoy XFCC-strip infra, operator dashboard view, cert issuance/rotation, XFCC parser hardening, cross-tenant drift export) — Cluster 2, deferred; routing-editor completeness & admin-read UX polish — Clusters 3+4, deferred; recovery-observability (recovery-lag gauge, zero-delta noop semantics, cross-tenant aggregate index) — left to the observe loop as backlog deltas; any change to the v30 OpenRouter recovery chain itself (it shipped and works — this milestone covers the RESIDUAL gap only).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Design-for-failure = fail LOUD on nonsense config, never silent-disable (the drift-knob validation rule; the WHY behind exit criterion 1).
- The recorder invariant — `provider_cost` is non-NULL only on `cost_basis='provider'` rows — is the load-bearing fact behind the filter fix; the explicit clause makes it auditable from the query, not just the recorder.
- No silent $0 upstream charge: a disconnect that cost money upstream must be surfaced to reconciliation (stamped cost or a recorded recovery path), never billed $0 and forgotten.
- Non-finite sanitization replaces inf/nan with null AFTER billing, never alters the billed numbers (carries the frozen v28 stt-nonfinite decision to the sibling routers).

## Shared / risky contracts (freeze these first)
- `reconcile_window` unbilled-upstream FILTER — owning task `reconcile-cost-basis-filter` (CHANGE-REQUEST to the frozen `reconciliation-aggregate` §3; behavior-preserving on current data).
- disconnect-row `provider_cost` stamp / recovery semantics for non-OpenRouter — owning task `disconnect-provider-cost` (must compose with, not alter, the v30 OpenRouter recovery chain).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] drift-threshold-validation       depends-on: none   — reject non-finite/≤0 `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` and negative `_CHECK_INTERVAL_SECONDS` at startup with a clear error.
- [ ] reconcile-cost-basis-filter   depends-on: none   — add an explicit `cost_basis='provider'` clause to the unbilled-upstream filter + a one-time audit of existing rows for `cost_basis='catalog' AND provider_cost>0`.
- [ ] disconnect-provider-cost      depends-on: none   — re-grounded post-v30: give residual non-OpenRouter / no-frame client-disconnect rows a recoverable `provider_cost` or recorded recovery path so the drift monitor surfaces them.
- [ ] passthrough-nonfinite-sanitize depends-on: none  — apply `sanitize_non_finite` to `images_router`, `embeddings_router`, and `proxy/api/router` (the sibling `JSONResponse(allow_nan=False)` render paths).

## Exit criteria (observable; map each to the task that delivers it)
- [x] A non-finite or ≤0 drift threshold (and a negative check-interval) is rejected at startup, not silently disabled.        (← drift-threshold-validation)
- [x] The unbilled-upstream-cost filter explicitly requires `cost_basis='provider'`; existing data is audited for breaches.   (← reconcile-cost-basis-filter)
- [x] A residual (non-OpenRouter / no-frame) client-disconnect row carries a `provider_cost` or recorded recovery path — no silent $0 upstream charge.   (← disconnect-provider-cost)
- [x] The images, embeddings, and proxy passthrough routers null-replace non-finite numbers without altering billed values.   (← passthrough-nonfinite-sanitize)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/core : `config.py` — new `_validate_check_interval` field-validator (negative `GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS` rejected at startup; the ≤0/non-finite threshold guard already shipped v30).
- gateway/usage : `reconciliation.py` — new READ-ONLY audits `audit_cost_basis_breaches` (catalog+provider_cost breach) + `audit_unrecovered_disconnects` (zero-estimate residue) + `CostBasisBreach`/`UnrecoveredDisconnect`; explicit `cost_basis='provider'` on `reconcile_by_tenant`'s unbilled FILTER. `recorder.py` — `disconnect_estimate` extra + stamp rule (residual partial disconnect → markup-stripped provider_cost, cost_usd=0, provider basis).
- gateway/proxy : `ports.py` (UsageRecordExtras.disconnect_estimate) + `use_cases.py` (disconnect handler computes/forwards disconnect_estimate, gen-id-absence gated) + `images_router.py`/`embeddings_router.py`/`router.py` (sanitize_non_finite at the JSONResponse render sites).
- tooling/skill/book : untouched (no engine/skill/docs changes this milestone).

### Cross-task evidence   (one row per task)
- drift-threshold-validation : gate=PASS · tests=3 green (config validator) · residue=none
- reconcile-cost-basis-filter : gate=PASS · tests=3 green · residue=none (re-grounded: guard already present → delivered the audit + belt-and-suspenders)
- disconnect-provider-cost : gate=PASS · tests=7 green · residue=none (refute-read caught + fixed a double-count BLOCKER; 87 v30 recovery/streaming tests still green)
- passthrough-nonfinite-sanitize : gate=PASS · tests=4 green · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — criterion 1 ← drift-threshold-validation; 2 ← reconcile-cost-basis-filter; 3 ← disconnect-provider-cost; 4 ← passthrough-nonfinite-sanitize.
- goal: the reconciliation + disconnect-billing pipeline is trustworthy under bad config and partial streams — proven by the full gateway suite at 1385 green with nonsense config rejected at boot, the leak filter explicitly provider-scoped + auditable, residual disconnects surfaced (stamp or audit, double-count-proof), and passthrough routers null-safe on inf/nan.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
