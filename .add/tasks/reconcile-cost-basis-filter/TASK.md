# TASK: Scope the unbilled-upstream filter to cost_basis='provider'

slug: reconcile-cost-basis-filter · created: 2026-06-18 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/usage/application/reconciliation.py:reconcile_window` — the FROZEN v29 aggregate. The unbilled-upstream filter `provider_cost > 0 AND cost_usd = 0` appears at TWO sites: Query 1 (the `unbilled_upstream_cost` SUM `FILTER (...)` AND the `unbilled_rows` COUNT `FILTER (...)`), and Query 2 (the `by_source` breakdown `WHERE`). Both must change together.
  - `apps/gateway/src/gateway/usage/application/reconciliation.py` module docstring (L1-14) + `ReconciliationSummary.unbilled_upstream_cost` field doc (L45 "Σ provider_cost where provider_cost>0 AND cost_usd=0") — the doc already states the intent "Drift reconciles ONLY `cost_basis='provider'` rows"; the FILTER doesn't say so explicitly yet.
Context (working folder):
  - `apps/gateway/tests/reconciliation_aggregate/conftest.py:seed_row` — seeds a ledger row with explicit `cost_basis` + `provider_cost` (catalog default); lets a test seed the hypothetical `cost_basis='catalog'` row WITH `provider_cost>0, cost_usd=0` that the new clause must exclude.
  - `apps/gateway/tests/reconciliation_aggregate/test_reconciliation_aggregate.py` — 8 existing tests (RA1–RA8), baseline green; the new test joins them.
Honors (patterns / conventions):
  - v29 §3 reconcile_window is FROZEN → this is a CHANGE-REQUEST / SUPERSESSION (MILESTONE shared contract), recorded at this task's freeze; behavior-preserving on conformant data (catalog rows have NULL provider_cost so `provider_cost > 0` already excludes them).
  - aligns the FILTER with the module's already-documented invariant ("drift reconciles ONLY cost_basis='provider' rows").
  - keep money `Decimal` end-to-end; SELECT-only (read never writes — RA7 invariant preserved).
Anchors the contract cites:
  - `reconcile_window` Query 1 unbilled FILTER (×2: SUM + COUNT) and Query 2 `by_source` WHERE
  - the clause delta `AND cost_basis = 'provider'`
  - outputs: `unbilled_upstream_cost`, `unbilled_rows`, `by_source` (shape unchanged)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: cost_basis-scoped unbilled-upstream filter — make the provider-only intent explicit
Source: [SPEC · open → this] reconciliation-aggregate v29 NIT-5 (= drift-alert F5 dup) — "add a `cost_basis='provider'` guard to the `unbilled_upstream_cost` FILTER in `reconcile_window`".
Framings weighed: add `AND cost_basis = 'provider'` to BOTH filter sites (chosen — the FILTER becomes the source of truth, matching the documented invariant) · leave as-is relying on the `provider_cost IS NULL`-on-catalog recorder invariant (rejected — latent: a future back-fill turns a catalog row into a phantom leak) · post-query filter in Python (rejected — SQL aggregate is the single source of truth, and a Python pass would re-introduce float rounding).
Must:
<must>
  - The `unbilled_upstream_cost` SUM counts only rows with `provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider'`.
  - The `unbilled_rows` COUNT and the `by_source` breakdown use the SAME `cost_basis = 'provider'` clause — all three stay consistent.
  - Behavior is byte-identical on conformant data (catalog rows have NULL `provider_cost`, already excluded by `provider_cost > 0`); the 8 existing RA tests stay green.
</must>
Reject:
<reject>
  - (no new rejection) — this is a read-only filter tightening; no new error code. The existing inverted-window `ValueError` is unchanged.
</reject>
After:
<after>
  - A `cost_basis='catalog'` row carrying `provider_cost > 0 AND cost_usd = 0` (a hypothetical future back-fill) is NOT counted in `unbilled_upstream_cost`, `unbilled_rows`, or `by_source`.
  - A `cost_basis='provider'` row with `provider_cost > 0 AND cost_usd = 0` IS counted (unchanged).
  - The filter now matches the module's documented invariant ("drift reconciles ONLY cost_basis='provider' rows").
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The change is behavior-preserving TODAY only because the recorder never writes `provider_cost` on a catalog row. Lowest confidence: if an EXISTING production row already violates that (cost_basis='catalog' with provider_cost>0 & cost_usd=0), this tightening would STOP counting it as a leak. Cost if wrong: a currently-counted unbilled row drops out of the metric. Mitigation: the invariant is enforced at the recorder (v27 cost_basis); this change aligns the filter WITH that invariant, and such a row would itself be a recorder bug to fix at source, not silently count here. ACCEPT.
  - [x] BOTH filter sites must change (Query 1 SUM-FILTER + COUNT-FILTER, and Query 2 by_source WHERE) — a partial change desyncs the SUM/COUNT from by_source. Confirmed by reading both queries.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Catalog row with a provider cost is NOT a leak (the future-proofing)
  Given a usage row with cost_basis='catalog', provider_cost=5.00, cost_usd=0 in the window
  When reconcile_window runs over that window
  Then unbilled_upstream_cost == 0.00
  And unbilled_rows == 0
  And by_source has no entry for it

