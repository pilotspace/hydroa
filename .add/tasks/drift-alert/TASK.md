# TASK: Periodic drift-alert — fire one deduped alert when window drift exceeds the threshold

slug: drift-alert · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/usage/application/reconciliation.py:reconcile_window` (v29 t1, FROZEN @ v1) — the checker calls it OPERATOR-WIDE (`tenant_id=None`, legitimate — a server-side job with no per-request caller) to get the window's `ReconciliationSummary` (`unbilled_upstream_cost`, `unbilled_rows`, `drift`, `by_source`).
  - `apps/gateway/src/gateway/alerting/application/event_emitter.py:emit_system_event(session_factory, *, event_type, dedupe_key, payload)` — the EXACT write seam: INSERT into `alert_events` `(tenant_id NULL, key_id NULL, …) ON CONFLICT (dedupe_key) DO NOTHING`, swallows all exceptions. A drift alert is a SYSTEM event (operator-wide, tenant_id NULL) — this is the right emitter (NOT the tenant-scoped `alert_writer.persist_soft_budget_alert`).
  - `apps/gateway/src/gateway/alerting/application/health_checker.py:UpstreamHealthChecker` — the periodic-checker TEMPLATE to mirror: `__init__(session_factory, …)`, `check_once()` (one cycle → maybe emit), `run_forever(*, interval_seconds)` (`while True: check_once(); asyncio.sleep(interval)`, swallows loop errors). The new `ReconciliationDriftChecker` copies this shape.
  - `apps/gateway/src/gateway/alerting/application/dispatcher.py:AlertDispatcher` — the EXISTING delivery seam: `run_forever()` polls `alert_events WHERE delivered_at IS NULL` and POSTs to the webhook, sets `delivered_at=now()` on 2xx. The drift alert needs NO new delivery code — writing the row is enough; the running dispatcher delivers it.
  - `apps/gateway/src/gateway/usage/infrastructure/alert_events_orm.py:AlertEventRow` — the target table (tenant_id NULLABLE, no FK in the ORM, so a tenant_id-NULL system event inserts cleanly; `dedupe_key` UNIQUE; `delivered_at` NULL = undelivered).
  - `apps/gateway/src/gateway/core/config.py:Settings` (lines 115-118) — the alert/health knob block to extend: mirror `health_check_interval_seconds: int = 60  # …(0 = disabled)`. NEW: `reconciliation_drift_threshold` (GATEWAY_RECONCILIATION_DRIFT_THRESHOLD; 0 = disabled) + `reconciliation_check_interval_seconds` (GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS; 0 = disabled).
  - `apps/gateway/src/gateway/main.py` (lines 267-280) — the lifespan wiring to mirror: `if _settings.health_check_interval_seconds > 0: app.state.X_task = asyncio.create_task(checker.run_forever(interval_seconds=…))`. The drift checker is wired the SAME way, gated on both new knobs > 0, alongside the dispatcher/health_checker.
