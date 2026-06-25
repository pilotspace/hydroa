# TASK: Tenant data retention & purge controls

slug: data-retention-controls · created: 2026-06-25 · stage: production
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
  - PURGEABLE time-series tables: `usage/infrastructure/orm.py` (`usage_records` tenant_id+created_at — the big billing/usage history) · `usage/infrastructure/alert_events_orm.py` (`alert_events`) · `audit/infrastructure/audit_events_orm.py` (`audit_events` — COMPLIANCE-sensitive; see §1 ⚠).
  - PATTERN to follow EXACTLY: the default-OFF periodic background sweeper — `usage/application/recovery_sweep.py:OpenRouterRecoverySweeper`+`should_start_recovery_sweep` and `usage/application/drift_checker.py:ReconciliationDriftChecker`+`should_start_drift_checker` (a `run_forever()` task gated on env knobs > 0, wired in `main.py` lifespan via `asyncio.create_task`, cancelled on shutdown).
  - `core/config.py:Settings` — add retention knobs (default 0 = OFF, like reconciliation_drift_*).
  - `audit/application/audit_writer.py:record_audit` — REUSE to audit the purge itself ("data.purge").
  - `main.py` lifespan — start/stop the retention task (the should_start predicate).
  - Alembic migration only if an index is needed for the age-scan (current head `e3f5a7c9b1d2`).
Context (working folder): PROJECT.md (tenant-scoping; design-for-failure — bounded batches, never block); CONVENTIONS.md (DDD application service + periodic-task idiom); gateway test DB :5433 UP; pytest ONE process.
Honors: DEFAULT-OFF (no deletion unless explicitly enabled, like every other sweeper); bounded/batched deletes; tenant-scoped age filter; the purge is itself an audited admin/system action; NEVER delete below a safety floor (esp. audit_events).
Anchors the contract cites: NEW `RetentionSweeper` (run_forever/sweep_once) · `should_start_retention_sweep` predicate · the retention Settings knobs · the per-table retention windows · the "data.purge" audit event · reuse of record_audit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant data retention & purge controls (bounded periodic deletion of aged time-series data, default-OFF)
Framings weighed: DEFAULT-OFF periodic RetentionSweeper, per-table retention windows via env knobs, batched age-scan DELETE, purge self-audited (chosen) · DB-native partitioning/pg_cron (rejected: infra change, not app-level) · on-demand admin purge endpoint (deferred: a manual destructive endpoint is higher-risk; periodic policy first)
Must:
<must>
  - A NEW `RetentionSweeper.run_forever()` / `sweep_once()` periodically DELETEs rows older than a per-table retention window, in BOUNDED batches, tenant-agnostic age-scan (every row carries created_at + tenant_id).
  - DEFAULT-OFF: no deletion happens unless an operator explicitly sets a retention window > 0 (mirrors should_start_drift_checker / recovery_sweep). A `should_start_retention_sweep` predicate gates startup.
  - PER-TABLE windows (independent knobs): usage_records · alert_events · audit_events — each 0 = never purge that table. (The exact policy + the audit floor = the §3 security decision.)
  - SAFETY FLOOR for audit_events: audit retention has a HARD MINIMUM (cannot be set below the floor) so the compliance trail can't be trivially erased; the immutability RULEs block UPDATE/DELETE for app code, so the purge runs via a controlled path (see §3).
  - The purge is AUDITED: each sweep that deletes rows emits a "data.purge" audit event (table, age-cutoff, rows_deleted) via record_audit — fail-open.
  - Design-for-failure: bounded batch size, exceptions logged + swallowed (a purge failure never crashes the app); the sweeper is cancellable on shutdown.
  - Tenant-scoping preserved: deletes filter purely by created_at age across all tenants (the retention policy is operator-wide), never crosses into other data.
