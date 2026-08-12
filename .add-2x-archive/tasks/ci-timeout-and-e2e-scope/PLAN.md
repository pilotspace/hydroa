# PLAN: Make the CI checks honestly green: raise the gateway timeout past the suite budget and return kind-e2e to opt-in

slug: ci-timeout-and-e2e-scope · created: 2026-08-07 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: the two CI checks that gate a merge can actually reach a verdict — the `gateway` job is given a wall-clock budget larger than the suite it runs, and `kind-e2e` stops reporting on pull requests it was never meant to gate.

Framings weighed:
- **manifest-guard both facts, then fix both** (chosen) — `.github/workflows/` is already treated as a drift-prone manifest by `tests/migrations/test_ci_workflow_parity.py`, whose own docstring names "make CI green by deleting a step" as the thing it stands against. A timeout too small to finish, and a permanently-red check nobody can act on, are the same failure in a different column: a check that *looks* enforced and enforces nothing. Extending that existing guard costs two functions and keeps the whole CI contract in one asserted place.
- *just edit the YAML* — rejected: this milestone's entire product is defensible change management. An unguarded edit re-opens the exact drift the guard exists to catch the moment someone tunes the number back down to save metered minutes.
- *narrow kind-e2e's paths instead of removing the PR trigger* — rejected: `ci-restoration` CR v2 is Tin-approved and explicit — "It stays opt-in (`workflow_dispatch`) and runs before a release cut." Honor the frozen amendment; do not invent a third position under it.

Must:
<must>
  - M1: the `gateway` job's `timeout-minutes` is at least `MIN_GATEWAY_TIMEOUT_MINUTES` (75) — a budget that carries the observed ~37-min suite on a 12-core dev host through a 4-core `ubuntu-latest` runner, plus install · lint · typecheck · two allow-list gates · the migration-parity step.
  - M2: `kind-e2e` is reachable ONLY by `workflow_dispatch` — its `on:` block carries no `pull_request` and no `push` trigger, so it can never report a status on a PR it was not meant to gate.
</must>
Reject:
<reject>
  - a `gateway` timeout at or below the suite's own wall-clock -> "job cancelled mid-suite; the check reports `cancelled`, never a verdict"
  - a `kind-e2e` PR trigger (however path-filtered) -> "a 0-green-in-15 check reports red on every PR touching `apps/**`, training every reviewer to ignore checks"
</reject>
After:
<after>
  - a full `gateway` run reaches its final step and reports pass/fail on its own merits rather than being cancelled by the runner
  - a PR touching only `apps/**` shows exactly two checks — `gateway` and `dashboard` — and no permanently-red third
  - the two facts are asserted by the repo's own suite, so a later tuning-down is red, not silent
</after>
Boundary: none — no external input. Both assertions are pure text/YAML reads over repo files (no network, no docker, no GitHub API), matching the existing parity module's stated discipline.
<assumptions>
  ⚠ 75 minutes is enough. The 37-min figure is a **12-core dev host** measurement; `ubuntu-latest` has 4 cores, so pytest-xdist gets ~⅓ the workers and the suite could run materially longer. If wrong: the run is cancelled again at 75 instead of 30 — same symptom, one more burnt run, and the fix is a second bump. Deliberately biased high over todo #93's suggested 60 so the FIRST post-fix run yields a real measured wall-clock instead of another content-free `cancelled`.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
Manifest: .github/workflows/ci.yml
  jobs.gateway.timeout-minutes  ->  int >= 75        (today: 30)
  jobs.dashboard               ->  UNCHANGED (10 min; its steps run in ~3)

Manifest: .github/workflows/kind-e2e.yml
  on  ->  { workflow_dispatch: {} }                  (today: workflow_dispatch + pull_request[paths])
  jobs.kind-e2e.*              ->  UNCHANGED (steps, 45-min bound, cluster wiring untouched)

Guard: apps/gateway/tests/migrations/test_ci_workflow_parity.py
  + MIN_GATEWAY_TIMEOUT_MINUTES = 75
  + test_gateway_timeout_outlasts_the_suite()    covers: M1
  + test_kind_e2e_is_dispatch_only()             covers: M2
  existing _load / REPO_ROOT helpers reused verbatim; no existing test touched.
