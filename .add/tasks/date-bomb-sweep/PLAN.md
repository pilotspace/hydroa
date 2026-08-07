# PLAN: Sweep the absolute-seed/relative-window date bombs before 1 September

slug: date-bomb-sweep · created: 2026-08-07 · stage: production
milestone: release-integrity
autonomy: auto   <!-- manual<conservative<auto — lower for high-risk (`add.py autonomy set`); a `component: <name>` line joins that root to §3 Scope; task edges: `--depends-on`/`--extends`/`--relates-to`; high-risk/method-defining? declare `risk: high` on the slug line; headless agent-crossed freeze? declare `gate_mode: ai-plan-verify` here (human floor: security|data|architecture never AI-frozen) -->
phase: build   <!-- direction→build→verify→done; direction drafts §1–§4 (rules · change plan · red suite) to the ONE freeze -->
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A test that seeds an ABSOLUTE date and then queries a WALL-CLOCK-RELATIVE window
is caught by a guard, not by `make ci` going red one morning with nobody having touched
the tree.

**The audit came back empty — and the guard proved the audit WRONG (CR-2, 2026-08-07).**
Todo #84 said "~20 test files carry hardcoded 2026-07/08 dates and have NOT been audited".
My hand audit found 77 files carrying such a literal, four suites issuing a bare relative
window, and judged all four clean. On its first run over the real tree the guard found
THREE live bombs my audit had missed, all in `tests/margin_dashboard` — the same suite PR
#92 fixed, which fixed six of the nine and left three behind.

That is the strongest possible argument for the guard, and it is also a correction to this
task's own premise: the deliverable is the guard AND the three fixes, not the guard alone.
A hand audit by the same agent that wrote the framing is exactly the thing a mechanical
check exists to distrust.

Framings weighed:
- **Build the guard; record the audit** (chosen). The one live instance was found the
  expensive way — `make ci` went red on 2026-08-01 with ZERO commits behind it — and fixed
  in PR #92. A guard is what stops the SECOND one, and it is the only durable artifact
  available now that the sweep has nothing to fix.
- Edit the 77 files carrying a 2026 literal — rejected, and this is the important
  rejection: most are CORRECT. `INSIDE` in `tests/margin_dashboard/conftest.py` is right
  for absolute-window requests (`start=`/`end=`/`period=2026-07`) and is deliberately kept
  alongside `INSIDE_CURRENT_MONTH`. A blanket "no hardcoded dates in tests" sweep would
  break the very tests PR #92 just fixed. Getting this wrong in EITHER direction re-breaks
  it, which is why the guard keys on the PAIRING, never on the literal alone.
- A time-travel run (`libfaketime`/freezegun at +40 days) — genuinely the most thorough
  detector, and rejected on a concrete flaw: it moves the PYTHON clock while Postgres
  `now()` and `func.now()` stay real. This repo has `server_default=func.now()` columns and
  a class of tests that compare the two, so a split clock manufactures failures that are
  not date bombs. Recorded as a §7 option if the guard ever proves too narrow.
- Do nothing — rejected: 1 September is the next trigger, and the failure mode is a red CI
  that looks like a code regression. Exactly the confusion suite-infra-tripwire just
  removed for infrastructure.

Must:
<must>
  - M1 a test module that BOTH seeds from an absolute datetime constant AND issues a
    request with a bare relative window (`window=` with no `start=`/`end=`) FAILS the guard,
    naming the file and both halves of the pairing
  - M2 a module that uses an absolute constant with an ABSOLUTE window (`start=`/`end=`/
    `period=`) PASSES — that is `tests/margin_dashboard`'s `INSIDE`, which is correct and
    must not be flagged
  - M3 a module that issues a bare relative window but seeds from the wall clock PASSES —
    that is `tests/spend_windows`, `tests/team_governance`, `tests/team_attribution`
  - M4 the guard runs inside the normal suite (no separate target to forget) and names the
    remedy: keep both constants, seed relative-window tests inside the current month
  - M5 (CR-2) the three live bombs the guard found are FIXED with PR #92's own remedy
    (`INSIDE_CURRENT_MONTH`), so the whole-tree check is green on merit and not by
    exception. No assertion is changed — only the `created_at=` seed argument.
