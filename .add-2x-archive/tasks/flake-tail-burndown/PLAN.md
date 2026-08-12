# PLAN: Kill the test-suite flake tail: 4 CI-red sites, a drop_all-after-lifespan guard, and the 89 unclassified fixed sleeps

slug: flake-tail-burndown · created: 2026-08-10 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: the gateway suite stops losing runs to timing races — every fixed-sleep-then-assert
site is explicitly bucketed and either converted to a bounded poll or documented as a
deliberate negative wait, and both known race mechanisms become unreintroducible by a guard.

Framings weighed:
  - **classify-then-convert per site, plus a guard per mechanism** (chosen) — the only framing
    that cannot silently weaken a test. Each of the 252 sites carries an explicit verdict;
    conversion touches ONLY the positive waits; the two mechanisms that produced today's CI
    red get a repo_hygiene guard so a third suite cannot reintroduce them by copy-paste (which
    is exactly how `preset_capability_validation` acquired the `drop_all` deadlock from
    `tests/realtime`).
  - blanket mechanical conversion of all 252 sites (rejected) — would convert the 38 NEGATIVE
    sites, where the sleep IS the assertion. `poll_until` returns the instant the first row
    exists and never gives the unwanted second write a chance to appear, turning a real
    assertion vacuous. That is test-weakening: non-negotiable, and `_polling.py`'s own
    docstring already forbids it.
  - `pytest-rerunfailures` across the whole suite (rejected) — the dependency is already
    present, so this is the cheap path, but it makes the suite green by RETRYING the race
    instead of removing it, and release-integrity exit #6 says green "with no manual retry".
    A retried flake is an unattested run.

Must:
<must>
  - M1 every site classified POSITIVE (waits for state to APPEAR) waits via
    `tests._polling.poll_until`/`poll_for_count` under a bounded timeout, never a bare
    `asyncio.sleep` followed by the assertion it is waiting for.
  - M2 every site classified NEGATIVE (the sleep proves something NEVER happens) KEEPS its
    fixed sleep and carries a written one-line reason at the site, so a later sweep cannot
    mistake it for an unconverted positive wait.
  - M3 every site classified STRUCTURAL (a fake/stub simulating latency, a TTL/expiry advance,
    a `sleep(0)` loop yield) is left byte-identical — it is not a race.
  - M4 zero sites remain UNCLASSIFIED: the 252-site census is exhaustive and each site's
    verdict is recorded.
  - M5 `tests/preset_capability_validation` performs its `Base.metadata.drop_all`/`create_all`
    BEFORE the app lifespan starts, matching the fix and the written rationale already present
    in `tests/realtime/test_realtime_ws.py`.
  - M6 a repo_hygiene guard fails when any test runs `drop_all`/`create_all` after a
    `TestClient.__enter__()`/`with TestClient(...)` in the same function.
  - M7 a repo_hygiene guard fails when a NEW bare `asyncio.sleep(...)` is immediately followed
    by a DB fetch + assertion (the positive-wait shape), grandfathering the documented
    NEGATIVE sites via their M2 reason.
  - M8 the four tests that failed CI run 31356301036 pass under `-n 6` on a loaded host.
</must>
Reject:
<reject>
  - a test that drops/creates schema after the lifespan has started -> "ERR_DDL_AFTER_LIFESPAN"
  - a new bare fixed-sleep-then-assert positive wait -> "ERR_UNBOUNDED_WAIT"
  - a NEGATIVE site converted to a poll (its assertion becomes vacuous) -> "ERR_VACUOUS_WAIT"
</reject>
After:
<after>
  - the full gateway suite completes green three consecutive times with no manual retry and no
    chunking workaround — release-integrity exit criterion #6 is satisfiable on this evidence.
  - the census file records a verdict for all 252 sites, so the next flake is diagnosed against
    a known population rather than re-surveyed from scratch.
</after>
Boundary: none — no external input. The variance this task speaks to is HOST LOAD, and the
tests must hold at both extremes: an idle laptop (where every current site already passes) and
a saturated 4-vCPU runner sharing its cores with Postgres + Redis (where they do not).
<assumptions>
  ⚠ that the classifier's NEGATIVE-outranks-POSITIVE rule is sufficient to protect the 38
    negative sites from conversion. It is a HEURISTIC over an 8-line-before/12-line-after
    window, not proof — a negative assertion further than 12 lines from its sleep reads as
    POSITIVE and would be converted into a vacuous test. If wrong: a test keeps passing while
    asserting nothing, which is strictly worse than the flake it replaced, and the suite's
    green stops meaning anything. Mitigation: every POSITIVE conversion is reviewed against its
    full enclosing function, not the window, and M7's guard is written to FAIL on an
    unreasoned conversion rather than trust the bucket.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

