# PLAN: Fail fast when test infrastructure is absent, and abort when it dies mid-run

slug: suite-infra-tripwire · created: 2026-08-06 · stage: production
milestone: release-integrity
autonomy: auto
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: The test suite refuses to start when its infrastructure is absent, and ABORTS when
that infrastructure dies mid-run — instead of emitting thousands of connection errors that
are indistinguishable from a real code regression.

Framings weighed:
- **Both halves: a start preflight AND a mid-run tripwire** (chosen). Todo #83 as originally
  written asked only for the preflight, but the incident that produced it was the OTHER
  failure: on 2026-08-05 `make ci` ran 2265 tests GREEN, then `hydroa-dev-postgres-1` exited,
  and the run finished `131 failed, 2130 errors` after 43 minutes. A start-only preflight
  would have sailed straight past that. Fixing only the half the todo names would leave the
  actual incident unaddressed.
- Preflight only — rejected: see above. It is the cheaper half and the less valuable one.
- Tripwire only — rejected: absent-at-start is the common developer case (stack not up yet)
  and deserves an instant, named answer rather than a mid-collection asyncpg traceback.
- A Makefile prerequisite target — rejected as the primary mechanism: it only guards
  invocations that go through `make`. The 2026-08-05 incident and most day-to-day runs are
  bare `uv run pytest`. A pytest hook covers every path, `make` included.

Must:
<must>
  - M1 a session whose Postgres is unreachable, or whose base test database is missing, or
    whose Redis is unreachable, STOPS before running tests, with a message naming which
    dependency failed and how to start it
  - M2 a session that loses its infrastructure mid-run ABORTS once N consecutive tests fail
    with a connection error, rather than continuing through the remaining suite
  - M3 the abort message states plainly that the run is INVALID as evidence — the failures
    are infrastructure, not the code under test
  - M4 `make test-fast` (deliberately DB-free: MockTransport/pure-unit suites) still runs
    with no Postgres and no Redis — the guard must not break it
  - M5 under xdist the preflight runs ONCE, on the controller, not once per worker
</must>
Reject:
<reject>
  - a single isolated connection error (a flake, or a test that deliberately probes a closed
    port) -> NO abort; only N CONSECUTIVE connection failures trip the tripwire
</reject>
After:
<after>
  - a red `make ci` can be trusted to mean "the code is broken", because the one other thing
    it used to mean now announces itself
  - todo #83 is closed, both halves
</after>
Boundary: none — no external request input. The input shapes are: infra up · Postgres down ·
Redis down · base database absent · infra dies after N passing tests.
<assumptions>
  ⚠ That connection failures can be recognised RELIABLY from a test report's text. The matcher
  keys on observed strings (`Connect call failed`, `ConnectionRefusedError`,
  `connection_lost`, `Connection refused`). If wrong in the false-NEGATIVE direction the
  tripwire silently never fires and we are back to today — tolerable, it is a safety net over
  existing behavior. If wrong in the false-POSITIVE direction it would abort a legitimate run,
  which is far worse: that is why the trip requires N CONSECUTIVE failures and why tests that
  deliberately probe a dead port (e.g. `vector_extension_preflight::
  test_unreachable_database_is_unknown_not_missing`) are safe — they PASS, and only
  failures/errors are counted.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

Grounding (verified in-tree 2026-08-06):
- `tests/_redis_env.py` — the single source of truth for endpoints:
  `TEST_DATABASE_URL` (env `GATEWAY_TEST_DATABASE_URL`, default
  `postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test`) and
  `_REDIS_HOST_PORT` (env `GATEWAY_TEST_REDIS_HOSTPORT`, default `localhost:6380`). The
  guard reads these, never its own literals, so a retargeted stack stays consistent.
  `_REDIS_HOST_PORT` is PRIVATE (pyright reportPrivateUsage fails on an outside read), so
  the guard derives the endpoint from the public `TEST_REDIS_URL`, or `_redis_env.py` grows
  a public accessor — hence that file is in Scope.
- `tests/conftest.py::_ensure_worker_database` — session-scoped, already opens the first
  connection with a bounded timeout and already handles the serial-run `CREATE EXTENSION`.
  It is a FIXTURE, so it only fires for tests that reach it — which is exactly why
  `make test-fast` runs DB-free today, and why the preflight must not be unconditional.