Scenario: Provider row with a provider cost and $0 billed IS a leak (unchanged)
  Given a usage row with cost_basis='provider', provider_cost=5.00, cost_usd=0 in the window
  When reconcile_window runs over that window
  Then unbilled_upstream_cost == 5.00
  And unbilled_rows == 1
  And by_source includes that row's usage_source with provider_cost 5.00

Scenario: Mixed window counts only the provider leak (consistency across all three outputs)
  Given one provider leak row (provider_cost=5.00, cost_usd=0) AND one catalog row (provider_cost=3.00, cost_usd=0)
  When reconcile_window runs
  Then unbilled_upstream_cost == 5.00 and unbilled_rows == 1
  And the by_source provider_cost total equals 5.00 (catalog row excluded everywhere)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
reconcile_window — unbilled-upstream FILTER (the only change):
  BEFORE (v29 §3, frozen):  provider_cost > 0 AND cost_usd = 0
  AFTER  (v30):             provider_cost > 0 AND cost_usd = 0 AND cost_basis = 'provider'

  Applied IDENTICALLY at all three sites:
    Query 1 · unbilled_upstream_cost  = SUM(provider_cost) FILTER (WHERE <AFTER>)
    Query 1 · unbilled_rows           = COUNT(*)           FILTER (WHERE <AFTER>)
    Query 2 · by_source               = ... WHERE <AFTER> GROUP BY usage_source

  ReconciliationSummary shape: UNCHANGED (same 9 fields, same types).
  Read-only: still two SELECT-only queries (RA7 read-never-writes preserved).