GROUND (real symbols, verified in-context 2026-08-10):
  `tests/_polling.py::poll_until` / `poll_for_count`  — the existing bounded-poll primitive
      (suite-stability M3). Its docstring already carries the POSITIVE-only rule this task
      enforces mechanically. REUSED UNCHANGED — this task adds no new waiting primitive.
  `tests/realtime/test_realtime_ws.py` (module docstring + its TestClient bootstrap) — the
      PRECEDENT: DDL before `__enter__`, with the deadlock rationale written out. M5 propagates
      this, it does not invent it.
  `tests/preset_capability_validation/test_preset_capability_validation.py::_bootstrap_and_signup`
      — calls `drop_all`/`create_all` through `tc.portal.call`, invoked AFTER `tc.__enter__()`
      in both `test_realtime_ws_chat_turn_rejects_incompatible_model` and
      `test_realtime_ws_stt_turn_rejects_incompatible_model`. This is M5's single fix site.
  `tests/guardrails/test_guardrails_core.py::test_prompt_injection_block_mode_rejects_payload`
      — `asyncio.sleep(0.15)` then `SELECT ... FROM usage_records`; CI got `0 >= 1`.
  `tests/batches/test_batch_window_grouping.py::TestWindowFlushesAsOneJob::test_window_flushes_as_one_multi_item_job`
      — `asyncio.sleep(0.5)` for 3 concurrent appends, then ONE `flush_once()`; CI got
      `{'claimed': 0, 'items': 0}`. The poll must re-invoke `flush_once()`, not just re-fetch:
      the flusher's due-check is elapsed-time-based.
  `tests/repo_hygiene/` — the existing home for AST/source-level guards (precedent:
      `test_timestamp_columns_have_one_clock_owner`, suite-stability CR v3). M6/M7 land here.

CENSUS (the frozen population — `scratchpad/classify_sleeps.py`, 2026-08-10):
  252 `asyncio.sleep(` sites across 115 files under `apps/gateway/tests/`
    POSITIVE    76   -> convert to a bounded poll                     (M1)
    NEGATIVE    38   -> keep + written reason                         (M2)
    STRUCTURAL  46   -> leave byte-identical                          (M3)
    UNKNOWN     92   -> per-site human-grade judgement, then bucketed (M4)
  Independently corroborates todo #79's hand census (83/29/89) — the deltas are boundary calls
  the per-site pass resolves, not a disagreement about the population.

GUARD CONTRACT (M6/M7 — the shape that gets frozen):
```
tests/repo_hygiene/test_no_ddl_after_lifespan.py
  scans every tests/**/*.py function body (ast)
  FAILS  -> names file::function where a drop_all/create_all call node follows a
            TestClient __enter__ / `with TestClient(...)` node        [ERR_DDL_AFTER_LIFESPAN]
  PASSES -> zero such functions

tests/repo_hygiene/test_no_unbounded_positive_wait.py
  scans every tests/**/*.py for `asyncio.sleep(<literal > 0>)` whose following statements
    (same block, before the next await of a request) contain BOTH a fetch and an assert
  FAILS  -> names the site unless it carries an inline `# NEGATIVE WAIT:` reason  [ERR_UNBOUNDED_WAIT]
  PASSES -> every remaining fixed-sleep site is either reasoned or not a positive wait
  ALLOW-LIST: the 38 M2 sites, each by its written reason — NOT by a path list, so a
              copy-paste into a new file is caught while the reasoned original is not.
```

CENSUS v2 (post-CR, the actionable population):
  89 sites matched the v1 guard -> 54 real-duration (ACTIONABLE) + 35 `sleep(0)` LOOP-YIELD
  The 54 are enumerated in `./sleep-worklist.txt`; the classifier is `./classify_sleeps.py`
  (both persisted in the task dir, NOT a scratchpad — todo #79 records that the previous
  classifier was lost exactly that way).

Target (measurable):
  - all 252 census sites carry a verdict; UNKNOWN == 0
  - the 4 tests from CI run 31356301036 pass 3/3 under `-n 6` with the host loaded
  - `make ci` exits 0 three consecutive times, wall-clock recorded per run
  - 0 NEGATIVE sites converted — verified by reading every conversion's enclosing function and
    by M7's guard refusing an unreasoned site
  - `git diff --stat` touches ZERO files under `apps/gateway/src/` (this is a test-only change;
    any production edit means the diagnosis was wrong and the task re-enters direction)
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/tests/` `./`
  <HARD. Deliberately EXCLUDES `apps/gateway/src/` — the diagnosis is that all four CI failures
  are test-harness races, not product defects, so a production edit is the signal that the
  diagnosis was wrong. It also excludes `.github/workflows/` (CI shape is ci-restoration's
  frozen contract) and `Makefile`.>