- `xdist/remote.py:136` — workers run their OWN `pytest_sessionstart`; `config.workerinput`
  exists ONLY in a worker (`xdist/plugin.py:349`). VERIFIED by reading the installed
  package, not assumed. Hence M5: gate on `not hasattr(config, "workerinput")`.
  The controller also receives every worker's `pytest_runtest_logreport`, so the same gate
  gives the tripwire whole-run visibility.
- `Makefile` — `test-fast` is explicitly the "no-DB blast-radius" target; `test` and
  `test-parallel` are the infra-bound ones. `ci: lint typecheck allowlist allowlist-node test`.
- Incident of record: `scratchpad/ci-final.log` — 2265 passed, then `131 failed, 2130 errors`,
  every one `OSError: Connect call failed ('127.0.0.1', 5433)`.

```
Module: tests/_infra_guard.py   (pure, unit-testable — the hooks stay thin wrappers)

  def check_infra(*, database_url, redis_hostport, timeout) -> list[str]
        [] -> everything reachable
        [problem, ...] -> one human-readable line per failed dependency, each naming
                          the dependency, the endpoint tried, and how to start it

  class InfraTripwire(threshold: int = 5)
        .record(failed: bool, text: str) -> None    # consecutive-run counter
        .tripped -> bool                            # threshold consecutive conn failures
        .reason  -> str                             # the INVALID-RUN message (M3)
        A non-connection failure, or any pass, RESETS the counter (R: no abort on a
        single isolated connection error).

  def looks_like_connection_loss(text: str) -> bool   # the matcher, tested directly

  def is_controller(config) -> bool                   # M5, extracted so it is testable
        False when the config carries xdist's `workerinput` (i.e. we are IN a worker).
        A hook body cannot be unit-tested; this predicate can, so the xdist rule is
        gated by a test rather than asserted in a comment.

Hooks in tests/conftest.py (thin):
  pytest_sessionstart(session)        -> controller-only; skip when GATEWAY_TEST_SKIP_INFRA_CHECK
                                         is set; on problems -> pytest.exit(msg, returncode=4)
  pytest_runtest_logreport(report)    -> controller-only; feed the tripwire; on trip ->
                                         pytest.exit(reason, returncode=4)
Makefile: `test-fast` sets GATEWAY_TEST_SKIP_INFRA_CHECK=1 (M4). Default is ON everywhere
  else, so a bare `uv run pytest` is guarded too — that is the path the incident took.
```

Target (measurable): the §4 suite runs RED before build and GREEN after. `make ci` stays green
at main's current bar (4519 passed, 0 failed). Two outcomes tests assert only indirectly are
confirmed by hand and recorded in §6: (a) with Postgres stopped, `uv run pytest` exits in
< 15s naming Postgres — versus today's multi-minute grind; (b) `make test-fast` still passes
with BOTH containers stopped.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Strategy: put the LOGIC in a pure module (`tests/_infra_guard.py`, mirroring the existing
`_polling.py` / `_redis_env.py` convention) and keep the two conftest hooks as thin wrappers.
Pure functions are directly unit-testable; a hook is not. Build order: red suite over the pure
logic first, then the module, then the hooks, then the Makefile opt-out for `test-fast`.

Scope (may touch): `apps/gateway/tests/_infra_guard.py` · `apps/gateway/tests/conftest.py` · `apps/gateway/tests/_redis_env.py` · `apps/gateway/tests/suite_infra_tripwire/` · `./../../../Makefile`
Regression floor: full `make ci` (the guard sits in the shared conftest, so its blast radius IS the whole suite — anything less would not prove M4/M5). Plus a manual `make test-fast` with both containers stopped.
Persona (optional): `sre-reliability-engineer` — "verify the environment, degrade safely, never fail silently."

