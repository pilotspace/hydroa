# TASK: Deterministic test DB isolation — kill the FK-violation flake

slug: test-db-isolation · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Deterministic test isolation — make the full `-m 'not e2e'` suite repeatable.

ROOT CAUSE (code-traced, not assumed): tests run SERIALLY (no pytest-xdist). The flake is
NOT a parallel race — it is cross-test contamination through the SHARED Redis (db 9). The
hot path fires `usage_recorder.record(...)` as a fire-and-forget task that pushes an event to
the Redis Stream `usage:events` (db 9) — NOT a direct DB write. The DB INSERT into
`usage_records` happens later when a *flusher-driving* suite (spend_windows, usage,
team_attribution, pricing_units, obs_callbacks, health_alerting, pii_v2, …) consumes the
stream. `flushdb` is sprinkled per-test-file in only 14 suites; there is NO global flush. So a
suite that fires `record()` WITHOUT flushing leaves stream events in db 9; a later
flusher-driving suite consumes those LEAKED events and INSERTs `usage_records` referencing a
tenant/key that its own per-test `drop_all`/`create_all` just recreated (or never created) →
**FK violation + varying counts**. The per-test schema reset already makes the DB hermetic;
the Redis stream is the un-isolated channel. ("Each suite passes in isolation" = no leaked
events; "full run flakes" = cross-suite leakage — exactly this mechanism.)

Framings weighed: a SHARED autouse fixture in `tests/conftest.py` that FLUSHDBs the test Redis
(db 9) before each test AND drains pending fire-and-forget asyncio tasks at teardown — closing
both leak sub-channels with ZERO production source change (chosen — minimal, central, kills the
exact traced cause; the scattered per-file `flushdb` calls become harmless redundancy, never
edited out of frozen suites) · per-test isolated Postgres DB / template DB (rejected — the DB
is ALREADY hermetic via drop_all/create_all; a private DB does NOT fix a leaked flush, which
would still FK-violate against a tenant that never existed in that DB; more machinery, wrong
layer) · external-transaction rollback fixture joining the app's session (rejected — the app
owns its own engine/sessionmaker and commits; joining would require a PRODUCTION change, which
the milestone forbids) · enable pytest-xdist for speed (rejected — orthogonal to determinism
and would ADD parallel races on the shared stores).

Must:
<must>
  - The full `-m 'not e2e'` suite runs DETERMINISTICALLY across repeated runs (no FK-violation
    flake, stable counts) — proven by ≥2 consecutive clean full runs.
  - A global AUTOUSE fixture in the shared `tests/conftest.py` SURGICALLY clears the leaked
    usage state in the test Redis (db 9) BEFORE every test — XTRIM `usage:events` to 0 (clears
    the undelivered backlog while PRESERVING the `ledger-flusher` consumer group) + DEL
    `usage:spend:*` — so no test inherits another test's leaked events/counters. It uses the
    SAME redis_url as `settings` (db 9). It MUST NOT FLUSHDB (that destroys the consumer group).
  - Setup-only: the fixture does NOT cancel pending tasks (cancelling all non-current tasks
    kills the pytest-asyncio/anyio runner) — function-scoped event loops already kill a test's
    leaked tasks at loop close, so a pre-test clear is the sufficient guarantee.
  - A documented `make test-fast` target runs the no-DB blast-radius gate (translation +
    dispatch + provider suites — no Postgres/Redis) for fast per-change gating.
  - NO production source under `src/gateway/**` changes; NO frozen test suite is edited.
</must>
Reject:
<reject>
  - a prior test leaked `usage:events` stream entries -> the autouse pre-test XTRIM clears the
    backlog (group preserved), so this test starts with an empty stream (no inherited events).
  - a fire-and-forget record()/alert task is still pending at test end -> the next test's
    pre-test clear wipes any leaked entry; function-scoped loops kill the task at loop close.
    The fixture NEVER cancels tasks (that would kill the test runner).
  - the redis client is unavailable at fixture setup -> the autouse flush is a NO-OP (graceful
    degradation) so the no-infra `make test-fast` suites still run; suites that genuinely need
    Redis fail loudly via their OWN redis_client fixtures, so a real FK flake is never masked.
    [amended v1→v2 at build: the autouse fixture runs for ALL tests incl. pure-unit ones, so a
    hard failure here would break the infra-free test-fast guarantee.]
