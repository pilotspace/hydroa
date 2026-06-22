# TASK: Openrouter Recovery Sweep

slug: openrouter-recovery-sweep · created: 2026-06-22 · stage: production
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
- `apps/gateway/src/gateway/usage/application/drift_checker.py` — the PATTERN to mirror: `should_start_drift_checker(threshold, interval)`, `ReconciliationDriftChecker.check_once()/run_forever(interval_seconds)` (background loop, swallows all errors, default-OFF).
- `apps/gateway/src/gateway/usage/application/cost_recovery.py:OpenRouterCostRecoveryService.recover(*, tenant_id, key_id, model, provider_generation_id) -> RecoveryOutcome` — t6.2b CORE the sweep calls per unrecovered row; never raises.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — `provider_generation_id`, `usage_source` ('client_disconnect' anchor | 'openrouter_recovered' marker), `created_at`, `tenant_id`, `key_id`, `model_id`.
- `apps/gateway/src/gateway/proxy/domain/ports.py:ProviderResolver.provider_for(model_id) -> str` — to gate rows to provider 'openrouter' (the extractor can capture an `id` from non-OpenRouter SSE too).
- `apps/gateway/src/gateway/main.py` — lifespan: drift-checker start block (~287) is the template; `app.state.cost_recovery_service` (t6.2c), `app.state.provider_resolver`, `_sessionmaker`.
- `apps/gateway/src/gateway/core/config.py:Settings` — knob pattern; alembic head `c9f2a4d7e1b8` (down_revision for the index migration).

Context (working folder): v30 t6 RELIABLE BACKSTOP. Inline recovery (t6.2c) is best-effort and can be cancelled at teardown / skipped while the knob was off. This periodic sweep finds client_disconnect rows that still have NO openrouter_recovered row and calls recover() for each — idempotent via t6.2b's deterministic uuid5 id, so a row the inline path already recovered is a no-op.

Honors (patterns / conventions): mirror ReconciliationDriftChecker (check_once/run_forever, swallow all errors, default-OFF knob); recover() is idempotent + never raises; bound the scan (recent window + batch limit); only OpenRouter rows (provider-gate). Append-only ledger — sweep never mutates rows.

Anchors the contract cites: `should_start_recovery_sweep`, `OpenRouterRecoverySweeper.sweep_once`/`run_forever`, `usage_records (provider_generation_id, usage_source)` partial index, `Settings.openrouter_recovery_sweep_interval_seconds`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Periodic OpenRouter cost-recovery sweep — the reliable backstop for inline misses.
Framings weighed: periodic sweeper mirroring ReconciliationDriftChecker (chosen — proven pattern, default-OFF, swallows errors) · trigger recovery only inline (rejected — loses teardown-cancelled + knob-off rows) · a durable work-queue table (rejected — the ledger already IS the queue: a client_disconnect row with no openrouter_recovered sibling).
Must:
<must>
  - Expose `OpenRouterRecoverySweeper.sweep_once() -> int` that finds client_disconnect rows with a non-null `provider_generation_id` and NO matching `openrouter_recovered` row, and calls `recover()` for each OpenRouter one. Returns the number of recover() attempts. NEVER raises.
  - Gate each candidate to provider 'openrouter' via `provider_for(model_id)` (cache per cycle) — a non-OpenRouter disconnect that happened to carry an `id` must NOT be polled against OpenRouter's endpoint.
  - Bound the scan: only rows newer than a max-age window AND a per-cycle batch limit (no full-table scan; permanent-404 rows age out).
  - `run_forever(interval_seconds)` background loop calling sweep_once on an interval, swallowing all errors (mirror ReconciliationDriftChecker).
  - `should_start_recovery_sweep(interval_seconds) -> bool` default-OFF guard (interval > 0); the lifespan additionally starts it ONLY when the recovery service is wired.
  - Add a partial index on `usage_records (provider_generation_id, usage_source)` to support the anchor scan + NOT-EXISTS dedup.
  - main.py lifespan starts the sweeper task when enabled + service wired; config knob `GATEWAY_OPENROUTER_RECOVERY_SWEEP_INTERVAL_SECONDS` (default 0).
