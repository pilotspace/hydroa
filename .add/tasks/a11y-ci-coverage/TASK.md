# TASK: Automated WCAG axe coverage across key surfaces

slug: a11y-ci-coverage · created: 2026-06-26 · stage: production
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
- `apps/dashboard/test-support/axe.ts` : EXISTING `axe(container, opts)` (axe-core run) + `axeMatchers.toHaveNoViolations`. ADD `expectNoSeriousViolations(container, opts?)` — runs axe and asserts ZERO `serious`/`critical` impact violations (the WCAG-AA bar the suite already applies AD-HOC, e.g. landing-page.test.tsx filters impact inline). Codifies the threshold in one place.
- (NEW) `apps/dashboard/tests/a11y-coverage.test.tsx` : a consolidated a11y net rendering the KEY surfaces that currently LACK axe and asserting `expectNoSeriousViolations`.
- (read-only) `apps/dashboard/components/auth/LoginForm.tsx` · `SignupForm.tsx` : auth forms — MISSING axe today (login/signup/sso tests have none). `components/ui/route-error.tsx` + `app/not-found.tsx` : new task-4 segments — no axe yet.

Context (working folder):
- axe is ALREADY broadly used (marketing pages, dashboard surfaces, design-system) via `@/test-support/axe`. The GAP = auth forms (login/signup/sso) + the new failure segments. The serious/critical filter is duplicated inline (landing-page.test.tsx:149).
- The vitest suite IS the CI a11y gate (`@axe-core/playwright` + the `test:a11y` playwright script exist but are NOT in CI — out of scope; this task hardens the in-CI jsdom axe coverage).
- LoginForm/SignupForm render standalone in jsdom (login.test.tsx does `render(<LoginForm/>)`); next-navigation is mocked in setup.

Honors (patterns / conventions):
- v13 a11y discipline (axe 0 serious/critical, monotonic headings, landmarks) — this task makes the threshold a reusable helper + closes the auth-surface gap.
- Reuse the existing axe-core helper; NO new dependency.

Anchors the contract cites: `expectNoSeriousViolations` (new helper), `axe` (existing), the impact filter (`serious`|`critical`), the covered surfaces (LoginForm/SignupForm/NotFound/RouteError).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A reusable serious/critical axe helper + a consolidated a11y coverage net over the uncovered key surfaces
Framings weighed: shared helper + one coverage test (chosen) · add axe inline to each surface's own test (rejected — scatters the threshold, easy to forget) · adopt playwright a11y in CI (rejected — heavier, out of scope; jsdom net is the in-CI gate)
Must:
<must>
  - M1 `expectNoSeriousViolations(container, opts?)` runs axe-core and asserts ZERO violations of impact `serious` or `critical`; on failure it lists the offending rules+nodes (reuses the existing formatter). Lower-impact (`minor`/`moderate`) violations do NOT fail (matching the suite's WCAG-AA bar).
  - M2 A consolidated `a11y-coverage.test.tsx` renders each currently-UNCOVERED key surface — LoginForm, SignupForm, NotFound, RouteError — and asserts `expectNoSeriousViolations` on each.
  - M3 The helper accepts axe run options (e.g. to scope rules) and forwards them verbatim — silences NO rule by default.
  - M4 No new dependency (reuses axe-core/@/test-support/axe); no existing test or component changed; the full suite stays green.
</must>
Reject:
<reject>
  - a serious/critical a11y violation on a key surface -> the coverage test FAILS (lists rule+nodes) (M1/M2)
  - silently lowering the bar (silencing a rule globally) -> rejected: the helper passes options through but silences nothing by default (M3)
</reject>
After:
<after>
  - The auth forms + failure segments are axe-checked in the standard (CI) vitest suite at the same serious/critical bar as the marketing/dashboard surfaces; the threshold lives in one reusable helper. No new dep, no behavior change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The uncovered surfaces actually PASS at serious/critical today — lowest confidence because they were never axe-checked; if one fails, that's a REAL a11y bug this task surfaces. Policy: a genuine violation on these shipped surfaces is fixed minimally IN this task (a label/role/contrast fix), or — if it needs design input — recorded as a SPEC delta and the surface temporarily scoped out with a logged reason (never silently). Most likely: forms already use labeled inputs (login.test uses getByLabelText), so they should pass.
  - [ ] color-contrast: jsdom can't compute layout/contrast reliably — axe may skip or false-positive color-contrast in jsdom; if noisy, scope `color-contrast` off for the jsdom net (it's a browser-only check) and note it. Confirm at build.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 helper fails on a serious violation
  Given a container with a serious/critical axe violation
  When expectNoSeriousViolations(container) runs
  Then it throws an assertion listing the rule + node

Scenario: M1 helper passes when only minor/moderate (or none)
  Given a clean container (or only minor issues)
  When expectNoSeriousViolations(container) runs
  Then it does not throw

Scenario: M2 LoginForm passes the serious/critical bar
  Given <LoginForm/> rendered
  When axe-checked via the helper
  Then no serious/critical violations

Scenario: M2 SignupForm passes
  Given <SignupForm/> rendered
  When axe-checked
  Then no serious/critical violations

Scenario: M2 NotFound + RouteError pass
  Given the failure segments rendered
  When axe-checked
  Then no serious/critical violations

Scenario: M4 no regression
  Given the full suite
  When run with the helper + coverage test added
  Then every existing test stays green and no rule was silenced by default
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// test-support/axe.ts (ADD to the existing module)
const SERIOUS = new Set(["serious", "critical"]);
export async function expectNoSeriousViolations(
  container: axeCore.ElementContext,
  options: axeCore.RunOptions = {},
): Promise<void>
//   const results = await axe(container, options)
//   const blocking = results.violations.filter(v => SERIOUS.has(v.impact ?? ""))
//   if (blocking.length) throw new Error(`serious/critical a11y violations:\n${formatViolations(blocking)}`)

