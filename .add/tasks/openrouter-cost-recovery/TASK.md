# TASK: Recover OpenRouter authoritative cost via generation endpoint on disconnect

slug: openrouter-cost-recovery · created: 2026-06-22 · stage: production
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
- `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream.get_generation(generation_id) -> GenerationCost | None` — t6.1 client. `GenerationCost.total_cost: Decimal` is the authoritative upstream cost; `None` = not settled yet (404 / no total_cost). Auth via the request-scoped credential contextvar (`_auth_headers` → `get_provider_credential`).
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder` — write-behind recorder. `record()` DERIVES cost_usd from pricing (cannot post a signed/pre-computed cost). The `cost_basis='provider'` path bills `cost_usd = provider_cost*(1+markup)` when `usage["cost"]` is present. `_fetch_markup_pct(session, tenant_id)` resolves the tenant markup. Pushes one event to Redis Stream `usage:events` then INCRBYFLOAT advisory counters.
- `apps/gateway/src/gateway/usage/application/flusher.py:UsageLedgerFlusher._process_entry` — drains `usage:events`, assigns `record_id = stream_id_to_uuid(entry_id)` (no caller control today), `INSERT … ON CONFLICT (id) DO NOTHING`. The append-only ledger's only dedup is the PK.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — `provider_generation_id` (t6.2, the lookup key), `cost_usd: Numeric(14,8)` (SIGNED — a negative correction row is representable), `usage_source`, `cost_basis`, `provider_cost: Numeric(20,10)`.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:resolve_provider_credential(resolver, tenant_id, provider)` — resolves a tenant credential and SETS the contextvar, returns a Token (or None when SKIPPED); paired with `reset_provider_credential`. `BYOK_PROVIDERS` includes `openrouter`, so recovery resolves the key the same way the request path does.

Context (working folder): v30 t6 disconnect cost-recovery. t6.2 (provider-generation-id-capture, committed 6f3cb3d) already stamps `provider_generation_id` on the client-disconnect row. THIS task builds the out-of-band recovery CORE; inline wiring + the periodic sweep are separate follow-on tasks that both call this service.

Honors (patterns / conventions): write-behind via Redis Stream (recorder never raises into the proxy path); all money is Decimal via `Decimal(str(x))` (v27 floor); design-for-failure (bounded retry, deadline, idempotent — CLAUDE.md); credential is request/task-scoped contextvar set+reset around the upstream call.

Anchors the contract cites: `OpenRouterCostRecoveryService.recover`, `RecordingUsageRecorder.record_correction`, `UsageLedgerFlusher._process_entry` (explicit-id branch), `usage_records.provider_generation_id`, `usage_source='openrouter_recovered'`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenRouter authoritative cost recovery — close the disconnect $0/partial gap by topping up to the real upstream cost (partial-floor + delta top-up, chosen by Tin 2026-06-22).

Framings weighed: partial-floor + delta top-up (chosen — disconnect row keeps its partial estimate; recovery appends the signed delta to reach the authoritative total) · $0-placeholder + full-cost recovery (rejected — bills $0 if recovery never lands) · update-the-disconnect-row-in-place (rejected — breaks the append-only ledger contract).

Must:
<must>
  - Expose `OpenRouterCostRecoveryService.recover(*, tenant_id, key_id, model, provider_generation_id)` that fetches the authoritative cost and appends ONE correction row bringing total billed for that generation up to `total_cost*(1+markup)`.
  - Compute the top-up as a DELTA: `delta = total_cost*(1+markup_pct/100) - already_billed`, where `already_billed = Σ cost_usd` over ledger rows for that `provider_generation_id` EXCLUDING prior recovered rows (`usage_source != 'openrouter_recovered'`). A negative delta (partial over-estimated the real cost) is allowed — the correction row carries a negative `cost_usd`.
  - Wait (bounded) for the anchor disconnect row to be flushed to the ledger before computing `already_billed`, so the partial floor is never double-counted (the recorder write is async via Redis→flusher).
  - Poll `get_generation` with a bounded ready-deadline: a `None` result means the cost has not settled upstream yet — retry on an interval until it resolves or the deadline elapses.
  - Bill the correction row via `RecordingUsageRecorder.record_correction(...)` — a NEW path that posts a pre-computed SIGNED cost_usd with `cost_basis='provider'`, `provider_cost=total_cost`, `usage_source='openrouter_recovered'`, `provider_generation_id=<gid>`, and an EXPLICIT deterministic event id.
  - Make recovery IDEMPOTENT at the DB: the correction row id is deterministic from the gid (`uuid5`), and the flusher honors an explicit `id` field so a duplicate recovery (inline + sweep racing) is a `ON CONFLICT (id) DO NOTHING` no-op.
  - Resolve the tenant's OpenRouter credential out-of-band (no request context): set the credential contextvar around `get_generation`, reset it in a finally.
  - NEVER raise into the caller — recovery is best-effort; all failures are logged and swallowed (sweep is the backstop).
