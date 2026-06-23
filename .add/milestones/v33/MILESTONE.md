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
- [ ] A non-finite or ≤0 drift threshold (and a negative check-interval) is rejected at startup, not silently disabled.        (← drift-threshold-validation)
- [ ] The unbilled-upstream-cost filter explicitly requires `cost_basis='provider'`; existing data is audited for breaches.   (← reconcile-cost-basis-filter)
- [ ] A residual (non-OpenRouter / no-frame) client-disconnect row carries a `provider_cost` or recorded recovery path — no silent $0 upstream charge.   (← disconnect-provider-cost)
- [ ] The images, embeddings, and proxy passthrough routers null-replace non-finite numbers without altering billed values.   (← passthrough-nonfinite-sanitize)

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