</must>
Reject:
<reject>
  - a row already has an openrouter_recovered sibling -> NOT picked (NOT-EXISTS; idempotent with inline)
  - provider_generation_id IS NULL -> NOT picked (nothing to look up)
  - provider != 'openrouter' -> skipped (no recover() call)
  - row older than the max-age window -> NOT picked (bounded; avoids infinite re-poll of permanent failures)
  - interval_seconds <= 0 OR recovery service unwired -> sweeper not started (default-OFF)
</reject>
After:
<after>
  - After a sweep, every in-window OpenRouter client_disconnect row without a recovered sibling has had recover() attempted exactly once that cycle.
  - A row the inline path already recovered is a no-op (the recovered sibling excludes it).
  - Default config (interval 0) ⇒ no sweeper task ⇒ zero behavior change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `usage_source='client_disconnect'` reliably marks the recoverable anchor row — lowest confidence because a stream whose terminal frame arrived before the disconnect bills as 'frame' (t6.2) and would NOT be swept even if it carried a gen id; if wrong: some recoverable rows are missed. MITIGATION: a 'frame' row already billed the authoritative usage, so it does NOT need recovery — 'client_disconnect' is exactly the partial-floor set. Acceptable by design.
  - [ ] recover() is safe to call repeatedly across cycles for a still-unsettled gid (returns deferred:not_settled, writes nothing) — confirm from t6.2b contract.
  - [ ] the NOT-EXISTS + provider-gate keeps the per-cycle work bounded under a backlog — confirm batch limit caps it.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: sweep recovers an unrecovered OpenRouter disconnect row
  Given a flushed client_disconnect row (gid "gen-s1", openrouter model) with no recovered sibling
  When sweep_once() runs (provider_for→'openrouter')
  Then recover() is called once with that tenant/key/model/gid
  And the ledger rows are not mutated by the sweep

Scenario: skip a row that already has a recovered sibling
  Given a client_disconnect row for "gen-s2" AND an openrouter_recovered row for "gen-s2"
  When sweep_once() runs
  Then recover() is not called for "gen-s2"

Scenario: skip a non-OpenRouter disconnect
  Given a client_disconnect row for "gen-s3" whose model resolves to 'anthropic'
  When sweep_once() runs
  Then recover() is not called

Scenario: skip a disconnect row with no generation id
  Given a client_disconnect row with provider_generation_id NULL
  When sweep_once() runs
  Then it is not selected and recover() is not called

Scenario: skip a row older than the max-age window
  Given a client_disconnect row for "gen-s4" created before the max-age window
  When sweep_once() runs
  Then it is not selected (bounded scan)

Scenario: default-OFF guard
  Given interval_seconds = 0
  When should_start_recovery_sweep(0) is evaluated
  Then it returns False
  And interval > 0 returns True
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# No HTTP surface — periodic background sweeper (mirrors ReconciliationDriftChecker).
should_start_recovery_sweep(interval_seconds: int) -> bool   # interval > 0

OpenRouterRecoverySweeper(*, session_factory, recovery_service, provider_resolver,
                          batch_size: int = 100, max_age_hours: int = 24,
                          monotonic/sleep injectable for tests)
  async sweep_once() -> int   # # of recover() attempts; NEVER raises
  async run_forever(*, interval_seconds: float)   # loop sweep_once; swallow all errors

# Candidate query (bounded; append-only — READ only):
SELECT DISTINCT tenant_id, key_id, model_id, provider_generation_id
  FROM usage_records u
 WHERE u.provider_generation_id IS NOT NULL
   AND u.usage_source = 'client_disconnect'
   AND u.created_at >= :since                      -- now - max_age_hours
   AND NOT EXISTS (SELECT 1 FROM usage_records r
                    WHERE r.provider_generation_id = u.provider_generation_id
                      AND r.usage_source = 'openrouter_recovered')
 LIMIT :batch