Least-sure flag surfaced at freeze: [contract] — the tripwire threshold and the matcher. A
false POSITIVE aborts a legitimate run, which is strictly worse than the status quo it
replaces; 5 consecutive + reset-on-anything-else is my judgement call, not a measured value.
§1's ⚠ carries the reasoning and why deliberate dead-port tests are safe (they pass).

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_check_infra_reports_unreachable_postgres: point check_infra at a closed port; assert
    a problem line naming Postgres and the endpoint tried · covers: M1
  - test_check_infra_reports_missing_database: point it at a live server but a database name
    that does not exist; assert a problem line distinct from "unreachable" — "the server is up
    but gateway_test is gone" is a different remedy · covers: M1
  - test_check_infra_reports_unreachable_redis: closed Redis port, live Postgres; assert the
    problem names Redis and NOT Postgres · covers: M1
  - test_check_infra_returns_empty_when_everything_is_up: against the real dev stack, assert []
    — the false-positive arm; a guard that cries wolf blocks every run · covers: M1, M4
  - test_problem_lines_say_how_to_start_the_stack: every problem line must name the
    docker compose command, so the message is actionable without reading source · covers: M1
  - test_tripwire_fires_after_consecutive_connection_failures: feed N connection-shaped
    failures; assert .tripped and that .reason says the run is INVALID · covers: M2, M3
  - test_tripwire_ignores_a_single_isolated_connection_failure: one conn failure then a pass;
    assert NOT tripped · covers: R
  - test_tripwire_resets_on_a_non_connection_failure: conn failures interleaved with an
    ordinary assertion failure; assert NOT tripped — a genuinely broken suite must still
    report its own failures rather than being blamed on infra · covers: R
  - test_matcher_recognises_the_observed_incident_strings: the literal texts from the
    2026-08-05 run ("Connect call failed", "ConnectionRefusedError",
    "unexpected connection_lost() call") · covers: M2
  - test_matcher_does_not_match_ordinary_assertion_text: an AssertionError body mentioning
    the word "connection" must NOT match — the false-positive direction · covers: R
  - test_preflight_is_controller_only_under_xdist: a config carrying `workerinput` must be
    skipped; one without it must be checked · covers: M5
</test_plan>

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/suite_infra_tripwire/` · MUST run red (missing implementation) before Build.

RED CONFIRMED 2026-08-06 — 12 failed in 0.24s, every one `ModuleNotFoundError: No module
named 'tests._infra_guard'`. Red for the RIGHT reason: the contract module does not exist.
Command: `uv run pytest tests/suite_infra_tripwire -p no:cacheprovider --no-cov -q`.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned, with THREE corrections the build forced:

1. **The tripwire must read `setup` reports, not just `call`.** The frozen §3 said "feed the
   tripwire" without naming the report phase, and my first hook body took `call` only. That
   would have watched the entire 2026-08-05 incident go past: those 2130 were ERRORS, not
   failures — a dead Postgres breaks the `app`/`_schema` FIXTURES, which pytest reports at
   `setup`. Now: `setup`-if-failed plus `call`, never `teardown`, so exactly one report per
   test feeds the consecutive counter. Caught by re-reading the incident log, not by a test —
   §4 unit-tests `InfraTripwire` directly and cannot see which reports the hook forwards. The
   end-to-end probe below is what actually proves it.
2. **`looks_like_connection_loss` refuses any text containing `AssertionError`.** Not in the
   frozen shape; added because the false-positive direction is the dangerous one and this
   repo really does assert about connection pools and connection ids. Costs a false negative
   when infra loss surfaces through an assert — which §1 explicitly accepts.
3. **`_connection_line` picks the marker line out of the traceback.** The first version
   printed `splitlines()[-1]`, which is pytest's source-location footer
   (`tests/x.py:5: OSError`) — where the test was, not what died. Now the abort message
   shows `OSError: Connect call failed ('127.0.0.1', 5433)`.

Two smaller ones: `redis_hostport()` was added to the guard because `_redis_env._REDIS_HOST_PORT`
is private and pyright rejects an outside read; and `asyncpg.connect(timeout=)` is typed `int`,
so the float is rounded (the outer `asyncio.timeout` is the real bound either way).

Code lives in: `src/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
      §4: 12/12 (red 12/12 before build). Floor: `make ci` GREEN — `4531 passed, 7 skipped,
      28 deselected, 1 xfailed, 0 failed` in 40:17, EXIT=0, `All checks passed!`
      (lint · typecheck · allowlist · allowlist-node). main's baseline was 4519 passed;
      +12 is exactly this task's suite. Log: scratchpad/ci-final3.log.
      ⚠ Two earlier floor attempts are recorded here because they are NOT evidence:
      run #1 (2 failed) and run #2 (42 failed) were both taken while a foreign
      `infra-billing-postgres-1` held port 5433 and this repo's stack was retargeted.
      Run #1's `tests/ops/test_probes.py::test_ready_probe_200_both_healthy` fails because
      it builds a bare `Settings()` whose default is the literal `localhost:5433` — it is
      structurally blind to GATEWAY_TEST_DATABASE_URL. Run #2's 42 are DuplicateTableError
      in tests/migrations + tests/catalog_db_seed, caused by forcing GATEWAY_DATABASE_URL
      (my error). Only the third run, on the real stack with no env overrides, is the floor.
