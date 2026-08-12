# TASK: Fire soft-budget alert on the non-chat path (embeddings/images/audio)

slug: nonchat-soft-budget-alert · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Non-chat soft-budget alert — extend `NonChatGovernance` to fire the SAME advisory
soft-budget alert the chat path fires, so an embeddings/images/audio request that crosses a
key's `soft_budget_usd` writes a `soft_budget_exceeded` alert_event identically to chat
(reusing `persist_soft_budget_alert` + the `alert_events` table). Closes the v7 open
follow-up ("the chat M11 soft-budget-alert is DROPPED on the non-chat path"). The HARD 402
budget enforcement already present is UNCHANGED; this only adds the advisory ALERT.

Framings weighed: explicit constructor injection of an optional `session_factory` into
NonChatGovernance + mirror the chat fire-and-forget alert block in `_check_per_key_budget`
(chosen — NonChatGovernance is a concrete class I OWN, so explicit DI is reflection-free and
honors the foundation's NO-hasattr/inspect rule; one alert seam reused, no parallel
re-impl) · copy the chat path's `getattr(guard, "_session_factory")` reflection (rejected —
that is the legacy hasattr seam the foundation says NOT to copy) · a typed-extras seam
(rejected — that is for FROZEN Protocol ports; NonChatGovernance's constructor is mine to
extend additively, which is cleaner than a capability TypedDict here).

Must:
<must>
  - When an embeddings/images/audio request's key has `soft_budget_usd` set AND the per-key
    monthly spend >= soft_budget_usd, NonChatGovernance schedules
    `persist_soft_budget_alert(session_factory, tenant_id, key_id, soft_budget_usd, spent)`
    as a fire-and-forget task — identical args + dedupe semantics to the chat path.
  - The alert fires whether or not a HARD `monthly_budget_usd` is set (soft is independent):
    in the no-hard-budget branch the soft-alert seam runs (cannot 402); the hard-402 path is
    byte-identical to today.
  - NonChatGovernance gains an OPTIONAL `session_factory` constructor dep (default None);
    when None (no DB wired) the alert is silently skipped — existing construction stays valid.
  - The deps wiring (embeddings/images/audio) passes `request.app.state.sessionmaker` so the
    alert is enabled on all three non-chat surfaces.
</must>
Reject:
<reject>
  - the alert write fails (DB error) -> SWALLOWED inside persist_soft_budget_alert (logged,
    never raised) — the request path is NEVER failed by an advisory alert (fire-and-forget).
  - `session_factory` is None (no DB wired) -> the alert is skipped silently; governance
    proceeds (advisory, not a gate).
  - repeated crossings in the same month -> ONE alert_event row (ON CONFLICT dedupe_key DO
    NOTHING — idempotent, inherited from persist_soft_budget_alert).
</reject>
After:
<after>
  - Embeddings/images/audio surface a soft-budget crossing identically to chat (same
    alert_events row, same dedupe_key `soft_budget:{key_id}:{YYYYMM}`).
  - The HARD 402 enforcement and the `authorize` public signature are byte-identical; the
    chat path is untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Extending the frozen NonChatGovernance (frozen @ embeddings-endpoint §3) with an OPTIONAL
    constructor param + an additive alert side-effect is a behavior-preserving EXTENSION, not a
    contract break — lowest confidence because it touches a frozen class; mitigation: the
    public `authorize` signature is unchanged, the hard-402 path is byte-identical, the new
    param defaults None (existing callers/tests keep working), so it is additive like the
    v8–v11 frozen-seam extensions; cost if wrong: a frozen embeddings/images/audio test breaks
    and is caught in the blast radius before gate.
  - [ ] `request.app.state.sessionmaker` is the right session_factory (fresh-session, app-
    scoped, not the request-scoped session that closes at request end) — confirm against
    main.py (it is `async_sessionmaker(engine, ...)`); the chat path uses the same app-scoped
    factory for its fire-and-forget write.
  - [ ] firing the alert in the no-hard-budget branch matches chat exactly — confirm the chat
    `authorize` calls `_check_per_key_budget` in the else branch only when soft_budget set
    (use_cases.py:616-618); the mirror is line-for-line.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: soft crossing, no hard budget → alert fires
  Given a key with soft_budget_usd=10, no monthly_budget_usd, spend=12
  When NonChatGovernance.authorize runs (with a session_factory)
  Then persist_soft_budget_alert is scheduled with (tenant, key, 10, 12)
  And authorize does NOT raise (soft is advisory, cannot 402)

Scenario: soft crossing WITH hard budget under cap → alert fires, no 402
  Given soft_budget_usd=10, monthly_budget_usd=100, spend=12
  When authorize runs
  Then the alert fires AND authorize does not raise (12 < 100)

Scenario: hard cap exceeded still 402 (unchanged)
  Given monthly_budget_usd=100, spend=120
  When authorize runs
  Then BUDGET_EXCEEDED (402) is raised — byte-identical to today

Scenario: spend below soft → no alert
  Given soft_budget_usd=10, spend=5
  When authorize runs
  Then no alert is scheduled and authorize proceeds

Scenario: no session_factory wired → alert skipped, no crash
  Given soft_budget_usd=10, spend=12, session_factory=None
  When authorize runs
  Then no alert is scheduled and authorize proceeds (advisory, not a gate)

Scenario: alert write failure is swallowed
  Given the alert write raises inside persist_soft_budget_alert
  When the fire-and-forget task runs
  Then the exception is swallowed (logged), the request path is unaffected