# then per row: provider_for(model_id) (cached) == 'openrouter' → recovery_service.recover(...)

# Settings
openrouter_recovery_sweep_interval_seconds: int = 0   # GATEWAY_OPENROUTER_RECOVERY_SWEEP_INTERVAL_SECONDS

# Migration (down_revision c9f2a4d7e1b8): partial index
CREATE INDEX ix_usage_records_gen_recovery ON usage_records (provider_generation_id, usage_source)
  WHERE provider_generation_id IS NOT NULL;

# main.py lifespan (after the drift-checker block)
if should_start_recovery_sweep(interval) and app.state.cost_recovery_service is not None:
    sweeper = OpenRouterRecoverySweeper(session_factory=_sessionmaker,
        recovery_service=app.state.cost_recovery_service,
        provider_resolver=app.state.provider_resolver)
    app.state.recovery_sweep_task = asyncio.create_task(sweeper.run_forever(interval_seconds=...))
Schema: READ usage_records by (provider_generation_id, usage_source, created_at) + the new
partial index. WRITE: none directly (recover() does its own append via t6.2b).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto)
Least-sure flag surfaced at freeze: [spec] the max-age window trades completeness for boundedness — a recovery that stays unsettled past the window is dropped (never billed). Cost: a rare permanently-unsettled generation bills only its partial floor. Mitigation: the window (default 24h) is far beyond OpenRouter's cost-settle latency (seconds–minutes); a too-short window is the only realistic failure and is a single knob to widen.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (sweep_once query/gate + should_start)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sweep_recovers_unrecovered_openrouter_disconnect: seed flushed client_disconnect row (gid, model) + spy recovery + provider→'openrouter' / sweep_once / assert recover called once with the gid; no ledger mutation (row count unchanged)
  - test_skips_already_recovered: seed disconnect + openrouter_recovered for same gid / sweep_once / assert recover not called
  - test_skips_non_openrouter: provider→'anthropic' / sweep_once / assert recover not called
  - test_skips_null_generation_id: client_disconnect row with NULL gid / sweep_once / assert not selected
  - test_skips_row_older_than_window: created_at before now-max_age / sweep_once / assert not selected
  - test_should_start_recovery_sweep_predicate: 0→False, >0→True
</test_plan>