</must>
Reject:
<reject>
  - provider_generation_id is empty / None -> outcome `skipped:no_generation_id` (nothing to look up; no row written)
  - an `openrouter_recovered` row already exists for the gid -> outcome `skipped:already_recovered` (idempotent; no second row)
  - the anchor disconnect row never appears within the ledger-wait deadline -> outcome `deferred:anchor_not_flushed` (no row; the sweep retries later)
  - get_generation returns None until the ready-deadline elapses -> outcome `deferred:not_settled` (no row; the sweep retries later)
  - the tenant has no enabled OpenRouter credential -> outcome `deferred:no_credential` (no row; logged)
</reject>
After:
<after>
  - On success exactly ONE row exists with `provider_generation_id=<gid>` AND `usage_source='openrouter_recovered'`, `cost_basis='provider'`, `provider_cost=total_cost`, and `cost_usd = total_cost*(1+markup) - already_billed`.
  - Σ cost_usd over ALL rows for the gid == `total_cost*(1+markup)` (the authoritative customer bill).
  - Calling recover again for the same gid writes no new row (idempotent).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The anchor disconnect row is reliably FLUSHED within the ledger-wait deadline under inline timing — lowest confidence because the recorder→Redis→flusher hop is async and the flusher runs on a ~1s loop; if wrong: inline computes `already_billed` without the partial floor and over-bills by the partial. MITIGATION: bounded poll-until-present; on timeout we DEFER (write nothing) and let the sweep — which only ever runs after flush — do it. So a missed wait degrades to "sweep handles it", never to a wrong bill.
  - [ ] OpenRouter's `total_cost` is the pre-markup upstream cost (same basis as `usage["cost"]` in the existing provider path), so applying the tenant markup yields the customer bill — confirm: matches t6.1's GenerationCost + provider-cost-reconciliation semantics.
  - [ ] A negative `cost_usd` correction row flushes and sums correctly (column is `Numeric(14,8)`, signed) — confirm in the DB round-trip test.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: recover tops up a partial disconnect row to the authoritative total
  Given a flushed client-disconnect row for gid "gen-1" billed cost_usd=0.20 (partial floor)
  And OpenRouter reports total_cost=1.00 for "gen-1" and the tenant markup is 0%
  When recover(gid="gen-1") runs
  Then exactly one openrouter_recovered row is appended with cost_usd=0.80, provider_cost=1.00, cost_basis='provider'
  And the sum of cost_usd over all "gen-1" rows equals 1.00

Scenario: markup is applied to the authoritative cost
  Given a flushed disconnect row for "gen-2" billed cost_usd=0.00 and tenant markup 50%
  And OpenRouter reports total_cost=2.00 for "gen-2"
  When recover(gid="gen-2") runs
  Then the recovered row cost_usd equals 3.00 (2.00 * 1.50 - 0.00)
  And cost_basis is 'provider' and provider_cost is 2.00

Scenario: negative delta when the partial over-estimated the real cost
  Given a flushed disconnect row for "gen-3" billed cost_usd=1.50 and markup 0%
  And OpenRouter reports total_cost=1.00 for "gen-3"
  When recover(gid="gen-3") runs
  Then the recovered row cost_usd equals -0.50
  And the sum of cost_usd over all "gen-3" rows equals 1.00

Scenario: idempotent — a second recover writes no new row
  Given recover already appended an openrouter_recovered row for "gen-4"
  When recover(gid="gen-4") runs again
  Then no new row is written
  And the existing recovered row is unchanged

Scenario: empty generation id is skipped
  Given provider_generation_id is "" (the stream carried no id)
  When recover(gid="") runs
  Then the outcome is skipped:no_generation_id
  And no ledger event is emitted

Scenario: cost not settled yet is deferred
  Given OpenRouter returns None for "gen-5" until the ready-deadline elapses
  When recover(gid="gen-5") runs
  Then the outcome is deferred:not_settled
  And no recovered row is written