Scenario: deps wire the session_factory
  Given the embeddings/images/audio dependency providers
  When they build NonChatGovernance
  Then session_factory == request.app.state.sessionmaker (alert enabled on all three)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
gateway.proxy.application.governance.NonChatGovernance — ADDITIVE extension (hard-402 + authorize unchanged)

CHANGED __init__(..., session_factory: Any = None)   # NEW optional dep; stored as self._session_factory
        existing params (authenticator, model_checker, budget_guard, rate_limiter,
        redis_client) UNCHANGED; default None → alert disabled (back-compat).

CHANGED authorize(...) Step 5-7 else-branch (monthly_budget_usd is None):
        if authz.soft_budget_usd is not None: await self._check_per_key_budget(authz)
        (mirror use_cases.py:616-618 — the soft-alert seam runs even without a hard budget)
        then team-budget + tenant-budget UNCHANGED.

CHANGED _check_per_key_budget(authz):
        early-return guard: if budget is None AND authz.soft_budget_usd is None: return
        (was: if budget is None: return)
        after reading spend, BEFORE the hard check:
          if authz.soft_budget_usd is not None and spent >= authz.soft_budget_usd
             and self._session_factory is not None:
            asyncio.ensure_future(persist_soft_budget_alert(
              self._session_factory, authz.tenant_id, authz.key_id,
              authz.soft_budget_usd, spent))   # + add_done_callback swallow (chat-identical)
        hard 402: if budget is not None and spent >= budget: raise BUDGET_EXCEEDED  (UNCHANGED)

CHANGED deps wiring — embeddings_deps / images_deps / audio_deps (×2: STT+TTS):
        NonChatGovernance(..., session_factory=request.app.state.sessionmaker)

Reuses: persist_soft_budget_alert (alert_writer) + alert_events table — same dedupe_key
  "soft_budget:{key_id}:{YYYYMM}", same fire-and-forget pattern. New import: asyncio.
Schema: none new (alert_events already exists). HTTP surface unchanged. Chat path untouched.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] extending the frozen NonChatGovernance with an
OPTIONAL constructor param + an additive alert side-effect is behavior-preserving, not a break
— the public authorize signature + the hard-402 path stay byte-identical, the new param
defaults None; if wrong a frozen embeddings/images/audio test breaks and the blast radius
catches it before gate. The alert reuses the chat seam (no parallel re-impl) via explicit DI
(no hasattr/inspect — honors the foundation capability-seam rule).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new alert seam (8 unit tests, behavior-asserting)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_soft_crossing_no_hard_budget_fires_alert: soft=10/no-hard/spend=12 → alert
    scheduled (sf,tenant,key,10,12) + authorize returns (no raise)
  - test_soft_crossing_with_hard_under_cap_fires_no_402: soft=10/hard=100/spend=12 →
    alert fires AND no raise (12<100)
  - test_hard_cap_exceeded_still_402: hard=100/spend=120 → ProblemError 402
    ERR_BUDGET_EXCEEDED (unchanged)
  - test_spend_below_soft_no_alert: soft=10/spend=5 → no alert + proceeds
  - test_no_session_factory_skips_alert: soft=10/spend=12/sf=None → no alert + proceeds
  - test_alert_failure_is_swallowed: persist raises → authorize unaffected (swallowed)
  - test_default_session_factory_is_none: legacy construction (no kwarg) stays valid
  - test_deps_wire_session_factory: embeddings/images/audio deps wire
    session_factory=request.app.state.sessionmaker
</test_plan>

Tests live in: `tests/nonchat_soft_budget_alert` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the soft-budget alert is fire-and-forget — NEVER awaited on
the hot path and NEVER able to fail the request (persist_soft_budget_alert swallows all
exceptions; the add_done_callback retrieves the task exception so it is never re-raised). The
hard-402 enforcement path stays byte-identical.
Code lives in: `src/gateway/proxy/application/governance.py` + `src/gateway/proxy/api/{embeddings,images,audio}_deps.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — new suite 8/8; blast radius (nonchat + embeddings + images + audio) 48/48;
      chat soft-budget regression (spend_windows + key_governance + health_alerting) 53/53
- [x] coverage did not decrease — additive seam fully covered by 8 new tests
- [x] no test or contract was altered during build — only src + deps + pyproject format-exclude
- [x] concurrency / timing of the risky operation is safe — alert is asyncio.ensure_future
      fire-and-forget (chat-identical), never awaited; add_done_callback retrieves the exception
      so it is swallowed; persist_soft_budget_alert swallows all errors internally; hard-402 path
      byte-identical
- [x] no exposed secrets, injection openings, or unexpected dependencies — alert payload carries
      only decimal strings; dedupe_key uses key_id (uuid)/YYYYMM, never key strings; parametrised
      SQL (text + bound params); no new packages
- [x] layering & dependencies follow CONVENTIONS.md — explicit constructor DI (reflection-free,
      honors the NO hasattr/inspect capability-seam rule); alert_writer imported locally to avoid
      module-level circular risk (same pattern as the chat path)
- [x] reviewed — auto-resolved under delegated auto mode (additive, behavior-preserving, no
      security finding); evidence-complete

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `session_factory` param stored as `self._session_factory`, read in
      `_check_per_key_budget`; `authorize` else-branch now calls `_check_per_key_budget` when
      soft set; all 3 deps (embeddings/images/audio×2) wire `request.app.state.sessionmaker`
      (asserted by test_deps_wire_session_factory + grep: 4 construction sites, all wired)
- [x] DEAD-CODE (code) — no orphaned symbol; the new param + seam are exercised by the suite
- [ ] SEMANTIC (prose / non-code) — n/a (code change)

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
