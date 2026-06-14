# TASK: Restore react-hooks lint rules to error + fix flagged patterns

slug: react-hooks-strict-lint · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
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

Feature: Restore `react-hooks/refs` + `react-hooks/set-state-in-effect` to `error` and fix the 60
flagged patterns with behavior-preserving state-model refactors (the eslint baseline returns to 0/0).

Framings weighed:
- **Fix the violations behavior-preservingly, then flip both rules to error** (chosen) — discharges
  the v16 "error→warn is a DECLARED+TICKETED transition, never permanent" convention; the 238-test
  floor proves no regression. The ratchet (rules stay at error) is guarded by a config test.
- Suppress per-line with `eslint-disable` — REJECTED by the milestone "strict-gate ratchet" decision
  (never an escape hatch) and by §0 honors.
- Drop the last-good-on-error feature / the form-reseed to dodge the rules — REJECTED: that is a real
  behavior change (the 4 spend error-fallback tests + the settings reseed UX would break) → HARD-STOP.

Must:
<must>
  - both `react-hooks/refs` and `react-hooks/set-state-in-effect` are set to `error` in
    `apps/dashboard/eslint.config.mjs`; the warn-downgrade block is removed.
  - `eslint .` exits 0 with **0 errors AND 0 warnings** over the production tree (app/components/lib).
  - SpendPage no longer reads/writes a ref during render: last-good-on-error is held in component
    STATE via the React-blessed guarded "adjust state during render" pattern (no useRef, no effect).
  - use-focus-trap no longer writes `onEscapeRef.current` during render: the write moves into an effect.
  - CacheSettings / GuardrailSettings / OidcSettings no longer seed form state in a `useEffect([data])`:
    the seed/reseed moves to a guarded adjust-during-render `prevData` sentinel.
  - the existing behavioral floor (238 tests) stays green; tsc `--noEmit` stays clean.
</must>
Reject:
<reject>
  - any behavioral change to the floor (a previously-green test now fails) -> "behavior_regression" (HARD-STOP)
  - introducing an unguarded setState-in-render (infinite-loop class) -> "set_state_in_render" (eslint error)
  - suppressing a rule with eslint-disable / @ts- comments instead of fixing -> "suppressed_not_fixed"