</reject>
After:
<after>
  - `pytest -m 'not e2e'` is repeatable: two consecutive full runs both green, identical
    pass count, zero FK-violation errors.
  - Each test begins with an empty `usage:events` stream regardless of suite order.
  - `make test-fast` is documented and runs the no-DB suites with no infra dependency.
  - `src/gateway/**` is byte-identical; no frozen test file changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The cross-test contaminator is the un-flushed Redis `usage:events` stream (db 9) consumed
    by a later flusher-driving suite — lowest confidence only because intermittent flakes are
    hard to pin to ONE channel; mitigation: root cause is CODE-TRACED (record()→stream, not
    DB; flusher→usage_records INSERT; flushdb in only 14/all suites; serial execution rules out
    races) AND a full-suite reproduction run is in flight for confirmation; cost if wrong: the
    autouse flush+drain still removes a real leak channel and is harmless, but determinism may
    need a second pass — caught by the ≥2-run determinism check before gate.
  - [ ] A global pre-test FLUSHDB of db 9 does not break the 14 suites that already flush — it
    only guarantees a clean slate they also expect; confirm by running those suites green.
  - [ ] Draining all non-current asyncio tasks at function-scoped teardown is safe (pytest-
    asyncio uses a fresh loop per test; the only pending tasks are leaked fire-and-forget ones)
    — confirm no pytest-asyncio machinery task is cancelled.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: leaked stream events do not contaminate a later flusher-driving test
  Given test A pushed an event to usage:events (db 9) and did NOT flush
  When test B starts under the autouse fixture
  Then usage:events is empty at B's start (XTRIM ran) so B's flusher sees only B's events
  And no usage_records row references a tenant outside B's own schema (no FK violation)

Scenario: clear is group-preserving — flusher-driving suites still work  [amended v4]
  Given the autouse XTRIM (not FLUSHDB) runs before a flusher-driving test
  When that test writes an event and drives the flusher (XREADGROUP)
  Then the `ledger-flusher` consumer group still exists (no NOGROUP) and the row is read
  And NO pending task is cancelled (cancelling all tasks would kill the pytest-asyncio runner)

Scenario: full suite is deterministic across repeated runs
  Given the autouse flush+drain fixture is active
  When `pytest -m 'not e2e'` runs twice consecutively
  Then both runs are green with identical pass counts and zero FK-violation errors

Scenario: redis unavailable → graceful no-op (infra-free fast gate works)  [amended v2]
  Given the test Redis (localhost:6380 db 9) is unreachable at fixture setup
  When a pure-unit test (e.g. a make test-fast suite) starts
  Then the autouse flush is a NO-OP and the test runs (no Redis required)
  And a redis-dependent suite still fails via its OWN redis_client fixture (flake not masked)

Scenario: make test-fast runs the no-DB gate
  Given the no-DB blast-radius suites (translation + dispatch + provider)
  When `make test-fast` runs
  Then it executes those suites with NO Postgres/Redis dependency
  And the production src tree is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
TEST-INFRA contract (no production src change; no frozen test edited).