- [x] coverage did not decrease — 90.94% vs main's 90.95%. A 0.01pp move, and NOT this
      change: run #1 with the same guard in place reported 90.95%. Run-to-run variance in
      which lines execute, well inside noise and far above the 80% gate. Recorded rather
      than rounded away.
- [x] no test or contract was altered during build — `add.py check` clean; the engine's own
      `_tripwire_divergence` returns `[]` (no contract_tampered, no test hash drift).
      §3 Scope was re-encoded from a bare `Makefile` token (which the grammar resolved to
      the non-existent `apps/gateway/tests/suite_infra_tripwire/Makefile`) to
      `./../../../Makefile` BEFORE build entry; same file, same semantics, no shape change —
      confirmed by the divergence check above.
- [x] the green was EARNED, not gamed — see the refute-read below. The one arm that could
      have been vacuous was caught DURING direction: an earlier draft of the healthy-stack
      test asserted only that the module imported. It now asserts `check_infra(...) == []`
      against the live stack, so a guard that starts crying wolf fails this suite.
- [x] concurrency / timing of the risky operation is safe — every probe is bounded
      (`asyncio.timeout` + asyncpg/redis socket timeouts); `check_infra` cannot raise, so a
      guard fault degrades to a reported problem, never a collection crash. The two probes
      run concurrently under `gather(return_exceptions=True)` so one cannot swallow the
      other's verdict. Whole-suite proof: 4531 tests, 40 minutes, ZERO false trips.
- [x] no exposed secrets, injection openings, or unexpected dependencies — see refute-read;
      no new packages (asyncpg + redis already in the allowlist; `make ci`'s allowlist and
      allowlist-node checks both pass).
- [x] layering & dependencies follow CONVENTIONS.md — the guard lives in `tests/` beside
      `_redis_env.py` / `_polling.py`, imports nothing from `gateway.*`, and the production
      tree is untouched (`git status`: only Makefile + tests/ + .add/).
- [ ] a person reviewed and approved the change — PENDING Tin.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked:
  1. **Credential leakage into CI logs** (security — would have been HARD-STOP). The
     problem messages interpolate the raw exception, and they land in CI output. Attacked
     with a password-bearing DSN across all four failure paths — auth-refused, unreachable,
     missing-database, and malformed-URL. None echoed the secret; only host:port and the
     database name are ever emitted, and asyncpg's ClientConfigurationError describes the
     expected scheme without quoting the input. NOT REFUTED — no finding.
  2. **"5 consecutive" is meaningless under xdist**, where 12 workers interleave, so it is
     not 5-in-a-row on any one worker. True, and it cuts the SAFE way: a lone dead-port
     failure on one worker is reset by another worker's pass almost immediately, making
     false positives harder, while a real outage fails every worker at once. NOT REFUTED.
  3. **`pytest.exit` from `pytest_runtest_logreport` may not stop an xdist run** — proven
     serially but not under `-n`, which would have left `make test-parallel` unguarded.
     Probed directly: 30 failing tests at `-n 2 --dist loadscope` aborted after exactly 5,
     exit code 4, tests 05–29 never ran. NOT REFUTED — residue CLOSED, not carried.
  4. **The tripwire watches the wrong pytest phase.** REFUTED — and this one was REAL. The
     first hook body read `call` only; the 2026-08-05 incident produced 2130 ERRORS, which
     pytest reports at `setup`. Fixed in build (§5 correction 1) and proven by the
     end-to-end probes, which the §4 unit tests structurally cannot see.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-08-07

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned, with THREE corrections the build forced:
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