Reject:
<reject>
  - Enabling purge without an explicit window (knob unset/0) -> NO deletion ("retention_default_off")
  - Setting audit_events retention below the safety floor -> rejected/clamped ("audit_retention_below_floor")
  - An unbounded single DELETE of an entire table -> not allowed; batched ("retention_unbounded_delete")
  - Deleting rows NEWER than the cutoff -> never ("retention_over_delete")
</reject>
After:
<after>
  - With windows set, rows older than each table's window are deleted in bounded batches on the interval; rows within the window remain; each purge run is audited; default config deletes nothing.
  - App boots/shuts the sweeper cleanly; full gateway suite green; no regression to billing/reconciliation reads within the retention window.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ THE DESTRUCTIVE PURGE POLICY is a SECURITY decision Tin must make: (a) WHICH tables are purgeable + their default
    windows; (b) the audit_events SAFETY FLOOR (or exclude audit entirely from purge); (c) whether to ship purge ENABLED
    with defaults or DEFAULT-OFF requiring explicit opt-in. Lowest confidence: audit retention — purging the audit trail
    can violate compliance; usage_records purge can break historical billing/reconciliation. If wrong: irreversible data
    loss or a compliance violation. MITIGATION: present the policy; freeze ONLY on Tin's explicit approval; default-OFF.
  - [ ] Operator-wide age-scan (not per-tenant API) is the right granularity for v1 — confirm at freeze.
  - [ ] usage_records purge interaction with reconciliation/billing reads — assume reads only touch within-window data; confirm.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Sweep deletes only rows older than the window
  Given usage_records with rows aged 400d and 100d and a 365d window
  When sweep_once runs
  Then the 400d row is deleted and the 100d row remains ("retention_over_delete" guard holds)

Scenario: Default-OFF when interval is 0
  Given GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS=0
  When startup is evaluated
  Then should_start_retention_sweep is False and no deletion happens ("retention_default_off")

Scenario: Per-table window of 0 skips that table
  Given GATEWAY_RETENTION_ALERT_EVENTS_DAYS=0 and a 200d-old alert row
  When sweep_once runs
  Then no alert_events rows are deleted

Scenario: Audit retention floor is enforced
  Given GATEWAY_RETENTION_AUDIT_EVENTS_DAYS=30 and GATEWAY_RETENTION_AUDIT_FLOOR_DAYS=365
  When the effective audit window is computed
  Then it is clamped to 365d, not 30d ("audit_retention_below_floor")

Scenario: Audit purge works only through the controlled bypass
  Given the immutable_guard trigger and an audit row older than the effective window
  When the sweeper purges audit with SET LOCAL app.audit_purge='on'
  Then the aged audit row is deleted
  And a plain DELETE (no GUC) against audit_events still RAISEs, and any UPDATE still RAISEs

Scenario: Deletes are bounded by batch size
  Given 2500 aged usage rows and batch size 1000
  When sweep_once runs
  Then it deletes in batches of <=1000 until none remain (no single unbounded DELETE — "retention_unbounded_delete")

Scenario: Each purge run is audited
  Given a sweep that deletes >0 rows
  When it completes a table
  Then a data.purge audit event records {table, cutoff, rows_deleted}

Scenario: Sweep failure is fail-open
  Given the DELETE raises
  When run_forever is executing
  Then the exception is logged and swallowed; the app/task stays alive; the next interval retries
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SETTINGS knobs (core/config.py — all default 0 = OFF):
  GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS   (0 ⇒ sweeper never starts)
  GATEWAY_RETENTION_USAGE_RECORDS_DAYS       (0 ⇒ usage_records never purged)
  GATEWAY_RETENTION_ALERT_EVENTS_DAYS        (0 ⇒ alert_events never purged)
  GATEWAY_RETENTION_BATCH_SIZE               (default 1000; bounds each DELETE)
  [audit_events: see decision A — likely NO knob in v1]