Context (working folder): the AUTOMATED leak monitor — the milestone's "can never go unnoticed" guarantee. v29 t1 built the metric, t2 exposed it on demand; this task adds a periodic server-side check that fires ONE deduped alert when the operator-wide window leak exceeds a configured threshold. Default-OFF (both knobs 0) → byte-identical to today; opt-in by the operator.
Honors (patterns / conventions): mirror the health-alerting background-checker pattern EXACTLY (check_once/run_forever, emit_system_event with ON CONFLICT dedup, swallow-all so a check error never crashes the loop or a request); reuse the EXISTING dispatcher for delivery (no new webhook code); default-OFF config knob (the v27/v28 "new behavior behind a default-off flag" convention); money stays `Decimal`. "accuracy is never an availability gate" extends — the checker only READS the ledger + writes an alert row, never touches the money path.
Anchors the contract cites: NEW `ReconciliationDriftChecker` (`check_once` / `run_forever`) · `reconcile_window(..., tenant_id=None)` operator-wide · `emit_system_event(..., event_type="reconciliation_drift", dedupe_key, payload)` · `alert_events` (tenant_id NULL system event, ON CONFLICT dedup, delivered by the existing dispatcher) · NEW Settings knobs `reconciliation_drift_threshold` + `reconciliation_check_interval_seconds` · the lifespan wiring in main.py.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `ReconciliationDriftChecker` — a periodic, operator-wide leak monitor that fires ONE deduped alert (via the existing alert_events + webhook seam) when a window's unbilled-upstream cost exceeds a configured threshold. The milestone's "an upstream charge with no matching user charge can never go unnoticed" guarantee.
Framings weighed: a background checker (`check_once`/`run_forever`) that emits a SYSTEM event via the existing `emit_system_event` + `AlertDispatcher` seam (chosen — mirrors `UpstreamHealthChecker` exactly, reuses delivery wholesale, default-OFF, the operator-wide read has no per-request caller so tenant_id=None is legitimate) · inline the check in the ledger flusher loop (rejected — couples leak-monitoring to the money-write hot path and ties it to the flush cadence) · an external cron/scheduled job (rejected — there is no scheduler infra; the in-process `run_forever` background-task pattern is the established one, started in the lifespan).
Must:
<must>
  - `ReconciliationDriftChecker.check_once()`: reconcile the OPERATOR-WIDE (`tenant_id=None`) window via `reconcile_window`, and if the trigger metric EXCEEDS the threshold, emit exactly one system event via `emit_system_event(event_type="reconciliation_drift", dedupe_key=<per-window>, payload=<the numbers>)`.
  - TRIGGER: fire when `summary.unbilled_upstream_cost > threshold` — the markup-free "upstream charged us, user billed $0" leak (the literal question this milestone answers). `threshold` comes from `reconciliation_drift_threshold` (absolute USD, `Decimal`).
  - DEDUP: at most ONE alert per monitored window — the window is the CURRENT UTC calendar day `[today 00:00, tomorrow 00:00)`; `dedupe_key = f"reconciliation_drift:{YYYYMMDD}"`; `ON CONFLICT (dedupe_key) DO NOTHING` makes a second crossing the same day a no-op.
  - OPERATOR-WIDE: the reconcile spans ALL tenants (`tenant_id=None`) — this is the global leak monitor, and the emitted event is a system event (`tenant_id NULL`), never tenant-attributed.
  - `run_forever(*, interval_seconds)`: loop `check_once()` every interval, SWALLOWING all errors (a check failure logs + continues; it NEVER crashes the loop or any request path).
  - DEFAULT-OFF: the checker is only started when BOTH `reconciliation_drift_threshold > 0` AND `reconciliation_check_interval_seconds > 0` (wired in the lifespan exactly like `health_check_interval_seconds`); both 0 → never started → behavior byte-identical to today.
  - READ + alert-write only: the checker reads `usage_records` (via the aggregate) and writes one `alert_events` row; it NEVER mutates the ledger or the money path. Delivery is the EXISTING dispatcher's job (no new webhook code).
</must>
Reject:
<reject>
  - (no user input — this is a background job, not a request handler; there is no 4xx surface.)
  - below threshold (`unbilled_upstream_cost ≤ threshold`) -> NO alert is emitted (the not-firing case is a first-class requirement, not an error).
  - a check-cycle exception (DB down, aggregate raises) -> SWALLOWED + logged; `run_forever` continues to the next interval and never raises.
