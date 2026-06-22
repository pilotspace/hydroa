# MILESTONE: Reconciliation hardening — make leak-detection trustworthy

goal: a platform operator can trust the billing reconciliation signal — no nonsense config silently disables it, no false leak from catalog rows, no unexplained $0 on disconnect, and drift is visible across all tenants
rationale: new-major (v30). Closes the open reconciliation hardening deltas from the v29 §7 observe — file-cited in `drift-alert`, `reconciliation-aggregate`, `reconciliation-endpoint`. Relationship to the milestone map: *extends* the billing-accuracy theme (v27 precision → v28 robustness → v29 reconciliation → **v30 hardening**); *depends-on* the v29 reconciliation primitives (`reconcile_window`, `/admin/reconciliation`, `drift-alert`) it hardens. Bumped the UI↔BE coverage stub v30 → v31 (5th renumber, same documented pattern). Scope "Core 3 + operator-wide view" + operator-auth model "separate ops-auth surface" both confirmed by Tin 2026-06-18.
stage: production · status: active · created: 2026-06-18

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  config hardening (reject a nonsense drift threshold at startup) · filter correctness (`cost_basis`-scoped `unbilled_upstream_cost`) · disconnect billing completeness (`provider_cost` stamped on mid-stream client-disconnect rows) · operator-wide cross-tenant reconciliation **endpoint** behind a new platform-operator authority (separate ops-auth surface).
Out: the RA9 belt-and-suspenders read-only COUNT test + the 6-money-field str-type assertion sweep (low-value nits — deferred) · a **dashboard UI** for the operator view (endpoint only here; UI is the v31 UI↔BE coverage program) · any change to markup semantics or the drift-sign convention (frozen at v29) · alert delivery-channel changes (the `drift-alert` seam stays as-is).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **NEW glossary term — `platform operator`**: an authority that reads *across* tenants. The #1 invariant ("every tenant-owned query is tenant-scoped") gets exactly ONE named, audited exception — the cross-tenant reconciliation read — and it lives behind a **separate ops-auth surface**, never on a tenant JWT.
- **NEW glossary term — `ops-auth`**: the separate operator credential surface — its own issuer/signing key (NOT mintable via tenant signup), enforced on an edge-restricted path. Designed-for-failure per the IO rule (verification timeout/cache/fallback where a key fetch is involved).
- **`unbilled_upstream_cost` is provider-basis-only**: a counted row satisfies `cost_basis='provider' ∧ cost_usd=0`. The recorder invariant (catalog rows have NULL `provider_cost`) becomes an EXPLICIT filter clause, no longer relied upon implicitly.
- **Design-for-failure floor**: a nonsense monitor config (`inf`/`nan`/≤0 threshold) FAILS LOUD at startup — never silent-disables. (global IO/design-for-failure rule)

## Shared / risky contracts (freeze these first)
- **Platform-operator authority model = separate ops-auth surface** (DECIDED 2026-06-18, Tin) -> owning task `operator-wide-reconciliation`. A dedicated operator credential with its own issuer/signing key, NOT issuable through tenant signup, enforced on an edge-restricted path (`/ops/...` or the existing edge-blocked `/internal` family). Cross-tenant power NEVER rides a tenant JWT; the tenant-isolation invariant stays pure. Freeze the exact wire shape (issuer/claims/path/verification + failure modes) in this task's §3, human-approved, before any code.
- **`reconcile_window` §3 supersession** -> owning task `reconcile-cost-basis-filter`. The `cost_basis='provider'` filter clause re-freezes the frozen v29 aggregate via the supersession pattern (behavior-preserving on today's data — catalog rows already excluded by the NULL-provider_cost invariant).
- **OPEN at t4 specify**: whether the cross-tenant aggregation needs an all-tenants *mode* on the (now re-frozen) `reconcile_window` — a second supersession — vs a sibling query. Decide at `operator-wide-reconciliation` §1/§3.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] drift-threshold-validation     depends-on: none                          — reject non-finite / ≤0 `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` at config-load (fail loud); `should_start_drift_checker` stays the start predicate. (risk:low) [core/config.py, usage/application/drift_checker.py]
- [ ] reconcile-cost-basis-filter    depends-on: none                          — add `AND cost_basis='provider'` to the `unbilled_upstream_cost` filter in `reconcile_window`; change-request / supersession to the frozen §3. (risk:low) [usage/application/reconciliation.py]
- [ ] disconnect-provider-cost       depends-on: none                          — stamp `provider_cost` on client-disconnect / `GeneratorExit` mid-stream rows so the drift monitor surfaces them (carried v27 t4 silent-$0). (risk:medium — billing path) [proxy/application/use_cases.py]
- [ ] operator-wide-reconciliation   depends-on: reconcile-cost-basis-filter   — cross-tenant (all-tenants) reconciliation endpoint behind the new separate ops-auth surface; tenant admin/member denied (403). (risk:high — deliberate tenant-scoping exception + new authority; security HARD-STOP at verify) [usage/api/router.py, usage/api/schemas.py, new ops-auth]

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A nonsense drift threshold (`inf` / `nan` / ≤0) fails fast at startup with a clear error; the monitor never runs silently useless.        (← drift-threshold-validation)
- [ ] The unbilled-upstream filter counts only provider-basis rows; a catalog row carrying a `provider_cost` is never counted as a leak.        (← reconcile-cost-basis-filter)
- [ ] A request whose client disconnects mid-stream records its `provider_cost`, so a real upstream charge billed $0 surfaces in reconciliation drift.   (← disconnect-provider-cost)
- [ ] A platform operator reads cross-tenant reconciliation drift through the authorized ops-auth endpoint; a tenant admin/member is denied (403).   (← operator-wide-reconciliation)

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