COMPONENT  usage/application/retention_sweep.py:
  RetentionSweeper(session_factory, settings, audit_writer)
    sweep_once() -> dict[table, rows_deleted]   # for each enabled table: DELETE ... WHERE created_at < now()-window LIMIT batch, loop until < batch
    run_forever(interval)                        # sleep/loop; exceptions logged+swallowed; cancellable
  should_start_retention_sweep(settings) -> bool # interval>0 AND at least one window>0
  Wired in main.py lifespan (asyncio.create_task; cancel on shutdown) — mirrors ReconciliationDriftChecker.
  Each run that deletes >0 rows emits record_audit(action="data.purge", target_type="retention",
    metadata={table, cutoff_iso, rows_deleted}) — system actor, fail-open.

DECISION A — PURGE SCOPE (Tin):
  [default/recommended] usage_records + alert_events ONLY; audit_events EXCLUDED — already protected by its
     DELETE-blocking immutability RULE; purging it is a deliberate out-of-band DBA op, not app policy.
   | include audit_events too — requires a controlled rule-bypass + a HARD retention FLOOR (e.g. ≥365d); weakens the
     immutability guarantee just shipped.
DECISION B — DEFAULT STATE (Tin):
  [default/recommended] DEFAULT-OFF — ship all knobs 0; an operator opts in per table (mirrors every other sweeper).
   | ship with default windows (e.g. usage 365d / alert 90d) — purge active out of the box (irreversible deletes by default).

Rejections: retention_default_off · audit_retention_below_floor · retention_unbounded_delete · retention_over_delete.
Schema: NO new table; DELETEs on usage_records/alert_events by created_at age in bounded batches. An index on created_at
  may be added (migration on head e3f5a7c9b1d2) if the age-scan needs it; else none.
Least-sure flag surfaced at freeze: [contract] decisions A+B — this is IRREVERSIBLE tenant-data deletion. Cost if wrong:
  permanent data loss or a compliance breach (esp. audit). This is why the freeze needs Tin's explicit approval; the build
  defaults to OFF + audit-excluded unless Tin widens it.
