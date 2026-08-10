# PLAN: Kill the test-suite flake tail: 4 CI-red sites, a drop_all-after-lifespan guard, and the 89 unclassified fixed sleeps

slug: flake-tail-burndown · created: 2026-08-10 · stage: production
milestone: release-integrity
autonomy: auto   <!-- manual<conservative<auto — lower for high-risk (`add.py autonomy set`); a `component: <name>` line joins that root to §3 Scope; task edges: `--depends-on`/`--extends`/`--relates-to`; high-risk/method-defining? declare `risk: high` on the slug line; headless agent-crossed freeze? declare `gate_mode: ai-plan-verify` here (human floor: security|data|architecture never AI-frozen) -->
phase: build   <!-- direction→build→verify→done; direction drafts §1–§4 (rules · change plan · red suite) to the ONE freeze -->
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

<!-- §2 (the old standalone SCENARIOS section) was RETIRED — pass/fail cases now live with the tests in §4 · TESTS & SCENARIOS. The §3–§7 numbers are unchanged so the freeze parser and every §-reference keep working; the jump from §1 to §3 is intentional. -->

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

<!-- CR v2 — amended 2026-08-10, approved by Tin Dang. WAS `asyncio.sleep(<literal>)`;
     NOW `asyncio.sleep(<literal > 0>)`. A NARROWING, and the reason is a defect-class
     distinction, not convenience.

     M7 exists to catch "someone guessed a duration and the guess fails under load."
     `asyncio.sleep(0)` contains NO duration to guess wrong — it is a single event-loop
     yield. Whether one yield suffices is a DETERMINISTIC property of the callee (does it
     await internally?), not a timing race.

     EVIDENCE (traced 2026-08-10): the billing path fires
     `asyncio.ensure_future(usage_recorder.record(**kwargs))` (use_cases.py:543), and the
     test double is `async def record(...): self.calls.append(dict(kwargs))` — ZERO internal
     awaits. A coroutine with no internal awaits runs start-to-finish on its FIRST
     scheduling step, and `sleep(0)` yields exactly once, which is precisely enough. Host
     load changes how long a step takes, never whether the loop schedules a ready task
     before resuming the yielder. All 10 sites in
     tests/image_edits_variations/test_image_edits_variations.py are this shape, and NO
     observed CI failure has ever been attributed to a `sleep(0)` site.

     EFFECT: the actionable population drops 89 -> 54, all genuine duration guesses.
     M4 ("zero unclassified") still holds — the 35 `sleep(0)` sites become an explicitly
     classified LOOP-YIELD bucket (deterministic, no action), not an unexamined one.

     RESIDUAL RISK, accepted and filed as its own todo rather than hidden here: if some
     `sleep(0)` site's fire-and-forget target DOES perform real IO, one yield is
     insufficient — but that fails DETERMINISTICALLY, on an idle laptop too. It would
     already be a hard red, never part of the rotating tail this task is closing. -->

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

<!-- The freeze IS the one approval, led by the bundle's lowest-confidence flag — Contract + Scope (may touch) = HARD (tamper-guarded); Strategy · Regression floor · Persona = SOFT/optional. Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen Contract = change request back to SPECIFY. Scope tokens, backticked: `./…` = this task dir · a "/" token = project root · a bare name = sibling of the previous token's dir · a directory covers its whole subtree · outside-root drops fail-closed · absent line = UNDECLARED (grandfathered, never retro-red). -->

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
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0. The test_plan bullets' `covers:` tails are machine-read too: `add.py locate path::test_name` resolves a failing test to the frozen §3 clause it proves -->
<!-- NON-CODING task (kind: docs · release · infra, or a non-coding project)? §4 is a failing-first ACCEPTANCE CHECK, not a script — verifiable pass/fail evidence (mkdocs build succeeds · §X covers A/B/C · every internal link resolves), red before the artifact exists and green after. Set `Tests live in: evidence` (no `./tests/`). The red→green discipline holds; only the must-be-executable-code requirement is lifted. -->

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

  W5 ran at `-n 4 --dist loadscope --no-cov` — byte-identical parallelism to `make test-ci`
  (the target `make ci` invokes), minus coverage. Coverage is a measured 1.92× wall-clock
  multiplier and cannot change a verdict, and the exit criterion says "full gateway suite",
  not "make ci". The first launch was KILLED ~10 minutes in: filling §5 surfaced that only
  two of the four §4-declared guards existed, and three green runs of a suite missing
  declared tests would have proved the wrong thing. Guards 3 and 4 landed first (`9fe7737`),
  then the streak restarted.
Code lives in: `apps/gateway/tests/` (test-only by contract — see §3 Scope)
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

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Refute-read verdict is recorded, never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

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