</reject>
After:
<after>
  - `eslint .` 0/0; both rules at error; 238-test floor green; tsc clean; no ref-in-render, no
    setState-in-effect, no setState-in-render anywhere in the five touched files.
  - a config guard test pins the two rules at "error" so a later task cannot silently re-loosen them.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The guarded "adjust state during render" pattern (setState during render, conditioned on a
    prevData/last-good compare) is lint-clean under the now-error rule-set — lowest confidence because
    `react-hooks/set-state-in-render` is at ERROR in the base config and could, in principle, flag the
    escape hatch. RESOLVED by empirical probe (2026-06-14): unguarded setState-in-render → error;
    the guarded form (SpendPage last-good shape + settings prevData shape) → eslint EXIT 0. If wrong:
    fall back to a key-based remount of an inner form component (no setState at all). Cost: low.
  - [x] `keepPreviousData` does NOT cover the error case (data===undefined on an errored new-key
    query), so last-good-on-error must be held explicitly — confirmed by the existing code's comment
    AND the 4 spend error-fallback tests (esp. test_window_change_error_keeps_prior_view_with_matching_label,
    which asserts viewData.window is the SHOWN data's window, not the pending selector). Keeping the
    last-good mechanism (just state instead of ref) is mandatory.
  - [x] ref-write-inside-an-effect is not flagged by `react-hooks/refs` — confirmed by probe (EXIT 0).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: lint baseline is strict and clean
  Given react-hooks/refs and set-state-in-effect are set to "error"
  When `eslint .` runs over app/ components/ lib/
  Then it exits 0 with 0 errors and 0 warnings

Scenario: the strictness ratchet is pinned
  Given the eslint config
  When the config guard test reads eslint.config.mjs
  Then it asserts both rules are "error" (not "warn"), so a later task cannot silently re-loosen them

Scenario: SpendPage keeps the prior view on a transient error (behavior preserved)
  Given a successful month spend view is shown
  When the window changes to "day" and that query 500s
  Then the alert shows AND the totals stay "5.00" labelled "(month)" (the shown data's window)
  And no ref is read during render (the value comes from component state)

Scenario: focus-trap still closes on Escape with the latest callback (behavior preserved)
  Given an active focus trap with an onEscape callback
  When Escape is pressed
  Then onEscape fires
  And the ref write happens in an effect, not during render

Scenario: settings tabs still seed + reseed from server data (behavior preserved)
  Given the cache tab GET returns { enabled:true, semantic_enabled:false }
  When the tab renders, the user toggles + saves, and the PUT returns new values
  Then the switches seed from the GET, the PUT body is correct, and the switches reflect the response
  And the seed/reseed happens via a guarded adjust-during-render, not a useEffect
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a lint-strictness + behavior-preserving refactor task; the "contract" is the eslint rule
posture + the invariant set the refactor must hold:

```
eslint.config.mjs:
  rules: { "react-hooks/refs": "error", "react-hooks/set-state-in-effect": "error" }
  (the warn-downgrade comment block is removed)

`eslint .`  -> EXIT 0, 0 errors, 0 warnings   (over app/ · components/ · lib/)

Behavioral invariants held (the 238-test floor is the proof):
  SpendPage:   viewData = isError ? lastGood : data   (lastGood in STATE, set via guarded
               adjust-during-render; NO useRef, NO useEffect)
  use-focus-trap: onEscapeRef.current updated INSIDE an effect; Escape + Tab trap unchanged
  settings (Cache/Guardrail/Oidc): form state seeded/reseeded via a prevData sentinel +
               guarded adjust-during-render, replacing useEffect([data]); seed-on-load,
               reseed-on-data-change, and the save→clear UX all unchanged

Guard test: a structural test pins both rules at "error" (the ratchet) — RED while they are "warn".
```

Status: FROZEN @ v1 — approved by Tin Dang (auto mode, autonomy:auto) 2026-06-14

Least-sure flag surfaced at freeze: [build] the guarded "adjust state during render" escape hatch
(SpendPage last-good in state; settings prevData sentinel) could in principle be flagged by
`react-hooks/set-state-in-render` (ERROR in the base config). Why least-sure: a static rule may not
recognize the convergence guard that makes the pattern legal. De-risked BEFORE freeze with a throwaway
eslint probe (2026-06-14): the guarded form → EXIT 0, an unguarded `setState(data)` in render → error
— so the rule is live AND the guard is what clears it. Cost if a real file still trips it: low — fall
back to a key-based remount of an extracted inner form component (no setState during render at all).
Secondary [test]: the config guard test asserts the rules are pinned at "error" (the ratchet) but
cannot itself prove the code is fixed — `eslint .` EXIT 0 / 0-warnings is the real gate (recorded §6).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: hold the existing floor (≥80% global; SpendPage 99.16%, OidcSettings 97.51%).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_react_hooks_rules_are_error (NEW guard): read eslint.config.mjs source; assert it sets
    react-hooks/refs="error" AND set-state-in-effect="error" and contains no "warn" downgrade for them.
    RED now (config has them at "warn"); GREEN after the flip. The ratchet guard (mirrors v17 task 1
    strict-harness.test.ts).
  - the 238-test behavioral floor is the regression net (NOT rewritten): spend-breakdown's 4
    error-fallback tests, spend-chart, tenant-settings' seed/save tests, ui-ux-verify's focus-trap
    Escape tests all keep their assertions and must stay green through the refactor.
  - the REAL red/green: with both rules flipped to "error", `eslint .` EXITS 1 (60 errors) until the
    five files are refactored, then EXITS 0 (0/0). This is the build's pass/fail signal.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · the guard test MUST run red before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/spend/SpendPage.tsx` `apps/dashboard/lib/use-focus-trap.ts` `apps/dashboard/components/settings/CacheSettings.tsx` `apps/dashboard/components/settings/GuardrailSettings.tsx` `apps/dashboard/components/settings/OidcSettings.tsx` `apps/dashboard/eslint.config.mjs` `apps/dashboard/tests-bff/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `apps/dashboard/.next/` `.add/tasks/react-hooks-strict-lint/`
Strategy (ordered batches): 1. add the RED config-guard test (rules still warn) 2. fix the 5 files
(SpendPage state-last-good · use-focus-trap effect-ref · 3 settings prevData) 3. flip both rules to
error in eslint.config.mjs 4. green: `eslint .` 0/0 + vitest floor + tsc.
Safety rule (feature-specific): every refactor is 1:1 behavior-preserving; the 238-test floor is the
proof; a real behavior change is HARD-STOP. Never eslint-disable; never weaken a floor test.
Code lives in: `apps/dashboard/` (components/spend, lib, components/settings, eslint.config.mjs).
Constraints: do NOT change any floor test or its assertions; allow-list packages only (no new deps).

<!-- Scope tokens are project-root-relative (have "/"); coverage/ + tsbuildinfo + .next/ declared per
     the v13 scope-lock convention (lint/test/build regenerate them). EXIT: all green; coverage held;
     no floor test touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run --coverage --testTimeout=20000` EXIT 0, **240 tests / 32 files**
  (238 floor + 2 new guard). NOTE: the first plain `--coverage` run flaked 3 tests (axe + 2 in-flight
  timing assertions) under CPU starvation; re-running the 3 files in ISOLATION → 37/37 green in 2.15s,
  and the full suite with a 20s timeout → 240/240 green. The flakes were load-induced, not regressions.
- [x] coverage did not decrease — **94.03% global** (unchanged from the v16 floor). Touched files:
  SpendPage 99.15% · use-focus-trap 95.58% (↑ from 95.45%) · OidcSettings 97.51% · CacheSettings 96.29%
  · GuardrailSettings 95.05%.
- [x] no test or contract was altered during build — the 238 floor tests keep every assertion verbatim;
  only NEW file is the guard test `tests-bff/lint-rules-strict.test.ts`. §3 contract FROZEN, untouched.
- [x] the green was EARNED — adversarial refute-read (sonnet, 6 targeted attacks) → **VERDICT: EARNED**.
  It confirmed at TanStack source level (queryObserver applies placeholderData only on `pending`, NOT
  `error`) that last-good-on-error MUST be held explicitly — so the state-based replacement is correct
  and the window-label behavior (test_window_change_error_keeps_prior_view) is preserved exactly. No
  infinite loop (structural sharing + the guard converges in one render), no overfit, no eslint-disable
  anywhere in the 5 files. Two nits accepted (sub-frame untriggerable onEscape window; the guard test is
  config-text-only by design — `eslint .` 0/0 is the real gate).
- [x] concurrency / timing safe — the guarded adjust-during-render converges in one extra render
  (`data !== prev` false after `setPrev(data)`; TQ `data` is referentially stable); no render loop.
- [x] no exposed secrets — SECURITY (OidcSettings): `client_secret` is STILL never seeded from server
  data; the new render-guard block explicitly omits it (adversarial attack #3 PASS; test_save_sso +
  test_owner_views_sso_no_secret green — the `<stored>` sentinel reaches no input). No new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — discharges the v16 "framework new-lint-rule:
  error→warn is a DECLARED+TICKETED transition, never permanent / never eslint-disable" convention; the
  ratchet (rules pinned at error) is guarded by the new config test (milestone "strict-gate ratchet").
- [x] a person reviewed and approved — auto-gate (autonomy:auto): behavior-preserving, no security
  surface change (the security invariant is PRESERVED + verified), evidence complete → auto-resolved PASS.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `lastGood`/`setLastGood` feed `viewData` (SpendPage);
  `seededData`/`setSeededData` gate the form seed in each settings tab; the use-focus-trap sync effect
  feeds `onEscapeRef.current` read by `handleKeyDown`. `eslint .` (no-unused) + tsc both EXIT 0.
- [x] DEAD-CODE (code) — removed now-unused imports (`useRef`/`useEffect` from SpendPage; `useEffect`
  from the 3 settings tabs); no orphaned symbol introduced (tsc + eslint confirm).
- [x] SEMANTIC (prose) — read the eslint.config.mjs ratchet comment + the guard test in full; both
  state the invariant and its limitation honestly (config-text guard; `eslint .` is the real gate).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto mode, autonomy:auto — auto-resolved on complete evidence) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass.
     No security finding: the OidcSettings client_secret write-only invariant is PRESERVED and verified
     (adversarial attack #3 PASS + green floor), so this is a clean behavior-preserving auto-PASS. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `eslint .` stays 0/0 (the ratchet test fails loud if a later task
re-downgrades); the 240-test floor stays green; SpendPage spend-recovery + settings reseed UX intact.
Spec delta for the next loop: React's guarded "adjust state during render" (conditional setState during
render, guarded by a prevData/identity compare) is the lint-clean replacement for BOTH ref-read-in-render
AND seed-state-in-effect under the React-Compiler rule-set — `set-state-in-render` permits the guarded
form (verified by probe + adversarial source-read). Prefer it over key-remount for data-seeding forms.

### Competency deltas
- [TDD · open] CPU-starved `vitest run --coverage` flakes timing-sensitive tests (axe ≥5s, in-flight
  `toBeDisabled` windows): 3 false failures vanished on isolation + a 20s testTimeout (evidence: 3 fail
  under load → 37/37 green isolated in 2.15s → 240/240 green with --testTimeout=20000). Fold candidate:
  the verify convention should run the floor with a generous testTimeout (or bounded workers) so a load
  flake never reads as a regression — `make test-fast` no-DB gate already exists; add a timeout floor.
- [ADD · open] the v16 "framework new-lint-rule error→warn is DECLARED+TICKETED, never permanent"
  convention now has a worked DISCHARGE template: fix behavior-preservingly (floor is the proof) → flip
  to error → pin with a config-text ratchet guard test (evidence: this task; mirrors v17 task 1
  strict-harness.test.ts). The ratchet test is config-text-only by design; `eslint .` 0/0 is the real gate.
- [TDD · open] pre-existing AppShell harness leak: tests that render AppShell fire `useCurrentUser`
  (GET /api/auth/me) with no msw handler → up to 7 unhandled-request logs under load (0 when unloaded;
  NOT from this task — diff confirms auth/me untouched). Belongs to `nav-role-filter` (which owns
  useCurrentUser-based nav): stub /api/auth/me in the shared AppShell test setup to reach a true 0-leak.
