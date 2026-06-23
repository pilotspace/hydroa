# TASK: Recoverable provider_cost on residual non-OpenRouter client-disconnect rows

slug: disconnect-provider-cost · created: 2026-06-23 · stage: production
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

RE-GROUND (v33, 2026-06-23): v30 ALREADY ships the OpenRouter disconnect chain — `_wrapped`'s `except (GeneratorExit, asyncio.CancelledError)` fires ONE flagged `client_disconnect` row, captures the SSE generation id, deterministically `gen.aclose()`s the upstream, and (knob-on) schedules `OpenRouterCostRecoveryService.recover` + the periodic sweep backstop. That chain is OUT OF SCOPE — this task covers the RESIDUAL gap only: a `client_disconnect` row from a NON-OpenRouter provider (or any disconnect with no generation id) gets NO recovery, so it stays `cost_basis='catalog'`, `provider_cost=NULL`, `cost_usd≈$0` → invisible to the drift monitor (a silent $0 upstream charge). Decision (Tin, 2026-06-23 via AskUserQuestion): **stamp + audit** — partial-token disconnects get an ESTIMATED provider_cost so drift surfaces them; zero-token (no estimate possible) disconnects are surfaced by a READ-ONLY audit.
Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:1485 _wrapped` — the disconnect handler (line ~1532 fires `_fire_record_with_raw(..., usage_source=disconnect_source, provider_generation_id=disconnect_gen_id)`). Compute non-recoverability here: `disconnect_estimate = (disconnect_source == "client_disconnect") and not disconnect_gen_id` (v33-amended at verify — gate on gen-id ABSENCE, not provider; the recovery chain keys only on gen-id and _stream_provider is None when the knob is off → a provider-gate double-counts. See §6).
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:300 _fire_record_with_raw` — add `disconnect_estimate: bool = False` → forward via `UsageRecordExtras` (the typed-extras seam; `_dispatch_record` filters by `supported_extras`).
  - `apps/gateway/src/gateway/proxy/domain/ports.py:29 UsageRecordExtras` — add `disconnect_estimate: bool` field + doc.
  - `apps/gateway/src/gateway/usage/application/recorder.py:50 supported_extras` (+ `record`/`_record_internal`:73/129) — accept `disconnect_estimate: bool = False`; after costing, when it is True AND `provider_cost is None` AND `cost_basis=='catalog'` AND `cost_usd>0`: set `provider_cost = cost_usd / (1 + markup_pct/100)` (catalog cost is markup-applied; base ≈ the upstream's charge), `cost_usd = _ZERO`, `cost_basis='provider'` → the row now reads as unbilled-upstream. (init `markup_pct` at top so it's bound on every path.)
  - `apps/gateway/src/gateway/usage/application/reconciliation.py` — NEW frozen `UnrecoveredDisconnect(id, tenant_id, model_id, created_at)` + NEW `audit_unrecovered_disconnects(session, window_from=None, window_to=None) -> tuple[...]`: READ-ONLY scan of `usage_source='client_disconnect' AND provider_cost IS NULL AND cost_usd = 0` with NO `openrouter_recovered` sibling (mirrors `audit_cost_basis_breaches` from the sibling v33 task).
Context (working folder):
  - `apps/gateway/tests/stream_disconnect_billing/` (v28) + `tests/` v30 t6 recovery suites — the existing disconnect-billing coverage + SSE-collect fixtures to mirror.
Honors (patterns / conventions):
  - Typed-extras seam ([[typed-extras-seam]]): no `inspect.signature` dispatch — declare the new kwarg in `UsageRecordExtras` + `supported_extras`; v1-Protocol fakes (no attribute) silently drop it.
  - recorder.record MUST NOT raise (swallow + WARN); fire-and-forget hygiene on any scheduled task.
  - reconciliation.py: READ-ONLY SELECT-only; money via `_money` (no float); `_as_naive_utc` window bind; frozen dataclasses.
  - No silent $0 (milestone shared decision); compose with — never alter — the v30 OpenRouter chain.
Anchors the contract cites:
  - `UsageRecordExtras.disconnect_estimate` · `RecordingUsageRecorder.record(disconnect_estimate=...)` · `audit_unrecovered_disconnects` + `UnrecoveredDisconnect` · the residual-disconnect stamp rule (catalog+positive estimate → provider-basis unbilled).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Residual client-disconnect cost visibility — stamp an estimated provider_cost on non-recoverable partial disconnects, and audit the zero-estimate residue.