Scenario: anchor row not yet flushed is deferred
  Given no ledger row exists for "gen-6" within the ledger-wait deadline
  When recover(gid="gen-6") runs
  Then the outcome is deferred:anchor_not_flushed
  And no recovered row is written
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Service API (internal — no HTTP surface; called by inline wiring + sweep)
OpenRouterCostRecoveryService.recover(*, tenant_id: UUID, key_id: UUID, model: str,
                                      provider_generation_id: str) -> RecoveryOutcome
  RecoveryOutcome (frozen dataclass):
    status: Literal['recovered','skipped:no_generation_id','skipped:already_recovered',
                    'deferred:anchor_not_flushed','deferred:not_settled','deferred:no_credential','error']
    delta_usd: Decimal | None        # the signed correction billed (recovered only)
    provider_cost: Decimal | None    # OpenRouter total_cost (recovered only)
  Never raises — any unexpected error -> status='error', logged.

  Config (constructor kwargs, all bounded — design-for-failure):
    ready_deadline_s: float          # max wall time polling get_generation for a settled cost
    ready_poll_interval_s: float     # wait between get_generation polls on None
    anchor_wait_s: float             # max wall time waiting for the anchor row to flush
    anchor_poll_interval_s: float

# Recorder correction path (additive)
RecordingUsageRecorder.record_correction(*, event_id: UUID, tenant_id: UUID, key_id: UUID,
    model: str, cost_usd: Decimal, provider_cost: Decimal, provider_generation_id: str,
    usage_source: str = 'openrouter_recovered') -> None
  Posts ONE Redis event carrying an explicit `id` field + a pre-computed SIGNED cost_usd
  + cost_basis='provider'. INCRBYFLOAT the advisory spend counters by float(cost_usd)
  (negative allowed). Never raises (mirrors record()).

Schema: usage_records (append-only; READ Σ cost_usd / EXISTS by provider_generation_id;
  WRITE one correction row via the stream→flusher pipeline). No DDL — reuses t6.2's
  provider_generation_id column + the signed cost_usd column. The flusher gains an
  explicit-id branch: a non-empty `id` event field becomes the row PK (uuid5-deterministic),
  so ON CONFLICT (id) DO NOTHING is the idempotency guarantee. Absent `id` field = the
  existing stream_id_to_uuid(entry_id) path (byte-identical for every prior caller).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto)
Least-sure flag surfaced at freeze: [spec] the anchor-flush wait under inline timing — the recorder→Redis→flusher hop is async and the flusher loops ~1s, so inline recovery may reach the deadline before the partial disconnect row is queryable. Cost if wrong: inline computes already_billed=0 and over-bills by the partial. Mitigation contracted: on anchor_wait timeout → status='deferred:anchor_not_flushed' (write NOTHING); the sweep — which runs only after flush — does it. So a miss degrades to "sweep handles it", never a wrong bill.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new module + the two additive paths)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_recover_tops_up_partial_to_authoritative_total: seed flushed disconnect row (cost_usd=0.20, gid), fake get_generation→total_cost=1.00, markup 0% / act recover / assert one recovered row cost_usd=0.80 + Σ=1.00
  - test_markup_applied_to_authoritative_cost: markup 50%, total_cost=2.00, prior 0.00 / assert recovered cost_usd=3.00, provider_cost=2.00, cost_basis='provider'
  - test_negative_delta_when_partial_overestimated: prior 1.50, total_cost=1.00, markup 0% / assert recovered cost_usd=-0.50 + Σ=1.00 (signed column round-trips)
  - test_idempotent_second_recover_writes_no_row: run recover twice / assert exactly one recovered row (deterministic uuid5 id → ON CONFLICT DO NOTHING)
  - test_empty_generation_id_skipped: gid="" / assert status skipped:no_generation_id + no xadd
  - test_not_settled_is_deferred: get_generation→None until deadline / assert deferred:not_settled + no recovered row
  - test_anchor_not_flushed_is_deferred: no row for gid within anchor_wait / assert deferred:anchor_not_flushed + no recovered row
  - test_record_correction_emits_signed_cost_with_explicit_id: unit on recorder — assert event carries id field + negative cost_usd + cost_basis='provider'
  - test_flusher_honors_explicit_id: unit — event with id field → row PK == that id (not stream_id_to_uuid); absent id → unchanged path
</test_plan>

Tests live in: `apps/gateway/tests/openrouter_cost_recovery/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/usage/application/cost_recovery.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/usage/application/flusher.py` `apps/gateway/tests/openrouter_cost_recovery/`
Strategy (ordered batches): 1. recorder.record_correction (signed cost + explicit id event) 2. flusher explicit-id branch 3. cost_recovery.py service (anchor-wait → dedup → get_generation ready-poll → delta → record_correction) 4. tests green.
Safety rule (feature-specific): wait-for-anchor-flush BEFORE computing already_billed (never double-count the partial floor); deterministic uuid5 id so duplicate recovery is a DB no-op; never raise into the caller.
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