</must>
Reject:
<reject>
  - a bare "no hardcoded date literal in tests" rule -> REFUSED. It flags ~77 correct files
    and would revert PR #92's own fix. The guard keys on the PAIRING or it is worthless.
  - an allowlist/skip entry for any file the guard flags -> REFUSED. Every finding is
    either a real bomb (fix it) or a detector defect (fix the detector). A suppression
    list turns a guard into a record of what we decided to stop looking at.
</reject>
After:
<after>
  - the 2026-09-01 rollover passes without a mystery red
  - todo #84 closed, its "~20 unaudited files" claim replaced by a recorded result
</after>
Boundary: none — the guard reads test SOURCE, not runtime values. The input shape is a
Python module's text.
<assumptions>
  ⚠ That source-level detection is precise enough. A test could build an absolute seed
  indirectly (a helper, a fixture in a sibling module, a `timedelta` off a literal) and
  slip past. That is a FALSE NEGATIVE — the guard is a net over a hazard with no net at
  all today, so a miss leaves us exactly where we are. The direction that would hurt is a
  false POSITIVE flagging a correct absolute-window test, which is why M2 and M3 are gated
  against the real files by name.
</assumptions>

<!-- §2 (the old standalone SCENARIOS section) was RETIRED — pass/fail cases now live with the tests in §4 · TESTS & SCENARIOS. The §3–§7 numbers are unchanged so the freeze parser and every §-reference keep working; the jump from §1 to §3 is intentional. -->

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding — THE AUDIT (run 2026-08-07; this is the evidence, not a plan to gather it):
- `src/gateway/usage/api/router.py::_compute_window_bounds` is the ONE wall-clock window
  resolver in the codebase. Confirmed by search: every other consumer IMPORTS it rather
  than duplicating it — `usage/api/margin_router.py`, `guardrail_analytics/api/router.py`
  (whose own docstring says "reuses ... verbatim (imported, not duplicated)"), plus three
  further call sites inside `usage/api/router.py`. So the hazard surface is exactly "tests
  that call an endpoint reaching `_compute_window_bounds` with a bare `window=`".