</reject>
After:
<after>
  - When the day's operator-wide `unbilled_upstream_cost` first crosses the threshold, exactly ONE `alert_events` row exists for that day (`tenant_id NULL`, `event_type="reconciliation_drift"`, `dedupe_key="reconciliation_drift:{YYYYMMDD}"`, `delivered_at NULL`, payload carrying window + unbilled_upstream_cost + unbilled_rows + drift + threshold). The running `AlertDispatcher` delivers it to the webhook. The `usage_records` ledger is byte-for-byte unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the TRIGGER metric + threshold semantics: I trigger on `unbilled_upstream_cost > threshold` (markup-FREE, absolute USD) because it is the unambiguous "upstream charged us, we billed the user $0" leak — exactly the milestone's question. The alternatives are (a) the markup-INCLUSIVE `drift` (noisier: healthy drift is negative, so "drift > threshold" measures margin erosion — a different signal that needs a sign/ίmeaning decision) and (b) a PERCENTAGE threshold (unbilled / provider_cost_total). Both numbers are already in the summary, so if wrong this is a one-line change to the trigger expression + the knob's meaning. [→ the freeze decision for Tin]
  - [ ] the monitored WINDOW = the current UTC calendar day with per-day dedup (≤1 alert/day) — confirm vs a rolling 24h window (a rolling window needs a coarser dedupe bucket anyway; the calendar day gives the simplest deterministic `dedupe_key` and matches the soft-budget `YYYYMM` precedent).
  - [ ] BOTH knobs default 0 (disabled) so production is unchanged until the operator opts in (sets a threshold + an interval) — confirm the default-OFF posture.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: DA1 crossing the threshold fires one alert
  Given operator-wide unbilled-upstream rows in today's window summing to 5.00 and a checker with threshold 1.00
  When check_once() runs
  Then exactly one alert_events row exists with event_type="reconciliation_drift", tenant_id IS NULL,
       key_id IS NULL, dedupe_key="reconciliation_drift:{today YYYYMMDD}", delivered_at IS NULL

Scenario: DA2 below the threshold fires nothing (reject)
  Given operator-wide unbilled-upstream cost of 0.50 and a checker with threshold 1.00
  When check_once() runs
  Then NO alert_events row of event_type="reconciliation_drift" exists
  And the usage_records ledger is unchanged

Scenario: DA3 a second crossing the same day is deduped to one alert
  Given the threshold is already crossed for today
  When check_once() runs twice
  Then still exactly ONE reconciliation_drift alert_events row exists for today (ON CONFLICT dedup)

Scenario: DA4 the monitor is operator-wide (aggregates across tenants)
  Given tenant A unbilled 0.60 and tenant B unbilled 0.60 (each alone below threshold 1.00) in today's window
  When check_once() runs
  Then the alert fires (operator-wide sum 1.20 > 1.00) — the leak is caught even though no single tenant crosses

Scenario: DA5 the alert payload carries the leak numbers and window
  Given the threshold is crossed by unbilled-upstream cost 5.00 over 3 rows
  When check_once() runs
  Then the alert_events.payload includes unbilled_upstream_cost="5.00", unbilled_rows=3, the window bounds, and threshold="1.00"