```

Status: FROZEN @ v1 — approved by Tin 2026-06-25 (security HARD-STOP, DESTRUCTIVE).
DECISIONS: A=INCLUDE audit_events WITH A HARD FLOOR · B=SHIP WITH DEFAULT WINDOWS (active by default).
FROZEN PARAMETERS (destructive — Tin-endorsed defaults):
  GATEWAY_RETENTION_CHECK_INTERVAL_SECONDS default 86400 (daily; 0 ⇒ OFF)
  GATEWAY_RETENTION_USAGE_RECORDS_DAYS      default 365   (0 ⇒ skip)
  GATEWAY_RETENTION_ALERT_EVENTS_DAYS       default 90    (0 ⇒ skip)
  GATEWAY_RETENTION_AUDIT_EVENTS_DAYS       default 730   (0 ⇒ skip); EFFECTIVE = max(knob, FLOOR) when knob>0
  GATEWAY_RETENTION_AUDIT_FLOOR_DAYS        default 365   (HARD floor; audit window can never be smaller)
  GATEWAY_RETENTION_BATCH_SIZE              default 1000  (bounds each DELETE)
AUDIT IMMUTABILITY MECHANISM CHANGE (change-request to audit-log-store, security-relevant):
  NEW migration (head e3f5a7c9b1d2) DROPs RULEs audit_events_no_update/_no_delete and REPLACES with a trigger
  `audit_events_immutable_guard` BEFORE UPDATE OR DELETE: UPDATE → always RAISE; DELETE → RAISE UNLESS
  current_setting('app.audit_purge', true) = 'on'. The RetentionSweeper's audit purge does `SET LOCAL app.audit_purge='on'`
  inside its txn ONLY (transaction-scoped; no other path can delete audit rows). Ordinary app code still cannot mutate/delete.
  This PRESERVES audit-log-store's intent (immutable to the app) while enabling the floored, audited retention purge.
Note: audit_events purge itself emits a data.purge audit event (recorded BEFORE the SET LOCAL bypass closes, or in a separate
  txn) so the purge is always traceable.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: retention module fully covered; full gateway suite green (no regression).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_deletes_only_aged_rows: 400d + 100d usage rows, 365d window -> only 400d deleted
  - test_default_off_when_interval_zero: interval=0 -> should_start_retention_sweep False; sweep_once no-op
  - test_window_zero_skips_table: alert window=0, 200d alert row -> not deleted
  - test_audit_floor_enforced: audit knob=30, floor=365 -> effective=365 (clamp); a 100d audit row survives, a 800d one is purgeable
  - test_audit_purge_via_bypass_only: with SET LOCAL app.audit_purge='on' an aged audit row deletes; a plain DELETE still RAISEs; any UPDATE still RAISEs (trigger test, migration applied)
  - test_batched_delete: 2500 aged rows, batch 1000 -> deleted in bounded batches, all removed, no single unbounded DELETE
  - test_purge_is_audited: a delete-bearing sweep -> data.purge audit row {table, cutoff, rows_deleted}
  - test_sweep_fail_open: DELETE raises -> run_forever logs+swallows, task survives, next tick retries
</test_plan>

Tests live in: `apps/gateway/tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/` `apps/gateway/src/gateway/usage/infrastructure/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/` `apps/gateway/tests/`
Strategy (ordered batches):
  1. RED tests `apps/gateway/tests/test_retention_sweep.py` (8 per §4) incl the trigger-bypass migration test.
  2. Settings knobs in core/config.py (defaults per §3; all gated, audit floor clamp helper).
  3. NEW `usage/application/retention_sweep.py`: `RetentionSweeper` (sweep_once batched per enabled table; effective_audit_window=max(knob,floor); audit purge wraps DELETE in `SET LOCAL app.audit_purge='on'`) + `run_forever` (fail-open) + `should_start_retention_sweep`.
  4. Migration on head e3f5a7c9b1d2: DROP the two audit RULEs, CREATE function+trigger `audit_events_immutable_guard` (UPDATE→RAISE always; DELETE→RAISE unless `current_setting('app.audit_purge',true)='on'`). Add a created_at index on usage_records/alert_events only if needed for the scan.
  5. Wire RetentionSweeper into main.py lifespan (create_task + cancel on shutdown), behind should_start_retention_sweep.
  6. Green: full gateway suite (docker DB up), ruff, pyright.
Safety rule (feature-specific): DESTRUCTIVE — deletes are IRREVERSIBLE. NEVER delete rows newer than now()-window. Audit window is ALWAYS max(knob, floor). Audit deletion ONLY via the transaction-scoped `SET LOCAL app.audit_purge='on'` path; ordinary UPDATE/DELETE on audit_events must still RAISE. Every delete bounded by batch size. Sweep failure is fail-open (logged, swallowed, task survives). The purge is itself audited.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the FROZEN contract; do NOT edit the shipped audit migration e3f5a7c9b1d2 (write a NEW migration that supersedes the RULEs); do NOT create tmp/*.txt scratch files (commit with inline -m); allow-list packages only.
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

- [x] all tests pass — INDEPENDENTLY re-ran retention+audit 23/23; subagent full suite 1607 passed
- [x] coverage did not decrease — +8 retention tests; audit immutability test STRENGTHENED (silent no-op → explicit RAISE)
- [x] no test weakened / contract unchanged — the audit immutability test change is STRICTLY STRONGER (asserts RAISE "audit_immutable_violation" vs previously silent no-op); FROZEN data-retention contract unchanged. The audit-log-store mechanism change (RULE→trigger) was Tin-approved as part of decision A and recorded as a change-request (see §7).
- [x] the green was EARNED — orchestrator READ the trigger fn SQL, the SET LOCAL-in-session.begin() bypass, effective_audit_window, and the DELETE WHERE created_at<cutoff; ran retention+audit subsets standalone. No vacuous asserts.
- [x] concurrency / timing — `SET LOCAL app.audit_purge='on'` is TRANSACTION-scoped (inside `async with session.begin()`, expires at commit) — cannot leak to other sessions/transactions; sweeper runs in its own task, cancellable; each table batch isolated
- [x] no exposed secrets / injection — parameterized SQL (:cutoff/:batch bound); no secrets; data.purge metadata = {table, cutoff, rows_deleted} only
- [x] layering & dependencies — RetentionSweeper in usage/application (mirrors recovery_sweep/drift_checker); reuses record_audit; no new dependency
- [x] a person reviewed & approved — TIN approved the destructive policy (A=include-audit-with-floor · B=ship-with-defaults, 2026-06-25) + the RULE→trigger mechanism change; orchestrator independent security review

### Build expectations — confirmed at gate
- [x] Deletes only aged rows — DELETE … WHERE created_at < cutoff LIMIT batch; test: 400d gone, 100d stays
- [x] Default-OFF gating — should_start_retention_sweep False when interval=0 OR all windows=0
- [x] Audit FLOOR inviolable — effective_audit_window = max(knob, floor); knob 30 + floor 365 → 365 (clamps UP); test confirms
- [x] Audit purge ONLY via bypass — trigger: UPDATE always RAISE; DELETE RAISE unless current_setting('app.audit_purge',true)='on'; sweeper sets it inside its own txn; plain DELETE + any UPDATE still RAISE (test)
- [x] Batched — DELETE … id IN (SELECT … LIMIT batch) loop; 2500 rows / batch 1000 → bounded; no unbounded DELETE
- [x] Purge audited — data.purge event {table, cutoff, rows_deleted} per delete-bearing table
- [x] Fail-open — per-table + run_forever catch/log/swallow; task survives; migration drops RULEs + creates trigger + adds created_at indexes (in BOTH migration and ORM __table_args__)

### Deep checks
- [x] WIRING — RetentionSweeper + should_start_retention_sweep wired in main.py lifespan (create_task + cancel); knobs in Settings; trigger live via migration f2a4c6e8b0d3 (parent e3f5a7c9b1d2)
- [x] DEAD-CODE — no orphan; ruff clean; pyright 8 errors = pre-existing baseline in main.py/config.py, 0 NEW
- [x] SEMANTIC — DESTRUCTIVE-BY-DEFAULT is intentional (Tin decision B): on deploy with defaults the sweeper deletes usage>365d, alert>90d, audit>730d daily. The audit floor (365d) caps how aggressively audit can ever be purged. RULE→trigger is a security-relevant change to a shipped task → change-request recorded.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (destructive policy A/B + mechanism change) + orchestrator independent review (trigger SQL, txn-scoped bypass, floor clamp, never-delete-newer, retention+audit subsets re-run) · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
- [SPEC · CHANGE-REQUEST · applied] audit-log-store immutability mechanism RULE(DO INSTEAD NOTHING) → TRIGGER(RAISE; DELETE allowed only under SET LOCAL app.audit_purge='on'). Tin-approved as decision A of this task; strengthens the guard (loud RAISE) AND enables the floored audit purge. Migration f2a4c6e8b0d3.
- [SPEC · open] ship-with-default-windows means destructive purge is ACTIVE on deploy — surface this loudly in release notes + ops docs so an operator can set knobs to 0 before first boot if they want to retain everything.
- [SPEC · open] consider an on-demand admin purge endpoint + a dry-run/report mode (rows-that-WOULD-delete) before a destructive change (evidence: v1 is policy-only, no preview).
- [SPEC · open] per-tenant retention overrides (v1 is operator-wide age-scan).

### Competency deltas
- [ADD · open] a later task can legitimately CHANGE-REQUEST a shipped task's frozen mechanism when a new requirement (audit purge) collides with it — surface the collision at the freeze, get explicit approval, implement via a NEW migration (never edit the shipped one), and prove the observable security property is preserved/strengthened (evidence: RULE→trigger here).
- [DDD · open] "retention/purge" is an operator-wide lifecycle policy distinct from tenant-scoped CRUD — modelled as a periodic application sweeper, not an API (evidence: on-demand endpoint deferred).