```

<!-- AMENDED 2026-08-07 by CR v2 (Tin-approved, after the first real runner measurement).
     The frozen line above reads `timeout-minutes -> int >= 75`. That number was the §1
     ⚠ assumption and it was WRONG: run 31197251730 was cancelled at 74m17s with the
     SERIAL suite still running (steps 1-9 cost 32s combined, so the budget is all test
     time). The frozen line is left untouched per the supersession pattern; what actually
     ships is:
       - `make ci` now runs a new `make test-ci` = `pytest -n 4 --dist loadscope`, NO
         --reruns. Same tests, same strictness, sized to ubuntu-latest's 4 cores.
         `-n 4` is explicit, never `-n auto`, which would resolve to the HOST's core
         count and blow past the 1..12 Redis db mapping in tests/_redis_env.py.
       - `jobs.gateway.timeout-minutes -> int >= 60` (MIN_GATEWAY_TIMEOUT_MINUTES = 60),
         ~2.4x headroom over the ~20-25 min the parallel run is expected to take.
       - `jobs.gateway.steps[Tests].run -> "make test-ci"`, so CI still runs exactly
         `make ci` and nothing divergent (the release-integrity anchor); the existing
         test_ci_enforces_every_make_ci_gate guard checks this automatically because it
         reads `make ci`'s prerequisites rather than a hardcoded list.
     Scope grows by `Makefile`. Rejected alternative: raise the cap to 150 and keep the
     suite serial — it preserves strict-serial semantics but bills ~2.5h of metered
     minutes per PR and makes the feedback loop unusable. Rejected alternative: matrix
     sharding — better feedback still, but real workflow surgery and shard-balancing,
     disproportionate to a task scoped as a timeout fix. -->

Grounding (real symbols, verified in-context):
- `jobs.gateway.timeout-minutes: 30` at `.github/workflows/ci.yml` — last 5 `main` runs all `completed / cancelled` at ~30m20s (`gh run list --branch main`), i.e. the runner kills it, the suite never reports.
- `.github/workflows/kind-e2e.yml`'s `on:` carries `pull_request.paths` including `"apps/**"` — which matches essentially every PR in this repo, contradicting the file's OWN header comment ("Heavy + opt-in by design… NOT in the fast `ci.yml` lane") and `ci-restoration` CR v2. History: **0 green in 15 attempts** since 2026-07-20.
- `_load`, `REPO_ROOT`, `CI_WORKFLOW` in `test_ci_workflow_parity.py` — the reusable seam; this task adds a `KIND_E2E_WORKFLOW` sibling constant and two functions.

Target (measurable): the two new guards run red on the current tree and green after the manifest edits; the full `tests/migrations/` regression floor stays green; `gh run list --branch main --limit 1` on the merge commit reports a status other than `cancelled` (confirmed post-merge, not by a test — a workflow's runtime behavior is only observable on a real run).
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `.github/workflows/ci.yml` · `.github/workflows/kind-e2e.yml` · `apps/gateway/tests/migrations/test_ci_workflow_parity.py`
Regression floor: `apps/gateway/tests/migrations/` — the whole parity + migrations module (fast, no network, and the module this task extends)
Persona (optional): `.add/personas/sre-reliability-engineer.md` — "Reliability is a feature — verify the environment, degrade safely, never guess." A check that cannot reach a verdict is precisely this persona's *opaque failure mode reaching production as a process gap, not bad luck*; and "environment assumptions decay" is exactly the 12-core-vs-4-core assumption flagged in §1.

Least-sure flag surfaced at freeze: [contract] the `75` in `MIN_GATEWAY_TIMEOUT_MINUTES`. It is an extrapolation from a dev-host measurement across a core-count change, not an observed runner wall-clock — the one number here that no test in this task can prove. It is deliberately above todo #93's suggested 60 to buy a real measurement on the first run; tightening it once a green run records the actual is a follow-up, not this task.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_gateway_timeout_outlasts_the_suite: arrange — load `ci.yml`; act — read `jobs.gateway.timeout-minutes`; assert it is an int `>= MIN_GATEWAY_TIMEOUT_MINUTES` (75), with a failure message naming the cancelled-run symptom so a future reader knows why the floor exists · covers: M1, R:cancelled-mid-suite
  - test_kind_e2e_is_dispatch_only: arrange — load `kind-e2e.yml`; act — read its `on:` keys; assert the key set is exactly `{workflow_dispatch}`, i.e. neither `pull_request` nor `push` is present · covers: M2, R:permanently-red-PR-check
</test_plan>

Both tests must be red on the pre-edit tree: `timeout-minutes` is `30` (< 75) and `on:` contains `pull_request`. Verified red before Build, per §4's floor.

Build-guidance (prose, NOT gated): assert on the parsed YAML via the module's existing `_load`, never a regex over the raw text — `timeout-minutes: 30` and `timeout-minutes: 30 # comment` must read identically, and the `on:` key is famously YAML-1.1-truthy (`on` can parse as the boolean `True` under some loaders). Confirm which key `yaml.safe_load` actually yields for `on:` and key off the real parse result rather than assuming the string `"on"`.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/migrations/test_ci_workflow_parity.py` · MUST run red before Build.