Schema: usage_records (read only) — columns provider_cost, cost_usd, cost_basis, usage_source. No DDL, no migration.
```

Status: FROZEN @ v30 — SUPERSEDES v29 reconcile-aggregate §3 (filter definition); approved under autonomy:auto (Tin pre-authorized "do all as recommended", 2026-06-18; low-risk, behavior-preserving on conformant data, non-security)

Supersession note (MILESTONE shared contract): the v29 §3 froze the unbilled filter as `provider_cost > 0 AND cost_usd = 0`. Per the supersession pattern, the frozen v29 TASK.md is left untouched; this v30 task records the new filter, which is behavior-preserving on today's data and only tightens against a future catalog back-fill.

Least-sure flag surfaced at freeze:
  ⚠ [spec] Behavior-preservation relies on the recorder invariant "catalog rows have NULL provider_cost". If a pre-existing row already violates it (catalog + provider_cost>0 + $0 billed), this tightening drops it from the leak metric. Why acceptable: such a row is itself a recorder bug; aligning the filter to the documented provider-only intent is correct, and the fix belongs at the recorder, not as a silent count here. Cost if wrong: one mis-recorded row stops showing as unbilled (would need the [SPEC] follow-up to audit existing data).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the new clause's exclude/include branches at all 3 output sites.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_ra9_catalog_provider_cost_not_counted_as_unbilled: seed cost_basis='catalog', provider_cost=5, cost_usd=0 / reconcile / assert unbilled_upstream_cost==0, unbilled_rows==0, by_source empty.  (RED today: old filter counts it)
  - test_ra10_provider_leak_still_counted: seed cost_basis='provider', provider_cost=5, cost_usd=0 / assert unbilled_upstream_cost==5, unbilled_rows==1, by_source has it.  (regression guard — green before & after)
  - test_ra11_mixed_window_counts_only_provider: seed provider-leak(5) + catalog(3) both $0-billed / assert unbilled==5, rows==1, by_source total==5.  (RED today: old filter would sum 8 / 2 rows)
</test_plan>

RED result (uv run pytest -k "ra9 or ra10 or ra11", DB up on :5433): 2 failed, 1 passed — red for the RIGHT reason:
  - ra9: unbilled_upstream_cost == Decimal('5.0000000000') (old filter counts the catalog row) ≠ 0 → FAIL.
  - ra11: unbilled_upstream_cost == Decimal('8.0000000000') (5 provider + 3 catalog) ≠ 5 → FAIL.
  - ra10: provider leak counted == 5.00 → PASS (regression guard, green before & after).

Tests live in: `apps/gateway/tests/reconciliation_aggregate/test_reconciliation_aggregate.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/reconciliation.py`
Strategy (ordered batches): 1. add `AND cost_basis = 'provider'` to Query 1's two FILTER clauses (SUM + COUNT) · 2. same clause on Query 2's by_source WHERE · 3. update the `unbilled_upstream_cost` field doc comment.
Safety rule (feature-specific): the clause must be IDENTICAL at all 3 sites (SUM/COUNT/by_source) or the outputs desync; read-only (no write).
Code lives in: `apps/gateway/src/gateway/usage/application/reconciliation.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
Built: 3 filter-clause edits + 1 doc-comment edit; 0 new deps; ruff clean; 11/11 RA suite green.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 11/11 `tests/reconciliation_aggregate` green (8 prior RA1–RA8 unchanged + new RA9/RA10/RA11) against real Postgres on :5433.
- [x] coverage did not decrease — +3 tests; the new clause's exclude (RA9/RA11) and include (RA10) branches are both exercised at all 3 output sites.
- [x] no test or contract was altered during build — build touched only `reconciliation.py`; the RA1–RA8 assertions are unchanged and stayed green (behavior-preservation proven, not asserted).
- [x] the green was EARNED, not gamed — self refute-read: RA9/RA11 were RED before the change (counted 5.00/8.00) and GREEN after (0/5.00); the catalog row is a real DB row, not a stub. RA10 guards the genuine-leak path. No fixture overfit (values chosen to distinguish 5 vs 8). The `Decimal('5.0000000000')` scale is the NUMERIC(20,10) column SUM (Decimal-equality is correct vs exact-string — the v29 F10 lesson).
- [x] concurrency / timing — N/A; two SELECT-only aggregate queries, no write, no shared state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — `'provider'` is a static SQL literal (no interpolation; the existing `# noqa: S608` covers the static clause); bound params unchanged; no new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — change confined to the usage/application aggregate; Decimal-end-to-end preserved.
- [x] reviewed and approved — autonomy:auto (Tin pre-authorized); low-risk, behavior-preserving supersession, non-security.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — no new symbol; the 3 edited FILTER/WHERE clauses are exercised by RA9/RA10/RA11 (and RA1–RA8 regression). The field-doc comment matches the new clause.
- [x] DEAD-CODE (code) — no new/orphaned symbol; pure clause edits inside the existing `reconcile_window`.
- [ ] SEMANTIC (prose / non-code) — N/A (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: autonomy:auto (Tin pre-authorized) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the unbilled-upstream metric staying stable across the deploy (no jump/drop = byte-identical on real data) · any future `cost_basis='catalog' ∧ provider_cost>0` rows appearing (would indicate a recorder-invariant breach now correctly excluded here).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · resolved-here] reconciliation-aggregate v29 NIT-5 / drift-alert v29 F5 — CLOSED by this task (explicit `cost_basis='provider'` at all 3 filter sites). The frozen v29 §3 is SUPERSEDED (note recorded in §3 here; v29 TASK.md left untouched per the supersession pattern).
  - [SPEC · open] one-time data audit: scan existing `usage_records` for any `cost_basis='catalog' AND provider_cost > 0` rows — if found, they are recorder-invariant breaches (v27) that this filter now correctly excludes, but they should be reconciled/fixed at source (evidence: the §3 least-sure flag; behavior-preservation assumed the invariant holds on current data).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
  - [ADD · open] a frozen MILESTONE shared contract (v29 reconcile_window §3) is correctly evolved by the SUPERSESSION pattern from a NEW task in a later milestone — record the new shape + a supersession note in the new task, leave the archived frozen TASK.md untouched; works even though the archived task is detached from the active engine registry (evidence: `--from-delta`/`drop-delta` rejected the archived slug, so the cross-reference was wired by hand in §1/§7).
  - [TDD · open] to red-test a DEFENSIVE filter whose guarded condition can't occur on conformant data, SEED the prohibited row directly (catalog + provider_cost>0) — the seed makes the latent bug observable now, turning "future-proofing" into an executable red→green (evidence: RA9/RA11 red on the seeded catalog row, green after the clause).
