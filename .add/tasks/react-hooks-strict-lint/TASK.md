# TASK: Restore react-hooks lint rules to error + fix flagged patterns

slug: react-hooks-strict-lint · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): restore the two eslint-config-next 16 React-Compiler rules
(`react-hooks/refs`, `react-hooks/set-state-in-effect`) from `warn` → `error` in
`apps/dashboard/eslint.config.mjs` (lines 35-38, the v14 downgrade) and FIX the 60 flagged patterns
(57 refs + 3 set-state-in-effect) with behavior-preserving refactors. Verified anchors (2026-06-14):
- **`components/spend/SpendPage.tsx` — 54 of 57 `react-hooks/refs` warnings, all rooted at one read:**
  line 131 `const lastGoodRef = useRef<SpendWindowResponse|undefined>(undefined)`, written CORRECTLY in
  an effect (132-136: `if (!isError && data!==undefined) lastGoodRef.current = data`), but READ DURING
  RENDER at 137 `const viewData = isError ? lastGoodRef.current : data` → the rule flags the in-render
  ref read; the other 53 warnings are the downstream `viewData` derivations (140,236,248,327,353). This
  is the v15 D1 "keep the prior view intact on a transient 422/404" design (a windowed-spend query whose
  queryKey changes per window → errored query has data===undefined, so it falls back to last-good).
  Candidate fixes (decide at specify/contract): (a) TanStack Query `placeholderData: keepPreviousData`
  (keeps prior data across queryKey change — may also cover the error case) + drop the ref; (b) lift
  last-good into `useState` updated in the SAME effect (trades refs→set-state-in-effect, NOT a net win);
  (c) `useEffectEvent`/derive. (a) is most idiomatic if it preserves the error-fallback behavior.
- **`lib/use-focus-trap.ts:36` — 1 `react-hooks/refs`:** `const onEscapeRef = useRef(onEscape); onEscapeRef
  .current = onEscape;` (35-36) writes the ref DURING RENDER (the "keep latest callback without
  re-subscribing the listener" pattern). Fix: move the write into a `useEffect(() => { onEscapeRef.current
  = onEscape })` (or adopt `useEffectEvent`), behavior-preserving (the listener still reads `.current`).
- **`components/settings/{CacheSettings.tsx:46, GuardrailSettings.tsx:69, OidcSettings.tsx:94}` — 3
  `react-hooks/set-state-in-effect`:** each does `useEffect(() => { if (data) { setX(data.x); ... } },
  [data])` to seed local editable FORM state from the arrived server query data. Fix (behavior-preserving):
  the React-idiomatic "reset state when a prop/data identity changes" — either a `key` on the form keyed to
  the data identity (remount-reset) or the documented "store previous data + adjust during render" pattern;
  must keep the edit→save→refetch→reseed UX (covered by the settings BFF tests).

Context (working folder): v17 MILESTONE.md (depends-on bff-test-harness-strict-handlers, now DONE — the
harness is strict + tsc-clean). The 238-test floor @ 94.03% covers all 5 components well (SpendPage 99.16%,
OidcSettings 97.51%, CacheSettings, GuardrailSettings, use-focus-trap 95.45%) → a behavior regression from
the refactor would be CAUGHT by the existing suite (this is the safety net that makes the refactor tractable).

Honors (patterns / conventions): the v16-folded convention "adopting a framework's NEW lint rules on
pre-existing code → downgrade error→warn (visible) + ticket; never break the baseline / never eslint-disable"
— this task is the TICKET being discharged: it flips warn→error AND fixes, never suppresses. Behavior-
preserving is the contract (the 238-test floor is the proof; a real behavior change is HARD-STOP).

Anchors the contract cites: `eslint.config.mjs` (both rules = "error", the warn-downgrade block removed) ·
`eslint .` EXIT 0 with 0 errors AND 0 warnings · `SpendPage.tsx`/`use-focus-trap.ts`/the 3 settings
components refactored (no in-render ref read, no setState-in-effect) · the 238-test floor green @ ≥80% cov.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: <name>
Framings weighed: <chosen> (chosen) · <alternative> · <alternative>
Must:
<must>
  - <required behavior>
</must>
Reject:
<reject>
  - <bad input / situation> -> "<error_code>"
</reject>
After:
<after>
  - <state that is true once it succeeds>
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ <the one assumption most likely to be wrong> — lowest confidence because <why>; if wrong: <cost>
  - [ ] <next assumption, ranked> — confirm or deny; never carry an open one forward
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: <short name>
  Given <starting situation>
  When <action>
  Then <expected result>
  And <what must remain unchanged>   # required for every rejection
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
<METHOD> <path>   body: { <fields> }
  200 -> { <success fields> }
  4xx -> { error: "<code>" | "<code>" }
Schema: <tables/fields touched, and access pattern>
```

Status: DRAFT
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
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

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