Scenario: DA6 a check-cycle error is swallowed, never raised
  Given a checker whose reconcile/session raises on this cycle
  When check_once() runs (and run_forever's loop body invokes it)
  Then it does not raise; the error is logged and the loop continues to the next interval
  And no alert_events row is written for the failed cycle

Scenario: DA7 default-OFF — the checker is not started unless both knobs are set
  Given default Settings (reconciliation_drift_threshold=0 and reconciliation_check_interval_seconds=0)
  When the app starts (lifespan)
  Then app.state.drift_checker_task is None (no background drift checker is created — behavior unchanged)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Periodic background monitor — NEW apps/gateway/src/gateway/usage/application/drift_checker.py:

  class ReconciliationDriftChecker:
      def __init__(self, *, session_factory: async_sessionmaker[AsyncSession],
                   threshold: Decimal) -> None
      async def check_once(self) -> None
      async def run_forever(self, *, interval_seconds: float) -> None

Behavior:
  - check_once():
      window_from = today 00:00 UTC; window_to = tomorrow 00:00 UTC  (current UTC calendar day)
      summary = await reconcile_window(session, window_from, window_to, tenant_id=None)  # OPERATOR-WIDE
      if summary.unbilled_upstream_cost > threshold:
          await emit_system_event(session_factory,
              event_type="reconciliation_drift",
              dedupe_key=f"reconciliation_drift:{window_from:%Y%m%d}",
              payload={ "window_from": <iso>, "window_to": <iso>,
                        "unbilled_upstream_cost": str(Decimal), "unbilled_rows": int,
                        "drift": str(Decimal), "threshold": str(Decimal) })
      # else: no-op (no alert). NEVER raises — wraps the read in try/except, logs + swallows.
  - run_forever(*, interval_seconds): while True: (swallow-all) check_once(); await asyncio.sleep(interval_seconds).
  - DEDUP: dedupe_key is per-UTC-day → emit_system_event's ON CONFLICT (dedupe_key) DO NOTHING → ≤ 1 alert/day.
  - OPERATOR-WIDE: tenant_id=None; the emitted row is a SYSTEM event (tenant_id NULL, key_id NULL).

Config — Settings (core/config.py), mirroring the health knobs:
  reconciliation_drift_threshold: Decimal = Decimal("0")    # GATEWAY_RECONCILIATION_DRIFT_THRESHOLD (0 = disabled)
  reconciliation_check_interval_seconds: int = 0            # GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS (0 = disabled)

Wiring — main.py lifespan (mirror the UpstreamHealthChecker block):
  app.state.drift_checker_task = None
  if settings.reconciliation_drift_threshold > 0 and settings.reconciliation_check_interval_seconds > 0:
      checker = ReconciliationDriftChecker(session_factory=_sessionmaker,
                                           threshold=settings.reconciliation_drift_threshold)
      app.state.drift_checker = checker
      app.state.drift_checker_task = asyncio.create_task(
          checker.run_forever(interval_seconds=float(settings.reconciliation_check_interval_seconds)))
  # + cancel app.state.drift_checker_task on shutdown alongside the health_checker/dispatcher tasks.

Schema: WRITES one alert_events row via emit_system_event (event_type="reconciliation_drift",
  tenant_id NULL, dedupe_key UNIQUE, delivered_at NULL → delivered by the EXISTING AlertDispatcher).
  READS usage_records via reconcile_window. NO migration (alert_events + the dispatcher already exist) ·
  NO new table/column · NO new dependency.
Invariants: operator-wide read · trigger = unbilled_upstream_cost > threshold · ≤1 alert per UTC day (dedup) ·
  default-OFF (both knobs 0 → checker never started) · swallow-all (check_once + run_forever never raise) ·
  ledger byte-unchanged · delivery reuses the existing dispatcher (no new webhook code).
```

Status: FROZEN @ v1 — approved by Tin (2026-06-18, via AskUserQuestion: "Freeze as drafted").
Least-sure flag surfaced at freeze: [spec] the TRIGGER metric. The alert fires on
`unbilled_upstream_cost > threshold` (markup-FREE, absolute USD) — the literal "upstream charged
us, we billed the user $0" leak this milestone exists to catch. The alternatives weighed and
declined at freeze: the markup-INCLUSIVE `drift` (noisier, measures margin erosion, needs a sign
convention) and a PERCENTAGE threshold (scales with volume). Both `unbilled_upstream_cost` and
`drift` are already in the summary, so re-pointing the trigger is a one-line change. Cost if wrong:
trivial. Window = current UTC calendar day, per-day dedup; both knobs default 0 (opt-in).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of `ReconciliationDriftChecker` (`check_once`/`run_forever`) + the default-OFF wiring (whole-suite ≥80%).
Plan (one test per scenario DA1–DA7; integration via the real-DB `db_session`/`app` fixtures — seed `usage_records` rows with `created_at = now(UTC)` so they fall in today's window, construct the checker with `session_factory = app.state.sessionmaker`, call `check_once()`, assert `alert_events`):
<test_plan>
  - test_da1_crossing_fires_one: seed operator-wide unbilled rows summing 5.00 (provider_cost>0 ∧ cost_usd=0) / checker threshold 1.00 / check_once / assert one alert_events row event_type="reconciliation_drift", tenant_id IS NULL, key_id IS NULL, dedupe_key=f"reconciliation_drift:{today}", delivered_at IS NULL.
  - test_da2_below_threshold_silent: seed unbilled 0.50 / threshold 1.00 / check_once / assert NO reconciliation_drift row; assert usage_records COUNT unchanged.
  - test_da3_same_day_deduped: seed unbilled 5.00 / threshold 1.00 / check_once TWICE / assert exactly ONE reconciliation_drift row (ON CONFLICT).
  - test_da4_operator_wide: tenant A unbilled 0.60 + tenant B unbilled 0.60 (each < 1.00) / threshold 1.00 / check_once / assert the alert fires (sum 1.20 > 1.00).
  - test_da5_payload_numbers: seed unbilled 5.00 over 3 rows / threshold 1.00 / check_once / parse alert_events.payload → unbilled_upstream_cost=="5.00", unbilled_rows==3, threshold=="1.00", window_from/window_to present.
  - test_da6_check_error_swallowed: checker with a session_factory that raises on use / check_once / assert it does NOT raise and writes no row (and run_forever's body would likewise survive).
  - test_da7_default_off_not_wired: with default Settings (both knobs 0) the app fixture's lifespan ran / assert app.state.drift_checker_task is None.
</test_plan>

Tests live in: `apps/gateway/tests/drift_alert/` · MUST run red (missing `drift_checker.py` / `ReconciliationDriftChecker`) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/drift_checker.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/drift_alert/`
Strategy (ordered batches): 1. add the two Settings knobs (`reconciliation_drift_threshold: Decimal = Decimal("0")`, `reconciliation_check_interval_seconds: int = 0`) to `core/config.py`. 2. write `ReconciliationDriftChecker` in `usage/application/drift_checker.py` (compute today's UTC window → operator-wide `reconcile_window` → if `unbilled_upstream_cost > threshold` emit the deduped system event; `check_once` swallows; `run_forever` loops+sleeps+swallows). 3. wire the lifespan in `main.py` (gate on both knobs > 0; `app.state.drift_checker_task`; cancel on shutdown beside health_checker). 4. red DA1–DA7 → green.
Safety rule (feature-specific): DEFAULT-OFF (never started unless both knobs > 0) · SWALLOW-ALL (check_once + run_forever never raise — a monitor must not crash the app) · OPERATOR-WIDE read only (no ledger write) · `Decimal` threshold (no float) · delivery reuses the existing dispatcher.
Code lives in: `apps/gateway/src/gateway/usage/application/` (+ config.py knobs + main.py wiring)
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

- [x] all tests pass — full suite **1214 passed** (`pytest --ignore=tests/edge`, edge = live-stack only); focused `tests/drift_alert` **10/10** green (DA1–DA10).
- [x] coverage did not decrease — `drift_checker.py` (incl. the new `should_start_drift_checker` predicate) + the `main.py` lifespan wiring are net-additive and exercised by DA1–DA10 (DA7/DA10 cover the default-OFF guard).
- [x] no test or contract was altered to pass — contract **FROZEN @ v1 UNCHANGED**. Tests were *strengthened* (added DA8 at-threshold boundary, DA9 `run_forever`, DA10 wiring-predicate truth-table; added the `drift`-field assertion to DA5) in response to the adversarial refute-read — the OPPOSITE of weakening — and re-snapshotted via the sanctioned tests→build re-cross. No assertion was loosened.
- [x] the green was EARNED — adversarial refute-read (sonnet, XML brief) run; verdict BLOCK 0.87 on **4 MAJOR test-COVERAGE gaps** (it confirmed the IMPLEMENTATION sound on every §3 clause — "no false-negative money leak is possible given correct configuration"). All 4 closed: F2 drift-field → DA5 assert; F4 boundary → DA8; F3 run_forever → DA9; F1 default-OFF wiring → `should_start_drift_checker` + DA10 truth-table. F10 (tighten to exact string) **REFUTED** — money is `NUMERIC(20,10)`/(14,8) so `SUM` carries scale ("5.0000000000"); Decimal-equality is correct, exact-string would be a false assert. F5/F6 → §7 deltas; F8 (run_forever inner guard) intentional template-mirror of `UpstreamHealthChecker`; F9 midnight-fragility accepted (astronomically rare). Re-ran 10/10 green.
- [x] concurrency / timing safe — mirrors `UpstreamHealthChecker` exactly: `check_once`/`run_forever` swallow-all (never raise), cancelled on shutdown via `contextlib.suppress(CancelledError)`, READ-only on the ledger; no shared mutable state; per-UTC-day dedup is enforced by the DB `ON CONFLICT (dedupe_key)` (race-safe, not in-process).
- [x] no exposed secrets, injection openings, or unexpected dependencies — all SQL via bound params (`reconcile_window` + `emit_system_event` use `text()` with `:params`); no secret read/written; NO new dependency (alert_events + dispatcher pre-exist; no migration).
- [x] layering & dependencies — application-layer checker composes the usage aggregate (`reconcile_window`) + the alerting emitter (`emit_system_event`); wired in the `main.py` lifespan beside the dispatcher/health_checker, gated on the pure `should_start_drift_checker` predicate.
- [x] reviewed — **auto-gated under `autonomy: auto`** (auto-resolved): no security finding, no concurrency/architecture residue, autonomy not lowered → no human-escalating trigger. The refute-read served as the independent adversarial review; all findings resolved.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `should_start_drift_checker` imported (main.py:86) + used in the start guard (main.py:289); `ReconciliationDriftChecker` imported (85), instantiated (293), task created (298–299); `drift_checker.check_once()` final-drain at shutdown (334); task cancelled (320) + pre-initialised in `create_app` (384). Confirmed by grep + `uv run pyright src/gateway` → 0 errors.
- [x] DEAD-CODE (code) — every new symbol is referenced (predicate, checker, both knobs); no orphan. (`run_forever`'s inner `except` is a deliberate defence-in-depth mirror of the health checker, not new dead code.)

### GATE RECORD
Outcome: PASS  (auto-resolved under `autonomy: auto` — full suite 1214 green, contract frozen, refute-read BLOCK fully remediated, no security/concurrency/architecture residue)
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: ADD auto-gate (adversarial refute-read, sonnet) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-UTC-day `reconciliation_drift` alert rate (DA1) · the `unbilled_upstream_cost` trend in the payload (DA5) · that the existing `AlertDispatcher` delivers the system event (delivered_at flips) · the check-cycle error rate stays a swallowed log line, never a crash (DA6).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · open] reject a non-finite / ≤0 drift threshold at startup — `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD=inf` passes the `>0` start-guard but `unbilled > inf` can never fire, so the monitor runs silently useless (evidence: refute-read F6; design-for-failure — a nonsense config should fail loud, not silent-disable).
  - [SPEC · open] add a `cost_basis='provider'` guard to the `unbilled_upstream_cost` FILTER in `reconcile_window` — today it counts any `provider_cost>0 ∧ cost_usd=0` row, so a future `catalog` row with a provider cost would read as a leak (evidence: refute-read F5; latent only — the recorder sets `provider_cost=NULL` for catalog rows. Belongs to `reconciliation-aggregate`, frozen → change-request).
  - [SPEC · open] stamp `provider_cost` on client-disconnect / `GeneratorExit` mid-stream rows (carried from v27 t4 silent-$0) — once stamped, THIS monitor surfaces them as unbilled-upstream automatically (evidence: v27 t4 follow-up; the drift monitor is the consumer that makes fixing it observable).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
  - [TDD · folded] the adversarial refute-read caught 4 §3-coverage gaps a green suite missed (drift-field unasserted · no at-threshold boundary · `run_forever` never invoked · default-OFF wiring unexercised) — the sanctioned response is STRENGTHEN-then-re-cross, never weaken (evidence: t3 refute-read BLOCK → DA5/DA8/DA9/DA10). [folded foundation-version 27]
  - [TDD · folded] verify a reviewer's "tighten to exact string" against the real column scale before applying — `SUM` over `NUMERIC(20,10)` yields `"5.0000000000"`, so Decimal-equality is RIGHT and exact-string would be a false assert (evidence: refute-read F10 refuted by the migration scale). [folded foundation-version 27]
  - [ADD · folded] extract a lifespan start-guard into a pure predicate (`should_start_drift_checker`) so the default-OFF invariant is unit-testable WITHOUT driving the flaky full lifespan (the "fixtures never cancel background tasks" foundation rule) — a reusable wiring-test pattern for the other checkers (evidence: F1 closed via the DA10 truth-table, not a lifespan test). [folded foundation-version 27]
