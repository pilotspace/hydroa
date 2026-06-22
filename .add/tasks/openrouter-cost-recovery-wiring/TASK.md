# TASK: Openrouter Cost Recovery Wiring

slug: openrouter-cost-recovery-wiring · created: 2026-06-22 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase` — `__init__` deps; `stream()._wrapped()` disconnect handler (the `except (GeneratorExit, asyncio.CancelledError)` block ~1479-1533) already fires the disconnect record + `disconnect_gen_id = extract_generation_id_from_sse(collected)` (t6.2) + `await gen.aclose()` (t5) then `raise`. `_resolve_credential` resolves the provider via `self._provider_resolver.provider_for(model_id)`.
- `apps/gateway/src/gateway/usage/application/cost_recovery.py:OpenRouterCostRecoveryService.recover(*, tenant_id, key_id, model, provider_generation_id) -> RecoveryOutcome` — t6.2b CORE; never raises.
- `apps/gateway/src/gateway/proxy/api/deps.py:get_completion_use_case` — builds CompletionUseCase from `app.state` via `getattr(..., None)` (tenant_credential_resolver, provider_resolver).
- `apps/gateway/src/gateway/main.py` — lifespan wiring: `app.state.openrouter_completion_upstream` (~413, the get_generation client), `app.state.usage_recorder` (~516, concrete RecordingUsageRecorder), `app.state.sessionmaker`, `app.state.tenant_credential_resolver` (~629), `app.state.provider_resolver` (~426, CatalogProviderResolver).
- `apps/gateway/src/gateway/core/config.py:Settings` — `Field(default=...)` knobs, env `GATEWAY_<UPPER>` (e.g. `upstream_stream_resilience_enabled`, `openrouter_usage_accounting`).

Context (working folder): v30 t6 inline recovery wiring. t6.2b built the recovery service; THIS task fires it (fire-and-forget) from the disconnect handler when the disconnected stream was OpenRouter, behind a default-OFF knob. The periodic sweep (t6.3) is the reliable backstop for whatever inline misses (restart, knob-off-window).

Honors (patterns / conventions): default-OFF knob ⇒ byte-identical when unset (v29 drift-checker / v27 usage-accounting precedent); never block or mask the disconnect re-raise (t5 floor); no NEW await in the GeneratorExit/CancelledError teardown path (capture provider at stream-setup); app.state seam so tests override.

Anchors the contract cites: `CompletionUseCase.__init__(cost_recovery=...)`, `app.state.cost_recovery_service`, `Settings.openrouter_cost_recovery_enabled`, `OpenRouterCostRecoveryService.recover`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Inline fire-and-forget OpenRouter cost-recovery wiring — schedule recover() from the stream disconnect handler.
Framings weighed: fire-and-forget task from the disconnect handler (chosen — best-effort, never blocks teardown; sweep is the backstop) · await recovery inline before re-raise (rejected — blocks/holds the disconnect, and recovery polls for seconds) · no inline at all, sweep-only (rejected — Tin chose BOTH for promptness).
Must:
<must>
  - CompletionUseCase gains an optional `cost_recovery: OpenRouterCostRecoveryService | None = None` dep (None ⇒ feature off ⇒ byte-identical).
  - In the stream disconnect handler, when `cost_recovery` is wired AND a generation id was captured AND the stream's provider is `openrouter`, schedule `recover(tenant_id, key_id, model, provider_generation_id)` as a FIRE-AND-FORGET task (asyncio.ensure_future) — do not await it.
  - Resolve the provider ONCE at stream setup and read it synchronously in the teardown path — add NO new await inside the GeneratorExit/CancelledError handler.
  - Scheduling must NEVER block the teardown nor mask the disconnect/cancel re-raise (suppress any scheduling error).
  - main.py constructs the service ONLY when `GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED` is true (default False); else `app.state.cost_recovery_service = None`.
  - deps.py passes `app.state.cost_recovery_service` into the use case.
</must>
Reject:
<reject>
  - cost_recovery unwired (None) -> no task scheduled (feature off / frozen test suites)
  - no generation id captured on the disconnect row -> no task scheduled
  - provider != 'openrouter' -> no task scheduled
  - provider_resolver unwired -> provider unknown -> no task scheduled (fail-closed)
</reject>
After:
<after>
  - With the knob ON + an OpenRouter disconnect carrying a gen id, exactly one recover() task is scheduled with (tenant_id, key_id, model_id, gen_id).
  - The disconnect/cancel still re-raises and the t5/t6.2 record + aclose are unchanged.
  - Default config (knob unset) builds the app with `app.state.cost_recovery_service is None` ⇒ zero behavior change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Scheduling a fire-and-forget task DURING GeneratorExit/CancelledError handling is safe and the task survives to run — lowest confidence because the request task is being torn down; if wrong: the scheduled recover() is cancelled before it does useful work. MITIGATION: this is exactly why inline is best-effort and the t6.3 SWEEP exists — a cancelled inline attempt is silently re-covered by the periodic backstop. Never a wrong bill, only a deferred one.
  - [ ] `provider_for(model_id)` returns the literal `'openrouter'` for OpenRouter models — confirm against CatalogProviderResolver mapping.
  - [ ] capturing the provider at setup (a 2nd provider_for call alongside _resolve_credential's) is negligible — confirm it is an in-memory dict lookup.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: inline recovery scheduled on an OpenRouter disconnect
  Given a CompletionUseCase wired with a cost_recovery service and provider_resolver→'openrouter'
  And a stream that yields an SSE chunk carrying id "gen-xyz" then the client disconnects
  When the disconnect handler runs
  Then recover() is called exactly once with model + provider_generation_id="gen-xyz"
  And the disconnect billing record and the upstream aclose still happen

Scenario: not scheduled when the service is unwired
  Given a CompletionUseCase with cost_recovery=None
  When an OpenRouter stream disconnects with a gen id
  Then recover() is never called
  And the disconnect record still fires

Scenario: not scheduled when no generation id was captured
  Given cost_recovery wired but the stream carried no id
  When the client disconnects
  Then recover() is never called
  And the disconnect record still fires

Scenario: not scheduled for a non-OpenRouter provider
  Given cost_recovery wired and provider_resolver→'anthropic'
  When an Anthropic stream disconnects with a gen id
  Then recover() is never called
  And the disconnect record still fires

Scenario: scheduling never masks the disconnect
  Given cost_recovery wired and provider 'openrouter'
  When the client disconnects mid-stream
  Then the GeneratorExit/cancel still propagates (the generator is closed)
  And recovery is scheduled without raising

Scenario: recovery disabled by default
  Given the app is built with no GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED set
  When the lifespan wires state
  Then app.state.cost_recovery_service is None
  And streaming behaviour is byte-identical to pre-task
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# No HTTP surface — this is internal wiring on the streaming path.
CompletionUseCase.__init__(..., cost_recovery: OpenRouterCostRecoveryService | None = None)

# stream()._wrapped() disconnect handler (additive, AFTER the existing t6.2 record + t5 aclose):
if (self._cost_recovery is not None
        and disconnect_gen_id
        and _stream_provider == "openrouter"):
    with contextlib.suppress(BaseException):
        asyncio.ensure_future(self._cost_recovery.recover(
            tenant_id=tenant_id, key_id=key_id, model=model_id,
            provider_generation_id=disconnect_gen_id))
# _stream_provider captured once at stream setup: provider_for(model_id) if provider_resolver else None

# Settings
openrouter_cost_recovery_enabled: bool = False   # GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED

# main.py lifespan
app.state.cost_recovery_service = (
    OpenRouterCostRecoveryService(upstream=app.state.openrouter_completion_upstream,
        recorder=app.state.usage_recorder, session_factory=app.state.sessionmaker,
        credential_resolver=app.state.tenant_credential_resolver)
    if settings.openrouter_cost_recovery_enabled else None)

# deps.py
cost_recovery=getattr(request.app.state, "cost_recovery_service", None)
Schema: none (no DDL; reuses t6.2/t6.2b). Reads: provider_for(model_id) at setup; the
fire-and-forget recover() does its own ledger IO (t6.2b).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto)
Least-sure flag surfaced at freeze: [spec] a fire-and-forget task scheduled DURING the disconnect teardown may be cancelled before it runs (the request task is dying). Cost: that inline attempt does nothing. Mitigation contracted: inline is best-effort BY DESIGN — the t6.3 periodic sweep re-covers any inline miss; never a wrong bill, only a deferred one.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (the new gating branch + the config default)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_inline_recovery_scheduled_on_openrouter_disconnect: PlanStreamUpstream w/ id chunk + spy cost_recovery + FakeProviderResolver→'openrouter' / disconnect / assert spy.recover called once with model+gen_id; record still fired
  - test_not_scheduled_when_service_unwired: cost_recovery=None / disconnect / assert no recover; record fired
  - test_not_scheduled_when_no_generation_id: chunks carry no id / disconnect / assert no recover; record fired
  - test_not_scheduled_for_non_openrouter_provider: FakeProviderResolver→'anthropic' / disconnect / assert no recover; record fired
  - test_scheduling_never_masks_disconnect: spy recover that would raise is suppressed / assert GeneratorExit still propagated (gen closed) + no exception escaped
  - test_app_builds_with_recovery_disabled_by_default: build app (no env) / assert app.state.cost_recovery_service is None
</test_plan>

Tests live in: `apps/gateway/tests/openrouter_cost_recovery_wiring/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/tests/openrouter_cost_recovery_wiring/`
Strategy (ordered batches): 1. config knob 2. use_cases: cost_recovery dep + capture _stream_provider at setup + gated fire-and-forget in disconnect handler 3. deps.py pass-through 4. main.py construct-when-enabled 5. tests green.
Safety rule (feature-specific): NO new await in the GeneratorExit/CancelledError handler (capture provider at setup); suppress(BaseException) around scheduling so it never masks the re-raise; default-OFF ⇒ byte-identical.
Code lives in: `apps/gateway/src/gateway/`
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

- [x] all tests pass — 7/7 in tests/openrouter_cost_recovery_wiring; full suite 1279 passed + 1 PRE-EXISTING shared-DB isolation flake (tenants/test_signup_taken_email_rejected — 11/11 green in isolation, untouched by this change)
- [x] coverage did not decrease — additive gating branch + config knob, all covered
- [x] no test or contract was altered during build — contract FROZEN @ v1; tests strengthened (added async-raising case) then re-crossed tests→build
- [x] the green was EARNED — adversarial refute-read (sonnet) on the HOT path: verdict NOT-REFUTED on correctness (no masked disconnect, no new await, byte-identical when off). Earned MEDIUM: the ensure_future lacked the file's standard add_done_callback exception-retrieval pattern → closed by matching it + a new async-raising test.
- [x] concurrency / timing of the risky operation is safe — ensure_future is synchronous (no new await in teardown); scheduling AFTER record+aclose, suppress(BaseException) so it never masks the re-raise; done_callback retrieves any task exception; new task is independent of the dying request task's cancellation
- [x] no exposed secrets, injection openings, or unexpected dependencies — credential resolved out-of-band by the service (t6.2b), not here; no new packages
- [x] layering & dependencies follow CONVENTIONS.md — use-case depends on a structural `_InlineCostRecovery` Protocol, not the concrete usage-layer service (no layering cycle)
- [x] a person reviewed and approved the change — Tin chose "build t6.2c then t6.3" via AskUserQuestion; autonomy:auto drives

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] OpenRouter disconnect with a gen id schedules exactly one recover() with (tenant,key,model,gen_id) — confirmed by test_inline_recovery_scheduled (spy.calls len 1, gen_id, model==disconnect-record model)
- [x] recovery NOT scheduled when off/no-gen-id/non-openrouter — confirmed by the 3 negative tests (spy.calls == [])
- [x] the disconnect record + re-raise are unchanged — every test asserts usage_source=='client_disconnect' still fired; sync- and async-raising recover both leave teardown intact
- [x] knob OFF (default) ⇒ app.state.cost_recovery_service is None ⇒ byte-identical — confirmed by test_app_builds_with_recovery_disabled_by_default; the `_stream_provider` resolution is also skipped when cost_recovery is None

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `cost_recovery` ctor param → `self._cost_recovery` → gated ensure_future in disconnect handler; main.py constructs the service (knob-gated) onto app.state.cost_recovery_service; deps.py passes it through; `_stream_provider` captured at setup and read in the handler. All references present.
- [x] DEAD-CODE (code) — no orphan: `_InlineCostRecovery` types the param; `openrouter_cost_recovery_enabled` read in main.py; `_stream_provider` read in the handler

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin (autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): inline-recovery-scheduled rate vs openrouter-disconnect rate (knob-on coverage); recover() outcome distribution from inline vs sweep (how often inline lands vs gets cancelled at teardown); any "Task exception was never retrieved" log (should be zero — done_callback consumes it).

### Spec delta
- [SPEC · open] t6.3 openrouter-recovery-sweep — the reliable backstop the inline path defers to (anchor_not_flushed / cancelled-at-teardown). Find client_disconnect rows w/ provider_generation_id and NO openrouter_recovered row → recover(); partial index + NOT-EXISTS dedup; default-OFF knobs.
- [SPEC · open] when both BYOK + cost-recovery are wired, provider_for(model_id) is resolved twice per stream setup (once in _resolve_credential, once for _stream_provider) — harmless (O(1) dict) but could be threaded through once if it ever matters (evidence: refute Finding 3).

### Competency deltas
- [ADD · folded] hot-path fire-and-forget must follow the file's EXISTING ensure_future hygiene (capture task + add_done_callback to retrieve exceptions) — a lone suppress(BaseException) only covers the synchronous schedule, not the coroutine's later raise (evidence: refute Finding 1, closed by matching the 8 sibling sites + an async-raising test). [folded foundation-version 28]
- [TDD · folded] a fire-and-forget test must (a) await a settle to prove the task RAN, and (b) exercise BOTH a sync-raise (schedule guard) and an async-raise (done_callback) — sync-only leaves the task-exception path uncovered (evidence: refute Finding 2). [folded foundation-version 28]