- [x] all tests pass — 11/11 in tests/openrouter_cost_recovery; full suite green (excl tests/edge live-stack)
- [x] coverage did not decrease — only additive new module + two additive paths, all covered
- [x] no test or contract was altered during build — contract FROZEN @ v1; tests strengthened then re-crossed tests→build (tamper-tripwire honoured)
- [x] the green was EARNED — adversarial refute-read (sonnet) run; verdict REFUTED on ONE real bug (advisory counter double-increment under concurrent double-fire) — NOT a test cheat. Closed by STRENGTHENING: SET NX idempotency guard keyed by event_id + new concurrent-double-fire test asserting the counter moves once. Re-refute of the math/idempotency/SQL paths: NOT-REFUTED.
- [x] concurrency / timing of the risky operation is safe — all deadline loops bounded (anchor_wait_s, ready_deadline_s); FakeClock proves non-spinning; DB write idempotent via deterministic uuid5 + ON CONFLICT; counter idempotent via SET NX
- [x] no exposed secrets, injection openings, or unexpected dependencies — credential via existing contextvar set/reset; all SQL parameterized (text() binds); no new packages
- [x] layering & dependencies follow CONVENTIONS.md — service in usage/application; reuses proxy/domain credential_context + proxy/infrastructure GenerationCost (no cycle); money is Decimal
- [x] a person reviewed and approved the change — Tin chose the billing model (partial-floor + delta) via AskUserQuestion; autonomy:auto drives the build

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] After a topup recovery, Σ cost_usd over all rows for the gid == total_cost*(1+markup) — confirmed by test_recover_tops_up (Σ==1.00) + test_markup_applied (recovered=3.00)
- [x] A negative delta is representable and sums correctly — confirmed by test_negative_delta (recovered=-0.50, Σ==1.00) round-tripped through Numeric(14,8)
- [x] A duplicate recovery writes exactly ONE ledger row AND moves the spend counter once — confirmed by test_idempotent (one row) + test_concurrent_double_fire (one row + counter≈0.40, not 0.80)
- [x] No row is written when skipped/deferred — confirmed by test_empty_generation_id (no xadd), test_not_settled, test_anchor_not_flushed (0 recovered rows)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — OpenRouterCostRecoveryService.recover calls recorder.record_correction + upstream.get_generation; record_correction emits the explicit-id event the flusher's new branch consumes; recovery_event_id used as the dedup key. (Inline + sweep CALLERS land in t6.2c/t6.3 — declared out of scope here; the service is exercised end-to-end by the suite.)
- [x] DEAD-CODE (code) — no orphaned symbol: every new symbol (RecoveryOutcome, recovery_event_id, record_correction, explicit-id branch) is referenced by tests and/or the service
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin (autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): recovered-row rate vs client_disconnect-row rate (recovery coverage); deferred:not_settled / deferred:anchor_not_flushed rates (tune deadlines); recovered delta sign distribution (frequent negatives ⇒ partial estimator over-shoots); 'error' outcome rate.

### Spec delta
- [SPEC · open] t6.2c openrouter-cost-recovery-wiring — fire recover() inline (fire-and-forget) from the use_cases disconnect handler when provider==openrouter + gen_id present; gate behind a default-OFF knob (evidence: this task built the CORE only; the hot streaming path is deliberately a separate scope)
- [SPEC · open] t6.3 openrouter-recovery-sweep — periodic backstop mirroring v29 ReconciliationDriftChecker: find client_disconnect rows with provider_generation_id and NO matching openrouter_recovered row, call recover() each; partial index + NOT-EXISTS dedup; default-OFF knobs (evidence: inline is best-effort; sweep is the reliable backstop — chosen "Both" by Tin)
- [SPEC · open] zero-delta recovery on a COMPLETE ('frame') anchor writes a $0 recovered row as the idempotency marker — confirm this is desired vs a distinct 'skipped:noop' outcome (evidence: refute-read Finding 3; harmless to Σ but writes a row)

### Competency deltas
- [ADD · open] the adversarial refute-read caught a real concurrency bug (advisory-counter double-increment) that ALL nine green tests missed — the DB dedups but INCRBYFLOAT does not; closed by a SET NX idempotency guard + a concurrent-double-fire test (evidence: refute verdict REFUTED → strengthen → NOT-REFUTED)
- [TDD · open] idempotency tests must exercise the CONCURRENT race (no flush between), not just the sequential path — a flush-between test only proves the read-side guard, never the write-side dedup (evidence: original test_idempotent passed while the counter still double-moved)