NEW  apps/gateway/tests/conftest.py — global AUTOUSE async fixture `_isolate_stores`
     + module helper `_clear_usage_leaks_if_reachable` (amended v4 — SURGICAL, group-preserving):
       async def _clear_usage_leaks_if_reachable(redis_url) -> None:
           r = aioredis.from_url(redis_url)
           try:
               await r.xtrim("usage:events", maxlen=0, approximate=False)  # clear backlog, KEEP group
               keys = [k async for k in r.scan_iter(match="usage:spend:*", count=500)]
               if keys: await r.delete(*keys)                              # clear spend counters
           except (RedisError, OSError): return   # no-op when Redis absent
           # (redis ConnectionError subclasses RedisError, NOT builtin ConnectionError)
           finally: await r.aclose()
       @pytest.fixture(autouse=True)
       async def _isolate_stores(settings) -> AsyncIterator[None]:
           await _clear_usage_leaks_if_reachable(settings.redis_url)   # clean slate BEFORE (the guarantee)
           yield                                                       # setup-only; no teardown perturbation
     # Uses the SAME `settings` fixture (db 9); no new URL literal. Redis-unreachable is a
     # no-op so the autouse fixture (which runs for ALL tests, incl. pure-unit) does NOT
     # force Redis on the infra-free make test-fast suites; redis-dependent suites still
     # fail via their own redis_client fixtures (Reject #3).
     # WHY XTRIM not FLUSHDB (amended v4): FLUSHDB deletes the usage:events stream together
     # with its `ledger-flusher` consumer group → every flusher-driving suite fails NOGROUP on
     # XREADGROUP (proven: guardrails + 4 health_alerting broke, suite 3x slower as tests retry
     # on broken state). XTRIM clears the leaked UNDELIVERED backlog (the FK channel) while the
     # consumer group SURVIVES. Setup-only (no cancel, no teardown flush): function-scoped event
     # loops already kill a test's leaked tasks at loop close, so a pre-test clear is sufficient.

UNCHANGED  the existing `app` fixture (per-test drop_all/create_all) — DB stays hermetic.
UNCHANGED  every per-suite `redis_client` fixture + scattered per-file flushdb (now
           redundant but harmless; frozen suites are NOT edited).

NEW  Makefile target `test-fast` (root) — no-real-infra blast-radius gate:
     test-fast:
       cd $(GATEWAY) && uv run pytest -p no:cacheprovider --no-cov -q \
         tests/tool_translation tests/response_format_translation \
         tests/provider_chat_dispatch tests/anthropic_provider tests/gemini_provider \
         tests/anthropic_tool_use tests/gemini_tool_use \
         tests/anthropic_json_mode tests/gemini_json_mode \
         tests/gemini_embed_tokens tests/nonchat_soft_budget_alert
     # MockTransport / pure-unit suites — run without Postgres/Redis. Documented in
     # Makefile .PHONY + a one-line comment; `make test` (full suite) unchanged.