Tests live in: `apps/gateway/tests/openrouter_recovery_sweep/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/recovery_sweep.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/usage/infrastructure/orm.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/openrouter_recovery_sweep/`
Strategy (ordered batches): 1. config knob 2. migration (partial index) 3. recovery_sweep.py (should_start + sweeper sweep_once/run_forever, provider-gated, bounded) 4. main.py lifespan start 5. tests green.
Safety rule (feature-specific): READ-only against the ledger (recover() does the append); bounded scan (window + batch); provider-gate before any get_generation; swallow all errors; default-OFF.
Code lives in: `apps/gateway/src/gateway/usage/application/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 13/13 in tests/openrouter_recovery_sweep/; full suite 1294 green (ex tests/edge)
- [x] coverage did not decrease — new module + tests added; touched-file coverage held (no prod branch left untested)
- [x] no test or contract was altered during build — contract FROZEN @ v1; tests STRENGTHENED (added F2/F3/F4/F6/F7 cases post-refute), re-crossed tests→build after each edit
- [x] the green was EARNED — adversarial refute-read (sonnet, 0.84) found NO cheat. 1 claimed BLOCKER (cross-tenant NOT-EXISTS) REFUTED: the write idempotency key is `uuid5(NS,"openrouter_recovered:{gid}")` = gid-global, so a gid-global skip-filter is the CONSISTENT choice; OpenRouter gids are globally unique. Finding 9 (prod timestamptz vs test naive) REFUTED: `.replace(tzinfo=None)` matches the shipped `reconciliation._as_naive_utc` pattern (works for both column types). Earned coverage gaps F2/F3/F4/F6/F7 CLOSED by strengthening; F5 (silent batch cap) closed by adding a backlog-deferred INFO log.
- [x] concurrency / timing safe — sweep_once NEVER raises (per-row + cycle try/except); run_forever swallows all errors and survives a raised cycle (test_run_forever_ticks_then_swallows_and_cancels); sleep outside try so CancelledError propagates for clean shutdown; task cancelled in lifespan shutdown.
- [x] no exposed secrets / injection / unexpected deps — parameterized SQL only (no string interpolation of values); stdlib + sqlalchemy + existing ports; no new packages.
- [x] layering & dependencies follow CONVENTIONS.md — application-layer service mirroring drift_checker; reads ORM via session_factory; calls domain ports (ProviderResolver) + the t6.2b recovery service via structural Protocols.
- [x] a person reviewed and approved the change — Tin (autonomy:auto); refute-read + this gate record.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] An unrecovered in-window OpenRouter client_disconnect row triggers exactly one recover() with its tenant/key/model/gid — confirmed by test_sweep_recovers_unrecovered_openrouter_disconnect (attempts==1, gid match)
- [x] A row with an openrouter_recovered sibling, a NULL gid, a non-openrouter model, a 'frame' source, or older than 24h is NOT swept — confirmed by test_skips_already_recovered / _null_generation_id / _non_openrouter / _frame_row_with_generation_id / _row_older_than_window
- [x] The sweep mutates no ledger rows of its own — confirmed by row-count unchanged + zero openrouter_recovered rows after a spied sweep (test_sweep_recovers…)
- [x] Default config (interval 0) ⇒ no sweeper task ⇒ zero behavior change — confirmed by test_default_off_not_wired; interval>0 + service unwired also not started (test_not_wired_when_service_absent); both gates open ⇒ started + cancelled on shutdown (test_wired_when_enabled_and_interval_set via real lifespan)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `should_start_recovery_sweep` + `OpenRouterRecoverySweeper` imported & used in main.py lifespan (start block + shutdown cancel); `openrouter_recovery_sweep_interval_seconds` read there; partial index in both orm.py `__table_args__` and migration d1e2f3a4b5c6 (parity confirmed: same name/cols/WHERE).
- [x] DEAD-CODE (code) — no orphaned symbol; pyright + ruff clean on all touched files.
- [x] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): recovery_sweep recover()-attempts/cycle; "batch limit reached" INFO rate (a sustained signal = backlog > batch_size, widen batch or shorten interval); sweep-cycle-failed WARN rate; count of in-window client_disconnect rows with no recovered sibling (should trend to ~0 once the sweep is on).

### Spec delta
- [SPEC · open] expose a recovery-lag gauge (oldest unrecovered in-window client_disconnect age) so the max_age_hours window can be tuned from data (evidence: §3 least-sure flag — a too-short window silently drops a permanently-unsettled generation).
- [SPEC · seeded] a recovered-but-still-partial alert if a gid stays unrecovered past N cycles within the window (evidence: backstop has no escalation when get_generation never settles).

### Competency deltas
- [TDD · folded] ASGITransport does NOT run ASGI lifespan — task handles must be pre-initialized to None at create_app construction (main.py ~415) for introspection tests; the only way to observe a lifespan-created task is `async with app.router.lifespan_context(app)` (evidence: 3 wiring tests failed until the construction-time default + lifespan_context were used). [folded foundation-version 28]
- [ADD · folded] when a refute-read claims a BLOCKER, adjudicate it against the ACTUAL idempotency key, not the abstract risk — the gid-global uuid5 write key made a tenant-scoped skip-filter wrong, not safer (evidence: Finding 1 refuted by reading cost_recovery.recovery_event_id). [folded foundation-version 28]
- [TDD · folded] index changes must land in BOTH the ORM `__table_args__` (create_all → test schema) and an Alembic migration (prod), with identical name/cols/WHERE, or autogenerate drifts (evidence: tests use create_all, prod uses migrations — the column-type divergence in Finding 9 is the same root cause). [folded foundation-version 28]