- Files carrying a hardcoded 2026-07/08 literal: **77** (todo #84 guessed ~20).
- Files issuing a bare relative window (`window=month|week|day`): **4**.
  * `tests/margin_dashboard/conftest.py` — the KNOWN one, already fixed in PR #92 via
    `INSIDE_CURRENT_MONTH`, with `INSIDE` deliberately kept for absolute-window tests.
  * `tests/spend_windows/test_spend_windows.py` — 24 date literals, and CLEAN: it seeds
    from `datetime.datetime.now(datetime.UTC)` at every site (~246, 320, 394, 463, 623,
    732). Its 2026 literals are docstrings and a `%Y%m` helper.
  * `tests/team_governance/test_team_governance.py` — CLEAN: one `window=month` query
    (~801), seeds via `now(datetime.UTC)` (~152/157). No absolute seed.
  * `tests/team_attribution/test_team_attribution.py` — CLEAN: three `window=month`
    queries (~537, 629, 739), no `datetime(2026, ...)` seed anywhere.
- `tests/margin_dashboard/test_verify_adversarial.py` imports the absolute `INSIDE` at 10+
  sites — checked and CORRECT: it pairs with the `WINDOW_FROM`/`WINDOW_TO` absolute window,
  and the tests near those seeds are auth/timeout cases, not window cases.
- **Result claimed at v1: zero remaining live date bombs of this class.** FALSIFIED by the
  guard on its first run (CR-2, approved by Tin 2026-08-07). Three live bombs remain, all
  in `tests/margin_dashboard`, all missed by both PR #92 and my hand audit:

  | test | pairing | consequence today |
  | --- | --- | --- |
  | `test_verify_adversarial.py::test_verify_tenant_id_filter_isolates_by_tenant_model` | `created_at=INSIDE` + `params={"window":"month"}` | **VACUOUS.** Probed 2026-08-07: the query returns **0 items** and the test passes. It asserts `str(tid_b) not in tenant_ids_returned` and `all(i["tenant_id"] == str(tid_a) ...)` — both trivially true over an empty set. This tenant-isolation test would pass with the `tenant_id` filter deleted, and has since 2026-08-01. |
  | `test_margin_dashboard.py::test_m1_summary_via_ops_router_matches_margin_summary` | same | Degraded, not vacuous — it asserts a 200 and a monkeypatched call trace, neither of which depends on the seeded row. The seed is now dead weight that reads as coverage. |
  | `test_margin_dashboard.py::test_m8_query_timeout_maps_to_504` | same | Benign — a DB fault is injected before the query runs, so the row never mattered. Still a true pairing and still worth correcting. |

  NOT a product vulnerability, and I have no evidence the `tenant_id` filter is broken —
  the defect is that nothing has been VERIFYING it. Fixing the seed is what re-verifies it,
  which is why M5 is part of this task rather than a follow-up.

```
Detector: apps/gateway/tests/repo_hygiene/_date_bomb.py   (pure, so it is unit-testable;
                                                           the guard test is a thin caller)

  def scan_source(text: str, *, imported_names: set[str] = frozenset()) -> str | None
        None    -> clean
        reason  -> a human-readable line naming BOTH halves of the pairing

  def scan_tree(root: Path, *, skip_fixtures: bool = True) -> list[tuple[Path, str]]
        Walks *.py under root, resolving each directory's absolute-seed constants first so
        a sibling import is followed one level. By default SKIPS any path containing a
        `fixtures/` segment — the planted bomb modules that prove the detector fires live
        there, and a guard that flagged its own test fixtures would be permanently red.
        `skip_fixtures=False` is how the §4 suite aims it AT those fixtures.

Guard: apps/gateway/tests/repo_hygiene/  (joins the existing repo-hygiene guard family,
                                          e.g. test_timestamp_columns_have_one_clock_owner)

  test_no_absolute_seed_with_relative_window()

  For each test module under apps/gateway/tests/:
    absolute_seed  := a datetime literal with an explicit year — datetime.datetime(YYYY,…)
                      — bound to a module-level constant OR passed as created_at=/period_start=
    relative_query := a request path containing `window=<month|week|day>` and NOT
                      containing `start=` or `end=`
    FAIL iff  absolute_seed AND relative_query  in the SAME FUNCTION BODY
              (module-level statements count as one implicit scope).

  CR-1 (2026-08-07, approved by Tin) — this said "in the SAME module" at v1. That rule is
  WRONG and the frozen §4 falsified it: `tests/margin_dashboard/test_margin_dashboard.py`
  holds correct absolute-window tests AND correct relative-window tests hundreds of lines
  apart, so a module-scoped rule flags the very file PR #92 already fixed. Two unrelated
  tests sharing a file are not a date bomb; ONE test that seeds absolutely and queries
  relatively is. Verified in both directions against real history:
    * at `8074d8d^` (pre-PR #92) the function rule reports 9 findings and includes ALL SIX
      functions the PR #92 commit message names — no misses.
    * on the fixed tree it is silent on every test PR #92 corrected.
  Detection is AST-based, not regex-over-text: an absolute seed is a `datetime(YYYY, ...)`
  CALL node and a sibling seed is an `ast.Name` load. Docstrings are stripped first. Prose
  DESCRIBING the hazard — this guard's own §4 messages, margin_dashboard's conftest — is
  not the hazard, and a text-level rule cannot tell the two apart.

  `scan_all(text, *, imported_names) -> list[str]` reports EVERY pairing in a module;
  `scan_source` returns the first. A file with three bombs must not look like a file with
  one — the third of the three below was hidden by first-match-wins until this was added.

  The failure names the file, the seed line, the query line, and the remedy: keep BOTH
  constants — absolute for start=/end=/period=, current-month for a bare window=.
  Imports are followed one level within the same directory (a module importing INSIDE
  from a sibling module — its package conftest, typically — counts as carrying the seed).
  That is exactly the margin_dashboard shape, and only a directory walk can see it, which
  is why `scan_source` also accepts the resolved names directly.
```

Target (measurable): the guard is RED before build against a deliberately-planted fixture
reproducing the PR #92 shape, and GREEN after, with all four real bare-relative-window
suites and `tests/margin_dashboard` passing UNFLAGGED. `make ci` stays green at 4531 passed.
Status: FROZEN @ v2 — approved by Tin Dang (CR-1 locality, CR-2 scope+M5; v1 2026-08-07)
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Strategy: write the guard against a PLANTED fixture first (a tiny module reproducing the
PR #92 pairing) so it is proven to fire, then run it over the real tree and confirm it
stays silent on all 77 literal-carrying files. A guard that has never been seen to fail is
not a guard.

Scope (may touch): `apps/gateway/tests/repo_hygiene/` · `apps/gateway/tests/date_bomb_sweep/` · `apps/gateway/tests/margin_dashboard/`
<!-- CR-2 (2026-08-07, approved by Tin): margin_dashboard/ added so the three bombs the
     guard found can be FIXED here. Without it the frozen §4 whole-tree test stays red and
     this task cannot gate on merit. Only `created_at=` seed arguments change there — no
     assertion is touched, exactly PR #92's own discipline. -->
Regression floor: full `make ci` — the guard walks every test module, so a false positive
anywhere in the tree is a red suite.
Persona (optional): `sre-reliability-engineer` — "the second occurrence is the one you get
to prevent."

DECIDED by Tin, 2026-08-07 interview: BUILD the guard, despite the audit finding nothing
to fix. Rationale accepted: the trigger is calendrical, new tests are written continuously,
and the pairing rule is narrow enough to be cheap. The audit result is recorded as evidence
in §3 rather than as work.

Least-sure flag surfaced at freeze: [spec] — that the guard is worth having at all, now
that the audit found nothing to fix. The honest case against: it guards a hazard with one
recorded instance and adds a source-scanning test to every run. The case for, which I
believe: that one instance cost a red `make ci` that looked like a code regression, the
trigger is calendrical so it WILL recur as new tests are written, and the pairing rule is
narrow enough to be cheap. If you would rather close #84 on the audit alone and skip the
guard, that is defensible and this is the moment to say so.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

<!-- The freeze IS the one approval, led by the bundle's lowest-confidence flag — Contract + Scope (may touch) = HARD (tamper-guarded); Strategy · Regression floor · Persona = SOFT/optional. Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen Contract = change request back to SPECIFY. Scope tokens, backticked: `./…` = this task dir · a "/" token = project root · a bare name = sibling of the previous token's dir · a directory covers its whole subtree · outside-root drops fail-closed · absent line = UNDECLARED (grandfathered, never retro-red). -->

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  THE GUARD FIRES (proven against a PLANTED fixture — a guard never seen to fail is not a
  guard, and the real tree is clean so nothing else can prove it)
  - test_flags_a_planted_absolute_seed_with_bare_relative_window: a fixture module
    reproducing the exact PR #92 shape — a module-level datetime.datetime(2026, 7, 15,…)
    used as created_at= plus a request to `...?window=month` with no start=/end=. Assert
    the guard reports it AND that the message names the file, both line numbers, and the
    remedy · covers: M1, M4
  - test_flags_the_seed_when_it_arrives_via_a_package_conftest_import: the fixture imports
    its constant from a sibling conftest rather than defining it. This is EXACTLY the
    margin_dashboard shape, so a guard that only looks at module-local definitions would
    have missed the one real instance we have ever had · covers: M1

  THE GUARD STAYS SILENT (the false-positive direction — the one that would hurt)
  - test_absolute_seed_with_absolute_window_is_not_flagged: a fixture pairing an absolute
    constant with start=/end=. This is tests/margin_dashboard's INSIDE, which is CORRECT ·
    covers: M2
  - test_period_query_is_treated_as_absolute: `period=2026-07` pins the window as firmly as
    start=/end= does; flagging it would push someone to "fix" a correct test · covers: M2
  - test_relative_window_seeded_from_the_wall_clock_is_not_flagged: seeds from
    now(datetime.UTC) · covers: M3

  THE REAL TREE (named files, not a blanket assertion — these are the audit's findings
  turned into a standing check)
  - test_the_four_real_relative_window_suites_pass_unflagged: margin_dashboard,
    spend_windows, team_governance, team_attribution — each asserted individually so a
    future regression names WHICH one broke · covers: M2, M3
  - test_the_whole_test_tree_is_currently_clean: run the guard over apps/gateway/tests and
    assert zero findings. Green only AFTER M5 fixes the three bombs the guard found — at
    v1 this was expected to pass immediately, which is exactly the assumption CR-2
    corrected · covers: M1, M5, R

  ADDED AT v2 (CR-1) — the detector defects the first real run exposed
  - test_reports_every_pairing_in_a_file_not_just_the_first: a fixture with THREE bombed
    functions. `scan_all` must return three reasons. First-match-wins hid the third real
    margin_dashboard bomb until this was found by hand, so a file with three bombs must
    never read as a file with one · covers: M1
  - test_prose_describing_the_hazard_is_not_the_hazard: a module whose only `datetime(2026,
    …)` and `window=month` occurrences are inside a DOCSTRING and an assertion MESSAGE.
    Must stay silent. The guard's own §4 suite tripped this, and a guard that flags the
    test proving it works is self-defeating · covers: M2
</test_plan>

RED evidence — run 2026-08-07, `pytest tests/date_bomb_sweep -q`:
```
10 failed in 0.23s
ModuleNotFoundError: No module named 'tests.repo_hygiene._date_bomb'   (all 10)
```
Every red is the SAME red, and it is the right one: the detector does not exist. There is
no partial-credit arm here — the guard is one module, so nothing can pass until it lands.

Planted fixtures (`./fixtures/`, text specimens — never imported, never executed, and
`scan_tree` skips `fixtures/` by default so the guard cannot flag its own evidence):
  bomb_inline.py                — absolute seed + bare `window=month`   (must FIRE)
  imported_seed/{seeds,mod_bomb}.py — seed arrives by import            (must FIRE, on
                                     mod_bomb only — seeds.py issues no query)
  clean_absolute_window.py      — absolute seed + start=/end=           (must stay SILENT)
  clean_period_window.py        — absolute seed + period=2026-07        (must stay SILENT)
  clean_relative_seed.py        — now(UTC) seed + window=month          (must stay SILENT)

ruff format + check: clean. pyright: 4 errors, all `Import "tests.repo_hygiene._date_bomb"
could not be resolved` — the same missing module, and they clear when it lands.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/date_bomb_sweep/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0. The test_plan bullets' `covers:` tails are machine-read too: `add.py locate path::test_name` resolves a failing test to the frozen §3 clause it proves -->
<!-- NON-CODING task (kind: docs · release · infra, or a non-coding project)? §4 is a failing-first ACCEPTANCE CHECK, not a script — verifiable pass/fail evidence (mkdocs build succeeds · §X covers A/B/C · every internal link resolves), red before the artifact exists and green after. Set `Tests live in: evidence` (no `./tests/`). The red→green discipline holds; only the must-be-executable-code requirement is lifted. -->

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned — guard first, against planted fixtures, then over the
real tree — and the "then over the real tree" step is what earned the two change requests.
Building it in that order is the only reason CR-2 was found before merge rather than on
1 September.

GREEN evidence — 2026-08-07:
```
tests/date_bomb_sweep tests/margin_dashboard tests/repo_hygiene
tests/spend_windows tests/team_governance tests/team_attribution
  -> 126 passed in 40.35s
ruff format + check: All checks passed!
pyright: 0 errors in date_bomb_sweep / _date_bomb.py (CI scope is src/gateway only)
```

Two-directional proof that the detector is calibrated, not merely quiet:
- FIRES: at `8074d8d^` (pre-PR #92) it reports **9** findings and includes **all six**
  functions PR #92's own commit message names. Zero misses against the one real historical
  bomb this repo has.
- SILENT: on the fixed tree it reports **0**, with `tests/margin_dashboard`,
  `tests/spend_windows`, `tests/team_governance` and `tests/team_attribution` each asserted
  by name.

M5 fix — three files, five lines, `created_at=` seed arguments ONLY; no assertion added,
changed, or relaxed (PR #92's own discipline). Verified by probe, not by inference:
```
before  /admin/platform/margin/by-tenant-model?window=month&tenant_id=... -> 0 items (test passed)
after   same request                                                      -> 1 item   (test passed)
```
So `test_verify_tenant_id_filter_isolates_by_tenant_model` now actually exercises the
filter — tenant A returned, tenant B excluded — where before it asserted over an empty set.
The filter itself was never broken; nothing had been checking it since 2026-08-01.

Detector defects found and fixed during build, both by running against real code:
1. module-scope pairing -> function-scope (CR-1).
2. regex-over-text seed detection flagged `datetime(2026, 7, 15, ...)` inside this suite's
   own assertion MESSAGES -> AST `Call`/`Name` detection, docstrings stripped first.
3. first-match-wins per file hid the third real bomb -> `scan_all` reports every pairing.
Each of 2 and 3 is now gated by a §4 test added at v2, so neither can silently return.
Code lives in: `src/`
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

`make ci` GREEN on the full tree — 2026-08-07, exit 0:
```
4559 passed, 7 skipped, 28 deselected, 1 xfailed, 0 failed   37:00
coverage 91.11% (main was 90.94%)   infra-guard trips: 0
```
main carried 4531; +28 = the three suites landed in this run (10 + 12 + 6).

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: self · adversarially checked:
- **Is the guard green because it finds nothing?** That is the failure mode a clean tree
  invites, and it is refuted two-directionally against real history, not against fixtures
  alone: at `8074d8d^` it reports 9 findings including ALL SIX functions PR #92's own commit
  message names, and 0 on the fixed tree. A guard that has never been seen to fire is not a
  guard.
- **Was the frozen §4 weakened to fit the build?** No — the opposite. §4 stayed intact and
  falsified the frozen §3 twice (CR-1 locality, CR-2 the audit's "zero bombs" claim). Both
  went back through SPECIFY as change requests and re-froze at v2. Two NEW tests were added
  at v2 for the detector defects found; nothing was removed or relaxed.
- **Is the M5 fix a test weakening?** Five `created_at=` seed arguments moved to
  `INSIDE_CURRENT_MONTH`. No assertion added, changed, or relaxed — PR #92's own discipline,
  verified by reading the whole diff (5 changed lines across 2 files).
- **Did the isolation fix actually restore coverage, or just silence the guard?** Probed the
  live request, not inferred: 0 items before (test passed), 1 item after (test passed). The
  test now genuinely exercises the tenant_id filter.
- **Could the guard flag correct code?** The false-positive direction is the one that would
  hurt — it would push someone to revert PR #92. Gated by name against all four real
  bare-relative-window suites plus fixtures for absolute-window, `period=`, wall-clock-seed,
  and prose-only shapes. An existing repo-hygiene guard also caught my own unjustified
  file-level `noqa` during this work; fixed, not suppressed.
- **Security:** NOT a product security finding — the `tenant_id` filter was never broken.
  The finding is that a tenant-isolation TEST had been verifying nothing since 2026-08-01,
  i.e. a coverage gap in the evidence, now closed. Flagged here because a vacuous isolation
  assertion is invisible in a green suite and is the kind of thing that should never be
  discovered twice.
- **Known limit, stated:** a seed built indirectly (a helper, a `timedelta` off a literal, a
  fixture two packages away) still slips past. FALSE NEGATIVE, accepted at freeze.

### GATE RECORD
Reported: yes
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-08-07

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Refute-read verdict is recorded, never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
