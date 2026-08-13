---
type: Task
title: baseline-and-verdict
status: done
milestone: evals-regression-gate
needs:
  - eval-run-executor.md
  - deterministic-scorers.md
gives:
  - S1 the baseline pin + verdict computation — thresholded pass/fail, equal-at-threshold decided explicitly
generated: { by: add/3.2.0, at: 2026-08-12 }
verified:
  - { by: "Tin Dang", at: 2026-08-13, act: freeze, authority: process, direction: "sha256:b3b0c7c9d979e935" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:07ac0a49ea25675e" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/baseline-and-verdict.d/runs/1.md }
  - { by: "Tin Dang", at: 2026-08-13, act: refreeze, authority: process, direction: "sha256:c36dc332a064e7cb" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:bdd7133a96fd03e6" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/baseline-and-verdict.d/runs/2.md }
  - { by: "Tin Dang", at: 2026-08-13, act: refreeze, authority: process, direction: "sha256:b02b3665f259e34f" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:96abcb2ef423cb3c" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/baseline-and-verdict.d/runs/3.md }
  - { by: "Tin Dang", at: 2026-08-13, act: gate, authority: process, outcome: PASS, receipt: /tasks/baseline-and-verdict.d/runs/3.md, brief: "sha256:96abcb2ef423cb3c", reason: "Self-driving verify per the refrozen contract (sha256:b02b3665f259e34f; reseal fixed the covers: token FORMAT — comma-separated ids in one covers:, not repeated · covers: segments — binding the four rejects, edges E1-E8, and A1 to tests that already prove them; additive, no rule/gives/test-logic change). 11 CHECKS green binding M1-M6, A1-A4, A6, E1-E8 and every reject: score fail-closed over the launch snapshot (refused/errored/unscoreable counted, never a pass), unscoreable-completed-not-a-pass, verdict strictly-worse=fail / strictly-better=pass / equal-rationals=pass via EXACT integer cross-multiply (R:FLOAT_TIE), no-baseline is the explicit no_baseline state (R:SILENT_PASS_NO_BASELINE), pin idempotent + rejects run-not-in-set + cross-tenant uniform 404 (R:CROSS_TENANT), durable across store instances (M6), score re-derived on demand (R:STALE_SCORE). pyright/ruff clean; migration-parity + guardrails no-new-tables green; four-manifest maintained. Human four-eyes owed at the PR gate." }
advised_by: backend-architect
---
## CARD
goal: pin a baseline, compare a candidate run, emit a thresholded pass/fail verdict
why: the milestone's payoff — a candidate strictly worse than baseline must FAIL, strictly better must PASS, and the boundary must not be decided by float luck
beat: done · next: add status

## RULES
<must>
- M1 A run's SCORE is the exact pair `(pass_count, total)`, computed ON DEMAND — never a persisted/cached number (R:STALE_SCORE). `total` = the run's LAUNCH SNAPSHOT cases ([[eval-run-executor]] A2: `snapshot_cases(created_at <= run.created_at)`), the fail-closed denominator. `pass_count` = snapshot cases whose result `status == "completed"` AND whose deterministic `ScoreResult.passed` is True, re-derived by calling the PURE scorer ([[deterministic-scorers]]) over `(case.assertion, result.response_text)`. A `refused`, `errored`, `pending`, or `unscoreable` case counts in `total` and NEVER as a pass (a run that did not answer a case cannot score as if it did).
- M2 A VERDICT compares a candidate run to its eval_set's pinned baseline run by EXACT INTEGER cross-multiplication — never float division (R:FLOAT_TIE): `PASS iff pass_c * total_b >= pass_b * total_c`, else `FAIL`. `>=` makes an equal-as-rationals candidate PASS (e.g. 3/5 vs 6/10 → `30 >= 30` → PASS); a strictly-worse candidate FAILS. The comparison is total-ordering-safe for unequal totals (candidate and baseline may have different snapshot sizes) because it compares rates, not counts.
- M3 Pinning: `PUT /v1/evals/sets/{set_id}/baseline {run_id}` designates a run as the set's baseline. The run MUST belong to that set AND the caller's tenant — otherwise a uniform 404 (R:CROSS_TENANT). Re-pin is IDEMPOTENT (upsert on `UNIQUE(eval_set_id)`): one baseline per set, promotable to a better run.
- M4 A verdict for a run whose set has NO pinned baseline is the EXPLICIT state `no_baseline` — never `pass`, never `fail` (R:SILENT_PASS_NO_BASELINE). A regression gate cannot render a verdict without a reference; the absence is surfaced, not defaulted green.
- M5 Every tenant-facing surface (pin · verdict) is tenant-scoped in the SAME query that resolves the row; an absent OR cross-tenant run/set is a uniform 404 with no existence oracle — reusing the /v1/evals `_authenticate` + `_err` envelope so the whole surface speaks one `{"error":{…}}` body.
- M6 The pinned baseline is DURABLE — persisted in `eval_baselines`, so a verdict is reproducible across a redeploy (a fresh store instance reads the same pin). Combined with M1's on-demand re-derivation, the same (candidate, baseline) always yields the same verdict on any host.
</must>
<reject>
- R:FLOAT_TIE the equal-at-boundary verdict is decided by float comparison (`rate_c >= rate_b` as floats), so 0.6 computed two ways can flip PASS/FAIL -> "R:FLOAT_TIE"
- R:SILENT_PASS_NO_BASELINE a run with no pinned baseline reports verdict `pass` -> "no baseline must be an explicit state, never a pass"
- R:STALE_SCORE a verdict is computed from a persisted/cached score column that can drift from the pure scorer -> "the score must be re-derived from case results + assertions, never read from a frozen number"
- R:CROSS_TENANT pinning or reading a verdict for a run/set outside the caller's tenant leaks existence or acts cross-tenant -> "R:CROSS_TENANT"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say who may pin or read a verdict; taking "any authenticated key of the tenant that OWNS the set may pin its baseline and read its verdicts — R7 has no separate eval-admin role; pinning is a tenant-scoped write like any other" -> if wrong, a role gate is missing and any key over-reaches. · probe: a tenant-B key pinning/reading a tenant-A set → 404, never acts.
- A2 [which] covers: S1 · the request does not say which cases form the denominator when candidate and baseline snapshots differ; taking "each run is scored over ITS OWN launch snapshot (A2 of the executor); candidate and baseline may legitimately have different `total`s if cases were added between launches, and M2's cross-multiply compares RATES so unequal totals are correct — NOT re-scoring both against the current set" -> if wrong, adding a case would silently re-score a historical baseline. · probe: a run whose set gained a case after launch scores over its own snapshot size, not the live set size.
- A3 [when] covers: S1 · the request does not say where the pass/fail boundary falls; taking "equal-as-rationals → PASS (`>=`), decided by EXACT integer cross-multiplication so a candidate that numerically matches the baseline (3/5 vs 6/10) is never flipped to FAIL by IEEE rounding — the milestone's whole point" -> if wrong, a float last-bit decides a gate. · probe: candidate 3/5 vs baseline 6/10 → PASS, and the code path multiplies integers (no `/` on the score before comparison).
- A4 [absent] covers: S1 · the request does not say what a missing baseline or an empty run means; taking "no pinned baseline → verdict `no_baseline` (M4); an empty baseline (`total_b == 0`, a vacuous run) → the cross-multiply yields `pass_c*0 >= 0*total_c` i.e. `0 >= 0` → PASS for any candidate, the documented degenerate; a `completed` case whose assertion.kind is unsupported scores `passed=False` (scorer M4) → not a pass" -> if wrong, an unpinned or empty gate reads green. · probe: verdict with nothing pinned returns `no_baseline` (not pass); an unscoreable completed case does not increment pass_count.
- A5 [order] covers: S1 · [order] n/a · the verdict is a single scalar comparison of two `(pass,total)` pairs — order-independent; the per-case detail list, if surfaced, reuses the executor's creation-order (A5) and adds no new ordering rule.
- A6 [experience] covers: S1 · the request does not say who reads a verdict and what makes a FAIL hard for them; taking "a CI job / operator reads pass|fail|no_baseline; a FAIL response names BOTH scores (candidate `(pass,total)` and baseline `(pass,total)`) so the regression is actionable at a glance — payload-free, never a bare boolean" -> if wrong, a failing gate is an unactionable red dot. · probe: a `fail` verdict response carries both score pairs + the baseline run id, not just `"fail"`.

## PLAN
contract:
```
# NEW module gateway/evals/verdict/ — aggregation + comparison. Pure comparison core; the
# I/O adapter reuses the run store (snapshot_cases + list_case_results) and the PURE scorer.
# NEW table eval_baselines(id, tenant_id, eval_set_id UNIQUE, run_id, pinned_at)  [four-manifest]

RunScore = tuple[int, int]            # (pass_count, total) — EXACT; no float rate at rest

def score_run(snapshot_cases, results_by_case_id, scorer) -> RunScore   # PURE over inputs
def decide(candidate: RunScore, baseline: RunScore) -> Literal["pass","fail"]:
    pass_c, total_c = candidate; pass_b, total_b = baseline
    return "pass" if pass_c * total_b >= pass_b * total_c else "fail"   # integer-only

PUT /v1/evals/sets/{set_id}/baseline  {run_id:"er_…"}
    -> 200 { eval_set_id, baseline_run_id, pinned_at }
    -> 404 ERR_EVAL_SET_NOT_FOUND (absent/cross-tenant set) | ERR_EVAL_RUN_NOT_FOUND (run not in set/tenant)
GET /v1/evals/runs/{run_id}/verdict
    -> 200 { run_id, score:{passed,total},
             baseline: { run_id, score:{passed,total} } | null,
             verdict: "pass" | "fail" | "no_baseline" }
    -> 404 ERR_EVAL_RUN_NOT_FOUND (absent/cross-tenant run)
```
scope:
- migrations/versions/<rev>_eval_baselines.py + tests/migrations EXPECTED_TABLES + migrations/env.py import + tests/guardrails NOT-IN allow-list  (four-manifest rule [[gateway-new-table-four-manifests]])
- src/gateway/evals/verdict/{domain,application,infrastructure,api}/  — port, pure decide(), score_run(), SqlAlchemy baseline store, router
- REUSE src/gateway/evals/scoring (Scorer), src/gateway/evals/runs (EvalRunStore.snapshot_cases + list_case_results), src/gateway/evals/api.router (_authenticate,_err,_unix), src/gateway/evals/wire_id (parse_run/parse_set)
- src/gateway/core/error_catalog.py — REUSE EVAL_RUN_NOT_FOUND + EVAL_SET_NOT_FOUND (no new spec unless a distinct verdict error emerges)
- src/gateway/main.py — include verdict router + wire the baseline store
considered-and-rejected: a nullable `baseline_run_id` column on `eval_sets` — fewer manifests, but it re-opens the `done` eval-set-store table, needs a circular FK (eval_runs.eval_set_id ↔ eval_sets.baseline_run_id), and records no pin timestamp. The dedicated table keeps eval_sets frozen and gives an auditable `pinned_at` (SOC 2).

## EDGES
- E1 strictly-worse candidate FAILS — candidate 2/5 vs baseline 4/5.
- E2 equal-as-rationals candidate PASSES via `>=`, EXACT — candidate 3/5 vs baseline 6/10 (`30 >= 30`), no float flip (R:FLOAT_TIE).
- E3 strictly-better candidate PASSES — candidate 5/5 vs baseline 3/5.
- E4 no baseline pinned → `no_baseline`, never pass (M4, R:SILENT_PASS_NO_BASELINE).
- E5 fail-closed denominator — a run with a refused + an errored case scores them into `total` but not `pass_count` (M1).
- E6 an unscoreable `completed` case (unsupported assertion.kind) scores `passed=False` → not a pass (M1 + scorer M4).
- E7 re-pin is idempotent — pinning run B after run A leaves exactly one baseline (B) for the set (M3).
- E8 durability — a pin written by one store instance is read by a fresh one (redeploy proxy, M6).

## CHECKS
- test_score_is_completed_scorer_pass_over_snapshot_total · covers: M1, A2, E5, R:STALE_SCORE · a 5-case run (3 completed+scorer-pass, 1 errored, 1 refused) scores exactly (3,5) re-derived from results+assertions (no stored number); non-completed cases are in the denominator, not the numerator.
- test_unscoreable_completed_case_is_not_a_pass · covers: M1, A4, E6 · a `completed` case with an unsupported assertion.kind scores passed=False → pass_count excludes it.
- test_verdict_strictly_worse_fails · covers: M2, E1 · a candidate 2/5 vs baseline 4/5 → "fail".
- test_verdict_strictly_better_passes · covers: M2, E3 · a candidate 5/5 vs baseline 3/5 → "pass".
- test_verdict_equal_rationals_pass_exact_no_float · covers: M2, A3, E2, R:FLOAT_TIE · candidate 3/5 vs baseline 6/10 → "pass" (numerically equal; the decision is integer cross-multiply, not float division).
- test_no_baseline_is_explicit_state_not_pass · covers: M4, E4, R:SILENT_PASS_NO_BASELINE · a verdict with nothing pinned → "no_baseline" (never "pass").
- test_pin_and_repin_idempotent_one_baseline · covers: M3, E7 · pin run A then run B → the set's baseline is B; verdict compares against B; exactly one row.
- test_pin_rejects_run_not_in_set · covers: M3, R:CROSS_TENANT · pinning a run that belongs to a DIFFERENT set of the same tenant → 404 ERR_EVAL_RUN_NOT_FOUND.
- test_cross_tenant_pin_and_verdict_uniform_404 · covers: M5, A1, R:CROSS_TENANT · tenant B pinning/reading tenant A's set/run → 404, no oracle, no write.
- test_baseline_durable_across_store_instances · covers: M6, E8 · a pin written via one store is read via a fresh store instance (redeploy proxy).
- test_fail_verdict_reports_both_scores · covers: A6, E1 · a "fail" response carries candidate + baseline (pass,total) and the baseline run id, not a bare string.
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