VERIFIED IN-CONTEXT (settles the build-guidance above): `yaml.safe_load` parses a workflow's `on:` key as the **boolean `True`**, not the string `"on"` — YAML 1.1 truthiness. `yaml.safe_load(kind_e2e)` yields top-level keys `['name', True, 'permissions', 'jobs']`. `workflow["on"]` therefore raises `KeyError`, and a test written that way would be red for the wrong reason and is one lazy edit away from being made vacuously green. The guard must read the `True` key (with the `"on"` string accepted as a fallback so a future YAML-1.2 loader swap does not silently blind the test).

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned, with one grounding fact that changed how the test had to be written. `yaml.safe_load` parses a workflow's `on:` key as the **boolean `True`** (YAML 1.1 truthiness), not the string `"on"` — verified in-context before writing the test, and captured in the `_triggers()` helper, which reads the boolean key and accepts `"on"` only as a forward-compatible fallback. A guard written the obvious way (`workflow["on"]`) would have raised `KeyError`, read as red-for-the-right-reason, and been one lazy "fix" away from vacuous. Everything else is the frozen contract verbatim: two constants, two tests, two manifest edits, no existing test touched.
Code lives in: `.github/workflows/ci.yml` · `.github/workflows/kind-e2e.yml` · `apps/gateway/tests/migrations/test_ci_workflow_parity.py`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

Evidence:
- RED before build: `test_gateway_timeout_outlasts_the_suite` + `test_kind_e2e_is_dispatch_only` both failed on the pre-edit tree; the 3 pre-existing parity tests stayed green.
- GREEN after build: `tests/migrations/` **32 passed** (the whole declared regression floor, parity module included).
- `ruff format --check` clean · `ruff check` clean · `pyright` 0 errors on the touched test file.
- Post-merge confirmation still owed (not test-observable): the first `main` run must report a status other than `cancelled`, and its wall-clock recorded to tighten the 75 floor.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: **mutation-tested both new guards in both directions.** (1) `timeout-minutes: 75 -> 74` — one minute below the floor, the tightest possible boundary — turned `test_gateway_timeout_outlasts_the_suite` red; (2) re-adding `pull_request: paths: ["apps/**"]` to `kind-e2e.yml` turned `test_kind_e2e_is_dispatch_only` red. Both restored and re-verified green (32 passed), and the restored YAML re-parsed to confirm `triggers == {'workflow_dispatch': {}}` and `timeout == 75` — the surviving `pull_request` string in the file is inside the explanatory comment only. This closes the vacuous-guard risk that the `on:`-truthiness trap creates: a test that could not go red would have looked identical in a green run.

Residual honestly stated: no test in this task can prove 75 is *sufficient* — only that the manifest declares at least 75. That is the frozen [contract] least-sure flag, and it resolves on the first real run, not here.

### GATE RECORD
Reported: no
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-08-07

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned, with one grounding fact that changed how the test had to be written. `yaml.safe_load` parses a workflow's `on:` key as the **boolean `True`** (YAML 1.1 truthiness), not the string `"on"` — verified in-context before writing the test, and captured in the `_triggers()` helper, which reads the boolean key and accepts `"on"` only as a forward-compatible fallback. A guard written the obvious way (`workflow["on"]`) would have raised `KeyError`, read as red-for-the-right-reason, and been one lazy "fix" away from vacuous. Everything else is the frozen contract verbatim: two constants, two tests, two manifest edits, no existing test touched.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