Framings weighed: stamp-estimate + audit (chosen — Tin via AskUserQuestion 2026-06-23: partial-token disconnects surface in the existing drift filter, zero-token ones are surfaced by a read-only audit; no fabricated number on rows we have no basis to estimate) · audit-only (rejected — leaves partial disconnects billed/visible as $0 even though we DO have a token estimate) · stamp-only with cost_basis='catalog' (rejected — would be flagged by the sibling task's `audit_cost_basis_breaches`, and catalog rows are invisible to the drift unbilled filter).
Must:
<must>
  - On a NON-RECOVERABLE client-disconnect row (usage_source='client_disconnect' AND NO generation-id — verify-amended from the provider-gated form; see §0/§6) where the catalog estimate is POSITIVE, the recorder stamps `provider_cost` = the markup-stripped catalog cost, sets `cost_usd = 0` and `cost_basis = 'provider'` → the row reads as unbilled-upstream and the drift monitor surfaces it.
  - A disconnect WITH a generation-id is UNCHANGED — the v30 recovery chain (inline + sweep) keys on the generation-id and still owns it (no stamp, so a recovery correction never double-counts).
  - A complete-frame disconnect (usage_source='frame') is UNCHANGED — only true partial/no-frame disconnects are stamped.
  - `audit_unrecovered_disconnects(session, window?)` returns one `UnrecoveredDisconnect(id, tenant_id, model_id, created_at)` per `usage_source='client_disconnect'` row with `provider_cost IS NULL AND cost_usd = 0` and NO `openrouter_recovered` sibling — the zero-estimate residue (READ-ONLY, ordered by created_at, optional half-open window).
</must>
Reject:
<reject>
  - (no new error path) — recorder.record never raises (swallow+WARN); the audit is a read; an inverted window raises ValueError like the sibling reconcilers.
</reject>
After:
<after>
  - No non-recoverable disconnect that cost upstream money is billed $0 AND invisible: a positive estimate shows as unbilled-upstream drift; a zero estimate is enumerable via the audit.
  - The v30 OpenRouter recovery chain is behavior-identical (recoverable rows untouched).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Stamping the markup-stripped catalog cost as `provider_cost` labels an ESTIMATE as provider-basis — lowest confidence because a true upstream cost may differ from list price; if wrong: the drift figure for these rows is an approximation, not exact. Mitigation: it's strictly better than silent $0/NULL (the whole point), it's confined to non-recoverable rows (no real provider cost is ever available for them), and `usage_source='client_disconnect'` keeps the estimate distinguishable from a real `frame` provider cost.
  - [x] `markup_pct` is bound whenever `cost_usd>0` (assigned on the `not cached` path); init it at the top so pyright + the cached path are safe. Confirmed from recorder.py:162-166.
  - [x] zeroing `cost_usd` does not double-bill: we never charged the user for the dropped response, and the advisory spend counter only increments when `cost_usd>0`. Confirmed recorder.py:328.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: non-recoverable partial disconnect is stamped as unbilled-upstream
  Given a non-OpenRouter client_disconnect with partial usage (a positive catalog estimate)
  When the recorder records it with disconnect_estimate=True
  Then the row has cost_basis='provider', provider_cost > 0 (≈ markup-stripped catalog cost), cost_usd = 0
  And reconcile_window counts it in unbilled_upstream_cost

Scenario: recoverable OpenRouter disconnect is left untouched
  Given an OpenRouter client_disconnect with a generation id
  When the disconnect handler records it
  Then disconnect_estimate is False → the row stays cost_basis='catalog' (provider_cost NULL)
  And the v30 recovery chain still owns the correction

Scenario: zero-estimate disconnect is surfaced by the audit
  Given a client_disconnect row with provider_cost NULL and cost_usd = 0 and no recovered sibling
  When audit_unrecovered_disconnects(session) is called
  Then it returns one UnrecoveredDisconnect for that row (id, tenant_id, model_id, created_at)
  And a recovered (openrouter_recovered sibling) row is NOT returned

Scenario: complete-frame disconnect is not stamped
  Given a client disconnect where a complete usage frame arrived (usage_source='frame')
  When the recorder records it
  Then it bills normally (no forced cost_usd=0, no provider-basis stamp)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Typed-extras seam (additive)
UsageRecordExtras.disconnect_estimate: bool        # default-absent → False
RecordingUsageRecorder.supported_extras += {"disconnect_estimate"}
RecordingUsageRecorder.record(..., disconnect_estimate: bool = False) -> None   # must not raise

# Recorder stamp rule (post-costing, per-row)
when disconnect_estimate AND provider_cost is None AND cost_basis == 'catalog' AND cost_usd > 0:
    provider_cost := cost_usd / (1 + markup_pct/100)   # Decimal; ≈ upstream list charge
    cost_usd      := 0
    cost_basis    := 'provider'
# else: row unchanged (recoverable / complete-frame / zero-estimate all fall through)

# Disconnect handler (use_cases._wrapped)
# v33-amended at verify (refute-read BLOCKER): gate on gen-id ABSENCE, not provider. The v30
# recovery chain (inline + sweep) keys ONLY on provider_generation_id, and when the recovery knob
# is off _stream_provider is None — so a provider-gate would stamp OpenRouter+gen-id rows the sweep
# later double-counts. A no-gen-id row is never a recovery candidate → double-count-proof.
disconnect_estimate = (disconnect_source == 'client_disconnect') and not disconnect_gen_id

# Audit primitive (reconciliation.py)
audit_unrecovered_disconnects(session, window_from=None, window_to=None)
    -> tuple[UnrecoveredDisconnect(id: UUID, tenant_id: UUID, model_id: str, created_at: datetime), ...]
  SELECT id, tenant_id, model_id, created_at FROM usage_records
   WHERE usage_source='client_disconnect' AND provider_cost IS NULL AND cost_usd = 0
     AND NOT EXISTS (recovered sibling on provider_generation_id, usage_source='openrouter_recovered')
   [AND created_at >= :from AND created_at < :to]   ORDER BY created_at
  raises ValueError on inverted window (both bounds or neither)
Schema: usage_records — READS cost_basis/provider_cost/cost_usd/usage_source/provider_generation_id;
        WRITES only the stamped (provider_cost, cost_usd, cost_basis) on the disconnect row at record time.
        No migration (all columns exist since v27/v30).
```

Status: FROZEN @ v33 — approved by Tin (AskUserQuestion 2026-06-23: "Both: stamp + audit")
Least-sure flag surfaced at freeze: [contract] stamping the markup-stripped catalog cost as `provider_cost` labels an ESTIMATE as provider-basis — accepted because it is strictly better than a silent $0/NULL and is confined to rows for which no real upstream cost will ever exist; `usage_source='client_disconnect'` keeps it distinguishable from a real `frame` provider cost.
<!-- superseded DRAFT marker below kept for template lineage -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: hold (new code fully exercised)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_partial_disconnect_stamped_as_unbilled: record a client_disconnect with positive partial usage + disconnect_estimate=True against real Redis→flush→ledger; assert the row has cost_basis='provider', provider_cost>0, cost_usd=0; assert reconcile_window counts it in unbilled_upstream_cost (tenant-scoped).
  - test_recoverable_disconnect_not_stamped: record with disconnect_estimate=False (the openrouter-recoverable case); assert cost_basis stays 'catalog', provider_cost NULL.
  - test_audit_surfaces_zero_estimate_residue: seed a client_disconnect row (provider_cost NULL, cost_usd=0, no recovered sibling) + a recovered one; assert audit_unrecovered_disconnects returns only the residue (tenant-scoped).
  - test_complete_frame_disconnect_not_stamped: record a frame-complete disconnect (disconnect_estimate=False, real usage); assert it bills normally (cost_usd>0).
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
Tests in: `disconnect_provider_cost`
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `/apps/gateway/src/gateway/proxy/domain/ports.py` `recorder.py` `/apps/gateway/src/gateway/usage/application/reconciliation.py` `/apps/gateway/src/gateway/proxy/application/use_cases.py` `/apps/gateway/tests/disconnect_provider_cost/`
Strategy (ordered batches): 1. extend the typed-extras seam (ports.UsageRecordExtras + recorder.supported_extras + record/_record_internal kwarg) · 2. recorder post-costing stamp rule (init markup_pct at top) · 3. use_cases disconnect-handler computes + passes disconnect_estimate · 4. reconciliation audit_unrecovered_disconnects + UnrecoveredDisconnect.
Safety rule (feature-specific): recorder.record never raises; stamp only mutates in-memory locals before xadd (no second write); audit is READ-ONLY; behavior-preserving on recoverable + frame rows.
Code lives in: the scope paths above
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

- [x] all tests pass — `tests/disconnect_provider_cost` 5/5 green; full gateway suite green (ex tests/edge)
- [x] coverage did not decrease — new stamp branch + audit fully exercised (stamp/non-stamp/frame/audit/inverted-window)
- [x] no test or contract was altered during build — §3 FROZEN @ v33; only src + new tests written
- [x] the green was EARNED, not gamed — round-trips through real Redis→flusher→Postgres ledger (not a mocked recorder); reconcile assertion reads the persisted row; audit assertions tenant-scoped against a non-truncated ledger. **Adversarial refute-read (sonnet) returned REFUTE/BLOCKER: a provider-gated predicate double-counts OpenRouter+gen-id rows when the recovery knob is toggled on AFTER a disconnect (sweep keys on gen-id, not provider_cost). CLOSED by re-grounding the predicate to gate on gen-id ABSENCE + adding 2 predicate tests (test_no_gen_id_disconnect_is_stamped / test_gen_id_disconnect_is_not_stamped); refute re-run upheld. All 87 v30 recovery/streaming tests still green.**
- [x] concurrency / timing of the risky operation is safe — stamp mutates in-memory locals BEFORE the single xadd (no second write, no new await); recorder.record still swallows+WARNs; audit is READ-ONLY
- [x] no exposed secrets, injection openings, or unexpected dependencies — typed-extras seam (no introspection); bound params; static clause noqa S608; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — domain TypedDict, application recorder/reconciliation, use_cases wiring; no cross-layer leak
- [x] reviewed under `autonomy: auto` — billing-semantics decision (stamp+audit) was Tin-approved via AskUserQuestion; no security finding

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a non-recoverable partial disconnect persists cost_basis='provider', provider_cost>0, cost_usd=0 — confirmed by test_partial_disconnect_stamped_as_unbilled reading the flushed ledger row + reconcile_window counting it as unbilled_upstream_cost
- [x] a recoverable (openrouter+gen-id) disconnect is byte-identical to v30 — confirmed by test_recoverable_disconnect_not_stamped (cost_basis='catalog', provider_cost NULL, cost_usd>0)
- [x] a complete-frame disconnect bills normally (no forced $0) — confirmed by test_complete_frame_disconnect_not_stamped (cost_usd>0)
- [x] zero-estimate residue is enumerable, recovered rows excluded — confirmed by test_audit_surfaces_zero_estimate_residue (only the NULL/$0 row, the openrouter_recovered sibling excludes its mate)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `disconnect_estimate` flows ports.UsageRecordExtras → _fire_record_with_raw → _dispatch_record (supported_extras filter) → recorder.record → stamp; `audit_unrecovered_disconnects`/`UnrecoveredDisconnect` imported + called by the new tests
- [x] DEAD-CODE (code) — no orphaned symbol; the audit is the operator surface for the zero-estimate residue (a future ops endpoint is a follow-up SPEC delta)
- [x] SEMANTIC (prose / non-code) — n/a (code task)

### GATE RECORD
Outcome: PASS
Reviewed by: auto (autonomy: auto; semantic decision Tin-approved via AskUserQuestion) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of stamped client_disconnect rows (provider_cost>0, cost_usd=0, usage_source='client_disconnect') and count of audit_unrecovered_disconnects (>0 = silent-$0 residue accumulating).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] expose audit_unrecovered_disconnects (and audit_cost_basis_breaches) via an admin/ops endpoint + alert when count>0 (evidence: both audits are library-only; an operator has no surface)
- [SPEC · open] reconcile_window's naive-UTC window binding is session-TimeZone sensitive for timestamptz rows written by the recorder (DB now()); pin the DB session TZ to UTC or bind tz-aware bounds (evidence: test had to widen to ±48h to be offset-proof)
- [SPEC · open] the stamped provider_cost is the markup-stripped catalog ESTIMATE, not a real upstream charge; if a non-OpenRouter cost API ever exists, replace the estimate with a real recovery (evidence: §1 ⚠ assumption)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · open] round-trip recorder→flusher→ledger on a dedicated Redis index (/9 + flushdb) is the honest way to test recorder costing changes — a mocked recorder would have hidden the created_at/tz interaction the wide-window fix surfaced (evidence: test_partial_disconnect first failed on reconcile windowing, not the stamp)
- [ADD · open] a billing-semantics fork mid-build (stamp vs audit vs both) is a genuine decision point even under autonomy:auto — AskUserQuestion resolved it without a security HARD-STOP, and the chosen 'both' composed cleanly with the sibling task's cost_basis audit (evidence: stamp uses cost_basis='provider' so audit_cost_basis_breaches needs no exemption)