Waves (the build sequences by proven-red first, so value lands before the long tail):
  W1 — the 4 CI-red sites + M5's DDL move. Highest-confidence, evidence already in hand.
  W2 — M6/M7 guards. Written to be RED against the tree as it stands, so W1's fix is what
       turns M6 green; this ordering is what proves the guard actually guards.
  W3 — the 76 POSITIVE conversions.
  W4 — the 92 UNKNOWN, per-site: read the enclosing function, assign a bucket, then treat as
       W3 (convert) or M2 (reason it). This wave is where the ⚠ assumption bites; it is
       sequenced last so a stall here still leaves W1–W3 shippable.
  W5 — 3 consecutive `make ci` runs. Serial, one pytest session at a time
       (`[[one-pytest-session-at-a-time]]`), unique `GATEWAY_TEST_DATABASE_URL`.

Regression floor: the full gateway suite (~4559 tests). This task's entire product IS the
  regression floor's determinism, so "green" means green three times, not once.
Persona: `.add/personas/sre-reliability-engineer.md` — its standing rule ("a locally-green suite
  is evidence of correctness, never evidence of change management") is the reason W5 exists, and
  its blameless-systemic rule is why M6/M7 are guards rather than four point fixes.

Least-sure flag surfaced at freeze: [test] the 92 UNKNOWN sites' per-site judgement (W4). The
  classifier protects the 38 known NEGATIVE sites, but an UNKNOWN site whose negative assertion
  sits outside the 12-line window will read as POSITIVE, and converting it yields a test that
  passes while asserting nothing — strictly worse than the flake. Every W4 conversion therefore
  requires reading the whole enclosing function, and any site whose intent is still ambiguous
  after that read stays a fixed sleep with an M2 reason. Choosing "keep the sleep" on a genuine
  positive wait costs one more flaky run; choosing "convert" on a negative wait costs a silently
  dead assertion. The asymmetry decides every coin-flip.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_no_test_runs_ddl_after_lifespan_entry: walk every `tests/**/*.py` function with ast;
    flag any whose body reaches a `drop_all`/`create_all` call after a `TestClient.__enter__()`
    or `with TestClient(...)`. Assert the flagged set is empty, and FAIL naming
    `file::function`. Runs RED today: `preset_capability_validation` matches twice.
    · covers: M6, R:ERR_DDL_AFTER_LIFESPAN
  - test_fixed_sleep_positive_waits_are_bounded_polls: flag every `asyncio.sleep(<literal>)`
    whose following statements in the same block contain BOTH a fetch and an assert, unless the
    site carries an inline `# NEGATIVE WAIT: <reason>`. Assert the flagged set is empty.
    Runs RED today against the unconverted census.
    · covers: M1, M7, R:ERR_UNBOUNDED_WAIT
  - test_negative_wait_sites_state_a_reason: every site the census buckets NEGATIVE carries a
    `# NEGATIVE WAIT:` line whose reason is non-empty. Guards against the M2 sites decaying
    into indistinguishable bare sleeps, which is what made this census necessary at all.
    · covers: M2, R:ERR_VACUOUS_WAIT
  - test_sleep_census_is_exhaustive: every `asyncio.sleep(` site in `tests/` resolves to exactly
    one of POSITIVE (now a poll) / NEGATIVE (reasoned) / STRUCTURAL (allow-listed by shape).
    Asserts the UNKNOWN bucket is empty — the machine-checkable form of M4.
    · covers: M4
  - [CR v3] test_suite_reading_a_singleton_row_clears_it: every table with a singleton primary
    key (`CheckConstraint("id IS TRUE")` — today exactly `routing_config`) is cross-test GLOBAL
    state. Any suite that reads it while building its own app (create_app in a suite conftest,
    bypassing the root `app` fixture's per-test DELETE) must clear it. Assert the flagged set is
    empty. Runs RED against the tree before `691cace`.
    · covers: CR v3 class 4
  - [CR v3] test_test_built_provider_injects_an_egress_policy: a provider constructed directly
    in `tests/` must pass `egress_policy=`, or it builds the production
    DenyPrivateAndMetadataEgressPolicy and performs a LIVE DNS LOOKUP — in suites that document
    themselves as needing no network. Assert the flagged set is empty. Runs RED against the
    tree before `691cace` (azure_audio).
    · covers: CR v3 class 5
  - [CR v3] test_fire_and_forget_assertion_has_a_wait: a test function that triggers a
    fire-and-forget write via an HTTP call and then asserts on the written table must contain
    SOME wait (a poll, a settle helper, or a declared sleep). A site with no wait at all never
    entered the sleep census. Assert the flagged set is empty. Runs RED against the tree before
    `2108c59`; validated by re-running the scan against `git show HEAD:` of the pre-fix file.
    · covers: CR v3 class 7
  - [CR v3] test_quiescence_settle_requires_an_expected_count: a "wait until the length stops
    changing" settle must take an expected count, because a stream at 0 is trivially stable and
    cannot be told apart from one that has not started. Assert no stability-only settle exists.
    Runs RED against the tree before `7057c59`.
    · covers: CR v3 class 9
  - ACCEPTANCE (not a unit red — the four sites' failure is load-dependent, and a flake is not a
    trustworthy red): the four tests named in §3 GROUND pass 3/3 at `-n 6` with the host under
    load, and `make ci` exits 0 three consecutive times. Evidence = CI run 31356301036 as the
    recorded RED, three run logs with wall-clock as the GREEN.
    · covers: M5, M8
</test_plan>

Why the guards are written BEFORE the conversions (W2 before W3/W4): a guard authored after its
violations are gone proves only that it compiles. Authored first, `test_no_test_runs_ddl_after_
lifespan_entry` must name `preset_capability_validation`'s two functions — and if it does not,
the guard is wrong and would have let the next copy-paste through. Same for the wait guard
against the 76-site backlog. This is the one place where red-first is load-bearing rather than
ceremony, because both guards are source-scanners whose failure mode is silent under-matching.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/repo_hygiene/` · MUST run red before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: waves ran W1 → W2 → (W3+W4 merged) → W5, with two deviations.

  W1/W2 REORDERED, deliberately. The plan wrote the guards (W2) after the four CI-red
  fixes (W1), so M6's guard was authored against a tree where its violation still stood —
  which is what the §4 note demands. But `test_no_ddl_after_lifespan` did NOT flag
  `preset_capability_validation` on the first draft: the `drop_all` sat three frames down
  (test → `_bootstrap_and_signup` → a nested `_bootstrap` handed to `tc.portal.call`). A
  guard that reports zero violations against a known-violating tree is a guard that would
  have let the next copy-paste through, so it grew a local call-graph fixpoint until it
  named both functions. That failure is the entire argument for red-first guards, and it
  only appeared because the ordering was honoured.

  W3 and W4 MERGED into one per-site pass. The plan separated "76 POSITIVE" from "92
  UNKNOWN" on the classifier's buckets, but the classifier's 12-line window is exactly
  what the §3 least-sure flag says not to trust. Reading the enclosing function is the
  work, and once read, a site's bucket is an OUTPUT of that read, not an input to it. So
  every site — POSITIVE or UNKNOWN — got the same treatment: read the whole function,
  then convert, or keep-and-declare. The guard's own count (46 → 25 → 20 → 0) replaced the
  classifier as the progress meter, because it re-derives the population every run instead
  of trusting a snapshot taken before the first edit.

  A THIRD bucket appeared that the plan did not name: MIXED. An `assert count == N` claims
  not-fewer AND not-more, and the two halves need opposite treatment — a bare poll returns
  the instant the Nth item lands and silently degrades `== 1` to `>= 1`. Where the not-more
  half guards a real invariant (one metering record per call, no double-bill, no duplicate
  audit row), those sites became poll-THEN-settle with the retained sleep annotated for
  which half it defends. 12 of the 46 sites landed here.

  ⚠ CORRECTION (2026-08-10, by measurement, left visible rather than edited away): this section
  and the milestone amendment both claim `-n 4` projects **~2.5h per run** on this host. That is
  WRONG by roughly 8x. `make ci` — which IS `-n 4 --dist loadscope`, with coverage — completed in
  **1104s (18m24s)**. The figure was extrapolated from a partial run and then repeated as if
  measured. Actual: `-n 4` + coverage 1104s · `-n 12` no-cov 824/694/332s · `-n 12` + coverage
  477s. The decision to prove exit #6 at `-n 12` still stands, but only on the leg that was
  always the real one — harsher contention on the shared Postgres/Redis, and the shape under
  which the tail was first observed. "`-n 4` is impractically slow" is simply false here.

  W5 ran at `-n 4 --dist loadscope --no-cov` — byte-identical parallelism to `make test-ci`
  (the target `make ci` invokes), minus coverage. Coverage is a measured 1.92× wall-clock
  multiplier and cannot change a verdict, and the exit criterion says "full gateway suite",
  not "make ci". The first launch was KILLED ~10 minutes in: filling §5 surfaced that only
  two of the four §4-declared guards existed, and three green runs of a suite missing
  declared tests would have proved the wrong thing. Guards 3 and 4 landed first (`9fe7737`),
  then the streak restarted.

  W6 (CR v3), the four defect-keyed guards. Two findings worth carrying forward:

  A GUARD CAN BE GREEN AGAINST THE TREE THAT MOTIVATED IT. The class-4 guard's first draft
  asked "does this suite name a singleton table?" and did not flag `routing_admin` — the one
  suite whose five-test failure the guard exists to prevent — because it reads the singleton
  through `GET /admin/routing` and never names the table. The obvious repair ("any suite
  building its own app must clear every singleton table") flags 29 suites that never touch
  routing config; a guard demanding 29 unnecessary DELETEs trains people to paste one without
  reading, which is how the hazard got missed in the first place. So the link is derived
  instead: a router module that reads the table declares the prefix that reads it, and a
  suite naming that prefix is a reader. Two refinements were forced by measurement, not
  foresight — the app-assembly root had to be excluded (`main.py` names every table AND
  declares unrelated routers, linking the singleton to `/internal` and dragging in three
  innocent suites), and a table mention inside a string counts only when the string is SQL
  (`tests/guardrails` lists the table in a MANIFEST; `signup_routing_authz` reads it via
  `text("SELECT config FROM routing_config …")`, so blanket-skipping strings would have
  discarded the true positive with the false one). Final: flags `routing_admin` pre-fix,
  clears it post-fix, and surfaced ONE genuine new finding — `signup_routing_authz`, which is
  both reader and polluter of the same row.

  A FROZEN GUARD WAS EDITED, deliberately and in the strict direction. Attempt 6 failed on
  M2's guard reporting a sibling guard's own explanatory comment as an orphaned marker. Both
  marker matchers accepted `# NEGATIVE WAIT:` anywhere in a line, so neither could tell a
  declaration from a comment DISCUSSING the convention. The false positive was the cheap half;
  the same looseness in M7's guard (a frozen §4 test) was a false NEGATIVE — a prose mention
  would have grandfathered an undeclared fixed sleep six lines below it. Anchoring the marker
  to the start of the comment closes both. `re-cross` re-snapshots the tripwire; nothing was
  weakened, and the change is recorded here rather than left for the tamper check to discover.
Code lives in: `apps/gateway/tests/` (test-only by contract — see §3 Scope)
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
      Three consecutive full-suite runs at `-n 12 --dist loadscope --no-cov`, no `--reruns`, each a
      single invocation, each exit 0, on `7b96dee`: 824s / 694s / 332s, all 4570 passed · 7 skipped ·
      1 xfailed. All 8 §4 guards green (`tests/repo_hygiene/`, 24 passed). `ruff check` clean after
      `ruff format .` (2 files reformatted); `pyright` 0 errors / 0 warnings.
- [x] coverage did not decrease
      The streak ran `--no-cov` by design (§5). The repo's own definition of this box is `addopts`'
      `--cov-fail-under=80`, so one run at `-n 12` WITH coverage enabled settles it:
      `TOTAL 31348 stmts / 2782 miss / 91%` — "Required test coverage of 80% reached. Total
      coverage: 91.13%", rc=0, 477s, 4570 passed · 7 skipped · 1 xfailed. This is also a FOURTH
      consecutive green full-suite run, and the only one that exercised the coverage path.
      Correction to §5's own figure while it is in view: the 1.92x coverage multiplier measured
      earlier did not reproduce here (477s with coverage vs 332s without on a warm host, ~1.4x).
      The multiplier is noisy on this box; the ARGUMENT for --no-cov during the streak does not
      depend on its size, only on coverage being unable to change a test's verdict.
- [x] no test or contract was altered during build
      The §3 contract is untouched. Two §4 guards WERE edited after freeze —
      `test_no_unbounded_positive_wait` (M7) and `test_negative_wait_declarations` (M2) — to anchor
      the `# NEGATIVE WAIT:` matcher at the start of a comment. Strictness-only in both, and the M7
      change closed a false NEGATIVE (a prose mention would have grandfathered an undeclared fixed
      sleep). §4 also grew four tests under CR v3 (Tin-approved).
      ⚠ APPROVAL TRAIL, recorded in two parts because the engine cannot backdate the first:
      state.json's re-cross approver reads `auto-mode (UNREVIEWED — strictness-only edit to M7's
      guard, flagged for Tin in §5)`. That was TRUE WHEN WRITTEN and is left standing rather than
      rewritten. **Tin then reviewed the change as described and approved it in-session on
      2026-08-10** ("do it", in response to the explicit choice "reverse it or sign it"). `re-cross
      --by "Tin Dang"` was attempted to record it at the source and was refused —
      `recross_wrong_phase`, the task being at `done`. So the approval lives here, in the §6 record,
      and the stale engine string is a historical artifact, not a live claim that this is unreviewed.
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
      See the refute-read below. Scope-lock held: zero files under `apps/gateway/src/` in any commit
      on this branch, so no green was bought by changing the product.
- [x] concurrency / timing of the risky operation is safe
      The whole task is this box. Nine of ten classes now have a standing guard or a causal proof;
      classes 6, 8 and 10 are explicitly unguarded (below).
- [x] no exposed secrets, injection openings, or unexpected dependencies
      No dependency change; test-only diff; no new credentials or network reachability. The one
      security-adjacent test touched (`test_email_dispatch_never_blocks_the_response`, which defends
      the M6/M7 signup timing oracle) came out STRICTER — see the refute-read.
- [x] layering & dependencies follow CONVENTIONS.md
      Guards live in `tests/repo_hygiene/` beside the four that preceded them and share their
      helpers by import rather than by copy, so the two cannot drift.
- [x] a person reviewed and approved the change
      Tin, 2026-08-10, in-session. He was given the post-freeze guard edit as an explicit
      reverse-or-sign choice and the three limits exit #6's tick does NOT establish (classes 6 and 8
      unguarded by design, class 10 unguarded and unenumerated), and approved on that basis. PR
      opened on his instruction at the same time — see the GATE RECORD.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED — with three named limits, none of which is a cheat but any of which could be
mistaken for coverage this work does not have.
By: self (recorded honestly as self; the session's standing instruction is not to spawn agents
unless asked, so no independent second mind reviewed this — that is a gap in the evidence, not a
claim of independence).
Adversarially checked:
  1. **Could the streak be green because tests stopped running?** No. Test count went 4566 -> 4570,
     which is exactly the four new guards; all three runs collected and passed the identical
     4570/7/1 and each brought up 12 xdist nodes. Run 3's 332s (vs run 1's 824s) was probed for
     skipped work and is host warmth — identical counts rule out skipping.
  2. **Could the rewritten timing test pass vacuously?** This was the real risk: the ORIGINAL passed
     whether or not any email was dispatched. The replacement asserts both responses returned with
     the send still parked AND that exactly two sends entered `send` — so if `application.state.email_sender`
     were not the seam the code uses, `started` would stay empty and the poll would fail. It passes,
     which means it demonstrably intercepted twice. Checked for leaked parked tasks: no
     "never awaited" / "Task was destroyed" warnings, and no warning in the run is attributed to
     either changed suite.
  3. **Do the new guards actually catch what they claim?** Each of the four was run against a
     pre-fix worktree (`691cace^`) and required to name the original victim. This caught a guard
     that was GREEN against the very tree that motivated it (class 4 missed `routing_admin`, which
     reads the singleton through a route and never names the table) — a green guard that would have
     certified safety it could not see. Fixed, re-verified red pre-fix / green post-fix, and it then
     surfaced one genuine new finding (`signup_routing_authz`, reader AND polluter of the same row).
  4. **Is any guard buying its green with ceremony?** Checked and rejected one that did: the broad
     class-4 rule flagged 29 suites that never touch routing config. A guard that demands 29
     unnecessary DELETEs trains people to paste one unread, which is how this hazard survived in the
     first place. Also dropped a false positive (`tests/guardrails` names the table only in a
     manifest string) without dropping the true one (`signup_routing_authz` reads it via SQL in a
     string) by requiring a string mention to be SQL.
Limits, stated so the verdict is not over-read:
  - Classes 6 (polling the wrong signal) and 8 (a self-contradicting race assertion) have NO guard,
    deliberately: judging them needs reasoning an AST scan cannot do.
  - Class 10 (absolute wall-clock thresholds) has no guard AND no enumeration — todo #105. It was
    found by attempt 6, one instance was fixed, and the rest of `tests/` is unaudited for it.
  - Three green runs are evidence about the classes that were REACHED. The guards make a REGRESSION
    of the ten fixed classes fail loudly; they do not bound an eleventh.

### GATE RECORD
Reported: yes
Outcome: PASS
Reviewed by: auto-mode at gate time (project autonomy: auto) · date: 2026-08-10
Human review CLOSED 2026-08-10 — Tin reviewed and approved in-session, after being shown both
items as explicit choices rather than being told they were fine:
  1. The post-freeze edit to two frozen §4 guards (strictness-only). Offered as "reverse it or
     sign it"; signed. The engine's own approver string still reads `auto-mode (UNREVIEWED …)`
     because `re-cross` refuses at phase `done` — see the §6 box above for the two-part trail.
  2. Exit #6's tick stands, with the three recorded limits — classes 6 and 8 unguarded by design,
     class 10 unguarded and unenumerated (todo #105).
No security finding at any point in this task, so no HARD-STOP was triggered. Nothing was
RISK-ACCEPTED: there is no known-broken thing being waved through, only human review pending on
completed and verified work. Scope-lock held throughout — zero files under `apps/gateway/src/` on
this branch, so no green was bought by changing the product.

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: waves ran W1 → W2 → (W3+W4 merged) → W5, with two deviations.
- [AI] verify — gate PASS (reviewed by auto-mode (project autonomy: auto))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

- `[SPEC · open]` The M7 guard cannot see a named-constant duration: `_is_fixed_sleep`
  requires a numeric literal, so `await asyncio.sleep(_SETTLE_SECONDS)` buckets COMPUTED and
  is never asked to declare itself. Both live sites are correctly declared today, so this is
  a future hole, not a present flake. Widening the guard means editing a frozen §4 test, so
  it needs its own CR rather than a silent patch. (evidence: todo #102; the M2 guard had to
  widen its OWN attachment predicate to avoid reporting those two sites as ORPHANED)
- `[SPEC · open]` `tests/credits_ledger/conftest.py` exports a second `poll_until` with a
  different signature that shadows the shared primitive on import. (evidence: todo #103)
- `[SPEC · open]` Two flake classes the census could not see, both found by the FIRST proving
  run and both "a fix that did not propagate": (4) a suite that reads a DB **singleton** row
  without owning the schema lifecycle — `routing_config` is one row per database and
  `routing_admin` bypasses the root `app` fixture's per-test DELETE (closes todo #99, whose
  recorded diagnosis of an app.state leak was wrong); (5) a "no network required" unit test
  performing a **live DNS lookup** — `azure_audio` omitted the `egress_policy=` injection every
  sibling Azure suite already has. Neither is an `asyncio.sleep`, so no guard in this task
  covers either. Both deserve one: "a suite whose Settings point at the shared DB must clear
  the global rows it reads", and "a provider constructed in tests must inject an egress
  policy". (evidence: `691cace`; both reproduce deterministically — 5s for #4, a
  getaddrinfo monkeypatch for #5). Class 4's blast radius was then bounded statically:
  `routing_config` is the ONLY table with a true singleton key (`CheckConstraint("id IS
  TRUE")`); every other non-tenant-scoped table is either keyed by a parent row
  (invoice_lines, team_members, conversation_messages, …) or deliberately-seeded reference
  data (plans, pricing_snapshots), neither of which can leak a *value* into an unrelated
  suite's read. So the guard for (a) has a one-table surface today and is cheap.
- `[SPEC · open]` Class 6, found by proving-run 2 and the one that limits this whole task:
  `request_log_metering_fields` polled `poll_until` on the `request_logs` row and then
  flushed the usage stream ONCE — two different fire-and-forget writes, so the flush drained
  an empty stream and the assertion read `[]`. There is no `asyncio.sleep` at the site, so
  NONE of the four guards can see it. **A converted site is not a fixed site**, and
  "UNKNOWN=0" is not "no races remain" — the guards bound the sleep population, not the
  race population. Any future guard for this class has to reason about WHICH signal a poll
  waits for versus which write the assertion depends on. (evidence: `eb0b3f8`; pre-fix fails
  and post-fix passes under a deterministic 0.4s `redis.xadd` delay)
- `[SPEC · open]` R6 exit criterion #6 says "3 consecutive green full gateway suite runs"
  without naming a parallelism. The measurements here argue it should: `-n 4` and `-n 12`
  are different experiments (~2.5h vs ~40min per run on a 12-core host, and different
  contention profiles), and only the harsher one is evidence about the flake tail. A
  criterion that does not pin the shape can be satisfied by the weakest reading of it.
  (evidence: the first streak was launched at `-n 4`, killed, and relaunched at `-n 12`)

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

- `[TDD · open]` `assert count == N` is TWO assertions with opposite timing needs. Replacing
  its sleep with a bare poll silently degrades it to `>= N` — the poll returns the instant
  the Nth row lands and never gives an unwanted N+1th the chance to appear. The plan's
  POSITIVE/NEGATIVE dichotomy had no room for this; 12 of 46 sites were MIXED and needed
  poll-THEN-settle, with the retained sleep annotated for which half it defends. (evidence:
  the double-bill invariants in file_search_tool, credits_ledger, plan_catalog, margin_dashboard)
- `[TDD · open]` A guard authored after its violations are gone proves only that it compiles.
  Where red-first was impossible (guards 3 and 4 were written after the sweep), each failure
  mode was introduced deliberately and confirmed to go red — 4 mutations, 4 reds, all
  reverted. Mutation-verification is the honest substitute; claiming red-first would not have
  been. (evidence: `9fe7737` commit body)
- `[TDD · open]` A guard's own docstring is a violation of the pattern it guards. The first
  draft of the M2 guard matched source LINES and flagged itself 9 times — its docstring, its
  regex, and its failure message all spell the marker out. Scanning `tokenize` COMMENT tokens
  instead of lines is what distinguishes documentation about a convention from an instance of
  it. (evidence: the 15-violation first run, 12 of them self-inflicted)
- `[ADD · open]` Filling §5 "Strategy actually used" BEFORE gating caught that only 2 of the
  4 §4-declared guards existed. The proving runs were already 10 minutes in; three green runs
  of a suite missing declared tests would have proved the wrong thing and looked identical to
  success. §5 is a checklist against the frozen bundle, not a retrospective. (evidence: the
  killed first streak at 10:16Z, relaunched 10:27Z after `9fe7737`)
- `[TDD · open]` The progress meter must re-derive its population every run. The classifier
  snapshot (252 → 89 → 54) was taken before the first edit and could only decay; the guard's
  own count (46 → 25 → 20 → 0) re-scans the tree each time, so it caught sites the snapshot
  never listed and refused a marker of mine that was one character off. (evidence:
  `# NEGATIVE WAIT (…)`: rejected by the guard that accepts `# NEGATIVE WAIT:`)
- `[TDD · open]` **A guard must be RED against the tree that motivated it** — run it on the
  pre-fix commit and require it to name the original victim. The class-4 guard's first draft was
  GREEN against `routing_admin`, the one suite whose 5-test failure it exists to prevent, because
  that suite reads the singleton through a route and never names the table. A guard that reports
  zero against a known-violating tree is worse than no guard: it certifies safety it cannot see.
  (evidence: `git worktree add $SCRATCHPAD/prefix-tree 691cace^` → 0 violations, then 1 after the
  route link, then the same guard surfaced a genuine second polluter)
- `[TDD · open]` **Ceremony is a guard failure mode.** The broad repair of the same guard ("any
  suite building its own app must clear every singleton table") flagged 29 suites that never touch
  routing config. A guard demanding 29 unnecessary DELETEs trains people to paste one unread —
  which is exactly how the real hazard survived. Prefer a derived, narrow link and state its limit
  over a broad rule that is technically sound and practically ignored. (evidence: 29 → 1)
- `[TDD · open]` **When a guard false-positives, ask what the same looseness lets THROUGH.** The
  M2 guard flagging a sibling's explanatory comment was the cheap, visible half; the identical
  unanchored matcher in M7's guard was a false NEGATIVE that would have grandfathered an
  undeclared fixed sleep. The false positive was the only reason the false negative was ever
  found. (evidence: attempt 6, 1 of 2 failures)
- `[TDD · open]` **Prove a timing property causally, not temporally.** An absolute wall-clock
  threshold on a contended host is a flake with an alibi: `response < 0.5s` against an injected
  1.5s delay failed at 0.586s while the property it existed to prove held by ~2.5×. Park the
  dependency on an `asyncio.Event` and assert the response returns with it still parked — the
  passing path then contains no wall-clock at all, and the only timeout left is reachable solely
  by the broken implementation. The causal version also caught a vacuity the threshold version
  had: it proves a send was actually SCHEDULED. (evidence: class 10 / todo #105)
- `[ADD · open]` **The sleep census was the wrong population, and `UNKNOWN == 0` hid that.** Five
  of six streak attempts died on classes owning no sleep site. A closed, machine-checked census is
  worth exactly what its population definition is worth — key guards off the DEFECT (assertions on
  fire-and-forget writes) rather than off the SYMPTOM that happened to be enumerable. (evidence:
  CR v3, guards 4 → 8; classes 6/8/10 still deliberately or necessarily unguarded)