// tests/a11y-coverage.test.tsx (NEW)
//   render(<LoginForm/>)     → await expectNoSeriousViolations(container)
//   render(<SignupForm/>)    → await expectNoSeriousViolations(container)
//   render(<NotFound/>)      → await expectNoSeriousViolations(container)
//   render(<RouteError error reset/>) → await expectNoSeriousViolations(container)
//   + a unit test that the helper THROWS on an injected serious violation (e.g. an <img> with no alt)
//     and PASSES on a clean container.
```

Schema: none — test-only + a test-support helper. No DB, no network, no new dependency, no app code touched.

Least-sure flag surfaced at freeze: [spec] whether the never-axe'd surfaces (LoginForm/SignupForm/NotFound/RouteError) PASS at serious/critical today — if one fails it's a REAL bug: fix minimally here, or scope-out with a logged SPEC delta (never silent). · [test] jsdom color-contrast may be unreliable — scope `color-contrast` off the jsdom net if noisy (it's browser-only), noting it.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; test-only hardening, low-risk; a real violation is surfaced/fixed/logged, never silenced)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on the new `expectNoSeriousViolations` helper.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_helper_throws_on_serious: render a container with an <img> missing alt (serious) → expectNoSeriousViolations rejects.
  - test_helper_passes_when_clean: render a clean container → resolves (no throw).
  - test_login_form_no_serious: render <LoginForm/> → expectNoSeriousViolations resolves.
  - test_signup_form_no_serious: render <SignupForm/> → resolves.
  - test_not_found_no_serious: render <NotFound/> → resolves.
  - test_route_error_no_serious: render <RouteError error reset/> → resolves.
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/a11y-coverage.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/test-support/axe.ts` `apps/dashboard/tests/a11y-coverage.test.tsx`
Strategy (ordered batches): 1. add expectNoSeriousViolations to test-support/axe.ts. 2. write the coverage test. 3. run — if a surface fails, fix minimally or log a SPEC delta. 4. green.
Safety rule (feature-specific): the helper silences NO rule by default; a real violation is fixed or logged, never suppressed.
Code lives in: `apps/dashboard/test-support/` + `apps/dashboard/tests/`
Constraints: do NOT change any existing test or app behavior; allow-list packages only (NO new dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 553 green (68 files); +6 new a11y tests
- [x] coverage did not decrease — new helper exercised both ways (throws on serious / passes when clean) + 4 surface checks
- [x] no test or contract was altered during build — additive helper + new test only; no app code touched (547→553 additive)
- [x] the green was EARNED — the helper is proven to actually FAIL (test_helper_throws_on_serious renders an alt-less <img> → rejects) AND pass when clean, so the 4 surface assertions are meaningful (not vacuous); they run the REAL axe-core against REAL rendered forms/segments. Test-only, no logic to game → no subagent refute-read needed
- [x] concurrency / timing safe — N/A: test-only, axe runs synchronously per render
- [x] no exposed secrets, injection openings, or unexpected dependencies — ZERO new deps; the helper silences NO rule by default (forwards options verbatim)
- [x] layering & dependencies follow CONVENTIONS.md — helper added to the existing test-support/axe.ts; reuses the shared formatter
- [x] a person reviewed — Tin approved the freeze (real violations would be fixed/logged, never silenced); the uncovered surfaces passed cleanly — no violation to escalate; auto-gate. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] The helper FAILS on a real serious violation — confirmed: `test_helper_throws_on_serious` (alt-less img) rejects with the rule list
- [x] LoginForm/SignupForm/NotFound/RouteError carry ZERO serious/critical violations — confirmed: all 4 surface tests pass (the never-axe'd surfaces are clean; no bug surfaced)
- [x] No rule silenced, no regression — helper forwards options verbatim; 553-green suite, tsc 0, eslint 0 errors

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `expectNoSeriousViolations` imported by the coverage test; reuses `axe` + `formatViolations` in the same module.
- [x] DEAD-CODE — helper consumed by 6 tests; BLOCKING_IMPACTS used; no orphan.
- [x] SEMANTIC — re-read the helper: filters impact to serious/critical, throws with the formatted list, default-silences nothing.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · auto-resolved under autonomy:auto (test-only hardening; surfaces clean) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): new serious/critical violations as surfaces evolve (the coverage test catches regressions in CI); the apply tasks should add each newly-built surface to this net.

### Spec delta
- [SPEC · open] Migrate the inline serious/critical filters (landing-page.test.tsx etc.) to `expectNoSeriousViolations` for consistency — this task added the helper but didn't refactor existing callers (evidence: ~10 tests still filter impact inline).
- [SPEC · open] Wire the `@axe-core/playwright` `test:a11y` script into CI for real-browser color-contrast/focus checks jsdom can't do — out of scope here; the jsdom net is the in-CI gate today (evidence: test:a11y exists but isn't run in CI).
- [SPEC · seeded] As harden-marketing/admin/auth build new surfaces, add each to a11y-coverage.test.tsx so the net grows with the app.

### Competency deltas
- [TDD · open] An a11y assertion helper must itself be proven to FAIL (render a known-bad node and assert it throws) — otherwise the surface "passes" could be vacuous; pair every "passes clean" with a "fails on real violation" test (evidence: test_helper_throws_on_serious anchors the 4 surface checks).
- [UDD · open] The never-axe'd auth forms + new failure segments passed serious/critical on the first check — the shared primitives (labeled Input, ErrorState role=alert) carry a11y by construction (evidence: 0 violations surfaced).