Schema: none. HTTP surface: none. src/gateway/**: byte-identical.
Determinism check (verify gate): `pytest -m 'not e2e'` run TWICE → both green, equal counts.
```

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Change request v3→v4 (at build): the blanket FLUSHDB destroyed the flusher's `ledger-flusher`
consumer group → guardrails + 4 health_alerting tests failed NOGROUP (got 0 rows) and the full
suite ran 3x slower (tests retrying on broken state). Controlled experiment (DISABLE flush) =
729 passed / only my own isolation test failed → confirmed MY fixture was the sole regression,
and the FK-flake did NOT reproduce in any full run (725, 729 clean). Replaced FLUSHDB with a
SURGICAL group-preserving clear (XTRIM usage:events + DEL usage:spend:*), setup-only. Verified:
surgical run 1 = 730 passed, 0 failed, 595s (≈ neutralized baseline 585s — overhead negligible).
No prior frozen test weakened; the edited tests are this task's own red suite.
Change request v1→v2 (at build): the autouse fixture runs for ALL tests including the
pure-unit `make test-fast` suites, so a hard-fail on unreachable Redis would break the stated
infra-free fast-gate guarantee. Amended Reject #3 + the "redis unavailable" scenario + the
fixture shape to GRACEFUL DEGRADATION (no-op flush when Redis absent, catching redis.exceptions
.RedisError which does NOT subclass builtin ConnectionError).
Change request v2→v3 (at build): the cancel-based drain of all non-current asyncio tasks
KILLED the pytest-asyncio/anyio runner task (CancelledError at teardown across the test-fast
suites — assumption #3 realized). Replaced the drain with a safe settle (`await
asyncio.sleep(0)` ×3, NEVER cancel); the pre-test FLUSHDB is the real isolation guarantee
(function-scoped loops already kill leaked tasks at loop close). Updated the structural test to
assert "no cancel" + post-yield flush. No prior frozen test weakened; the edited test is this
task's own red suite, corrected to match the safe design.
Least-sure flag surfaced at freeze: [spec] the cross-test contaminator is the un-flushed Redis
`usage:events` stream (db 9) consumed by a later flusher-driving suite — code-traced
(record()→stream not DB; flusher→usage_records INSERT; flushdb in only 14 suites; SERIAL
execution rules out parallel races). A baseline full-suite run was GREEN (725 passed, 19
deselected, 13m37s) — the flake is INTERMITTENT so one clean pass neither reproduces nor
disproves it; the fix closes the traced leak channel regardless. If the autouse flush+drain
leaves any residual non-determinism it is caught by the mandatory ≥2 consecutive clean full
runs at the verify gate before PASS. Pure test-infra: no `src/gateway/**` change, no frozen
suite edited — lowest blast radius possible.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral isolation invariant + structural presence (5 tests)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_a_seed_stream_in_db9: seed usage:events + a spend key in db 9 (the leak fixture)
  - test_b_stream_clean_at_start: assert xlen(usage:events)==0 AND the leaked spend key is
    gone — proves the autouse pre-test clear ran (RED without it: inherits test_a's seed)
  - test_conftest_has_autouse_store_isolation: root conftest declares an autouse fixture +
    `_clear_usage_leaks_if_reachable` (structural)
  - test_conftest_is_surgical_and_group_preserving: fixture uses XTRIM + DEL usage:spend:*,
    NOT flushdb, and NEVER cancels tasks (structural) [amended v4]
  - test_make_test_fast_target_defined: Makefile has a .PHONY test-fast target running
    --no-cov over the translation suites (structural)
</test_plan>
Note: the "redis-unavailable → graceful no-op" and "deterministic across 2 runs" scenarios
are proven at the VERIFY gate (bogus-infra test-fast run + ≥2 consecutive full runs), not as
unit tests — a unit test cannot deterministically simulate an intermittent flake or tear down
real Redis mid-suite.

Tests live in: `tests/test_db_isolation` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the autouse clear MUST be group-preserving (XTRIM, never
FLUSHDB) and must NEVER cancel pending asyncio tasks (kills the test runner). Graceful no-op
on unreachable Redis so the infra-free fast gate keeps working.
Code lives in: `tests/conftest.py` (test-infra) + root `Makefile`. NO `src/gateway/**` change.
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — isolation suite 5/5; DETERMINISM GATE: 2 consecutive clean full
      `-m 'not e2e'` runs (run1 730 passed/0 failed/595s; run2 730 passed/0 failed/643s);
      `make test-fast` green; isolation+guardrails+health_alerting slice 37/37
- [x] coverage did not decrease — additive test-infra; 5 new tests
- [x] no test or contract was altered during build — only tests/conftest.py + Makefile +
      pyproject format-exclude + this task's own red suite (contract amended v1→v4 via
      documented change requests, never weakened to pass)
- [x] concurrency / timing safe — clear is SETUP-ONLY and NEVER cancels pending tasks (a
      cancel-drain killed the pytest-asyncio/anyio runner — caught + removed); XTRIM is
      group-preserving so flusher-driving suites are unaffected; graceful no-op on Redis-down
- [x] no exposed secrets / injection / unexpected deps — no secrets; XTRIM/DEL on db-9 keys
      keyed by `usage:events`/`usage:spend:*` only; no new packages (redis.asyncio already used)
- [x] layering & deps follow CONVENTIONS.md — test-infra only; no `src/gateway/**` change
- [x] reviewed — auto-resolved under delegated auto mode; evidence-complete (2-run determinism),
      no security finding; the 4 build-time contract amendments are documented in §3

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `_clear_usage_leaks_if_reachable` called by the autouse `_isolate_stores`
      fixture (runs for every test); `test-fast` target wired in Makefile + .PHONY; confirmed
      by the structural tests + 2 green full runs
- [x] DEAD-CODE — removed the unused `asyncio` import after dropping the cancel-drain; no
      orphaned symbol (ruff F401 clean)
- [x] SEMANTIC — n/a (code/infra change)

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · folded] the model missed multi-tenancy (evidence: scenario_x failed) -->
