# TASK: Verification pass across the redesigned usage/cost + key/budget journeys (axe a11y, keyboard, responsive, suites green)

slug: ui-ux-verify · created: 2026-06-13 · stage: production
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

Touches (files · symbols · signatures): the milestone-exit VERIFICATION pass over the two redesigned journeys (tasks 2+3). ADDS a verification suite; touches NO production component (verify-only). Verified the surfaces + tooling:
- Redesigned surfaces under test: usage/cost — `components/usage/{UsagePage,UsageStatsCards,UsageTable,BudgetWidget,BudgetEditForm}` + `components/spend/{SpendPage,SpendSparkline}`; key/budget — `components/keys/{KeysPage,KeyRow,CreateKeyDialog,PlaintextKeyBanner,KeyGovernanceEditor}`. All already restyled onto `components/ui/*` + the `@theme` tokens.
- a11y tooling already in place (v13 task 1): `vitest-axe` + `axe-core` (allow-listed); the established pattern is `tests/design-system/a11y.test.tsx` — `import { axe } from "vitest-axe"`, `expect.extend(axeMatchers)`, `expect(await axe(container)).toHaveNoViolations()`, with a local `declare module "vitest"` augmentation for `toHaveNoViolations`. axe default ruleset = WCAG 2.0/2.1/2.2 A+AA.
- Keyboard a11y already shipped: `lib/use-focus-trap.ts` (focus-trap + ESC + restore) wired into the 3 /keys dialogs; `components/ui` Button/Input carry `focus-visible:ring-2 focus-visible:ring-ring`; AppShell has skip-link `href="#main"`, `<nav aria-label="Primary">`, `<main id="main">`.
- Existing behavioral suites (the regression floor — MUST stay green): `tests/usage.test.tsx`, `tests/keys.test.tsx`, `tests-bff/govern.test.tsx`, `tests-bff/spend-chart.test.tsx`, `tests/keys-dialog-a11y.test.tsx`, + the design-system suites = 110 tests at HEAD.
- Test harness: vitest 3.2 + jsdom; two projects `legacy` (tests/) + `bff` (tests-bff/). `npx` stdout is swallowed in this env → run `./node_modules/.bin/vitest run --reporter=json --outputFile=…` and parse.

Context (working folder):
- ENVIRONMENT LIMIT (decisive for scope): jsdom has NO layout engine and NO canvas → axe-core's `color-contrast` rule CANNOT run (it needs canvas pixel sampling) and true CSS breakpoint rendering (desktop/tablet/mobile) CANNOT be measured. No Playwright/Puppeteer is installed and `next dev` needs a live gateway backend the sandbox lacks. So the milestone's "browser-axe color-contrast" + "renders correctly across breakpoints" are verifiable only in a real browser — recorded as an explicit, documented residue (NON-security), not silently passed.
- `.add/milestones/v13/MILESTONE.md` ui-ux-verify row + exit criteria #4 (axe zero serious/critical + keyboard) and #6 (responsive desktop/tablet/mobile).

Honors (patterns / conventions):
- MILESTONE.md: WCAG 2.2 AA enforced — every redesigned surface passes axe with zero serious/critical AND keyboard-operable; behavior/data unchanged (the existing suites stay green); responsive to mobile.
- v13 design-system contract + CONVENTIONS.md v1 UDD: RTL scoping `within(section)`; the four state patterns; tokens consumed not hardcoded.

Anchors the contract cites: the 12 redesigned surface components above (under axe + keyboard verification) · `vitest-axe`/`axe-core` (the a11y gate tool) · `lib/use-focus-trap.ts` + `components/ui` focus-visible rings + AppShell landmarks (the keyboard surface) · the responsive token utilities (`sm:`/`md:` grid/flex classes) as the static responsive proxy · the 110-test behavioral floor that MUST stay green · the documented browser-only residue (color-contrast + visual breakpoints).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: UI/UX verification pass — an automated WCAG 2.2 AA (axe) + keyboard-operability + state-pattern + responsive-utility verification suite across the two redesigned journeys (usage/cost + key/budget), plus the milestone-exit confirmation that the full behavioral floor stays green. Adds tests only; touches no production component.

Framings weighed: Automated vitest-axe + keyboard + responsive-class suite over every redesigned surface, with browser-only checks (color-contrast, visual breakpoints) recorded as explicit residue (chosen — maximal verifiable coverage in this CI/jsdom env, CI-runnable, honest about the gap) · Full Playwright/browser-axe E2E (rejected — no Playwright installed, `next dev` needs a live gateway backend the sandbox lacks; would be a separate infra task) · Trust the per-task green and skip a dedicated verify suite (rejected — the milestone's exit criteria #4/#6 demand a cross-journey a11y+keyboard gate, not just per-task suites).

Must:
<must>
  - Run an axe-core (vitest-axe) scan on EVERY redesigned surface in its data/success state — UsagePage, SpendPage (+chart), KeysPage (list), CreateKeyDialog (open), KeyGovernanceEditor (open), PlaintextKeyBanner — asserting ZERO serious/critical violations (WCAG 2.2 A+AA ruleset).
  - Verify keyboard operability: the 3 /keys dialogs trap focus + close on Escape + restore focus (assert via the focus-trap behavior); the AppShell skip-link → `#main` and landmark roles (`nav[aria-label]`, `main#main`) are present; interactive controls are reachable by role/name (no mouse-only affordance).
  - Verify the four state patterns (loading · empty · error · success) render on each stateful surface via the shared state components (role=status / role=alert / Empty / data).
  - Verify responsive intent statically: the redesigned surfaces apply the responsive token utilities (e.g. `sm:`/`md:` grid/flex breakpoints) rather than fixed widths — a jsdom-checkable proxy for the breakpoint layout.
  - Confirm the entire behavioral floor stays green (all suites at HEAD + the new verify suite) with NO production-component change in this task.
  - Record the milestone exit-criteria status: which criteria are PROVEN here vs the explicit browser-only RESIDUE (color-contrast pixel sampling + true desktop/tablet/mobile visual rendering), so the gate is honest.

Reject:
<reject>
  - Any redesigned surface with a serious/critical axe violation -> "a11y_serious_violation"
  - A dialog not keyboard-operable (focus not trapped, or Escape does not close) -> "keyboard_inoperable"
  - A surface missing one of the four state renderings -> "state_pattern_gap"
  - This task modifying any production component (it is verify-only) -> "scope_creep" (a fix belongs back in task 2/3 as a change request)
  - Silently claiming browser-only coverage (contrast/visual-responsive) that jsdom cannot prove -> "unverifiable_claim"
  - Any import outside the node allow-list -> "unlisted_dependency"
</reject>
After:
<after>
  - A `tests/` verification suite proves zero serious/critical axe violations + keyboard operability + the four state patterns + responsive-utility presence across both journeys.
  - The full suite (110 prior + the new verify tests) is green; coverage ≥ 80% held; lint clean; no production component changed.
  - The gate record states the PROVEN criteria and the documented browser-only residue (non-security) explicitly — no silent pass.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ axe-core run in jsdom surfaces NO false serious/critical violations on the populated surfaces (beyond the un-runnable color-contrast rule) — lowest confidence because axe in jsdom can flag structural issues (e.g. a `role=dialog` without an accessible name, a table without a caption, a `<ul>` nested oddly, a duplicate id from repeated key rows) that did not show in the per-task suites. If wrong: each finding is a REAL a11y bug → fix it in the owning component as a scoped change (this is the verify task doing its job), re-running until zero serious/critical; a structural axe fix is markup-level, no data/contract change.
  - [ ] color-contrast genuinely cannot run in jsdom (needs canvas) so excluding it is correct, not a dodge — CONFIRMED (axe-core documents the canvas dependency); recorded as browser residue, not passed.
  - [ ] the responsive-utility static check is a meaningful proxy (the surfaces really do use `sm:`/`md:` classes from the restyle) — confirmed by the task 2/3 diffs (grid-cols-2 sm:grid-cols-4, flex layouts); true visual breakpoint rendering remains browser residue.
  - [ ] verify-only scope holds — IF an axe finding forces a component edit, that edit is the legitimate output of verification (a found-and-fixed bug), recorded as such; it is NOT scope_creep because the milestone gates a11y through THIS task.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Usage journey passes axe
  Given UsagePage and SpendPage rendered in their data/success state
  When axe-core scans each container
  Then there are zero serious/critical violations
  And no production component is modified to fake the pass

Scenario: Key journey passes axe
  Given KeysPage (list), CreateKeyDialog (open), KeyGovernanceEditor (open), PlaintextKeyBanner rendered
  When axe-core scans each container
  Then there are zero serious/critical violations
  And the data seam / markers from tasks 2-3 stay unchanged

Scenario: Dialogs are keyboard-operable
  Given each of the 3 /keys dialogs is open
  When the user presses Escape / Tabs through it
  Then focus is trapped inside, Escape closes it, and focus restores to the opener -> else "keyboard_inoperable"
  And the dialog controls remain reachable by role/name

Scenario: Shell landmarks + skip-link present
  Given the AppShell renders
  When the accessibility tree is inspected
  Then a skip-link targets #main and nav[aria-label]/main#main landmarks exist
  And the shell markup is unchanged from task 1

Scenario: Four state patterns render
  Given a stateful surface (e.g. UsageStatsCards / KeysPage / SpendPage)
  When each of loading/empty/error/success is driven
  Then role=status (loading), role=alert (error), the Empty component (empty), and data (success) each render -> else "state_pattern_gap"
  And the shared state components are used (not ad-hoc markup)

Scenario: Responsive utilities applied (static proxy)
  Given the redesigned surfaces
  When their className output is inspected
  Then responsive breakpoint utilities (sm:/md: grid/flex) are present, not fixed pixel widths
  And no surface hardcodes a non-responsive layout

Scenario: Behavioral floor stays green
  Given the full vitest suite at HEAD plus the new verify suite
  When it runs
  Then all tests pass (110 prior + new) and coverage ≥ 80%
  And no production component was changed by this verify task (verify-only) -> else "scope_creep"

Scenario: Browser-only residue declared, not faked
  Given color-contrast (canvas) and true visual breakpoints cannot run in jsdom
  When the gate is recorded
  Then those checks are listed as explicit browser-only residue (non-security), not claimed as passed -> else "unverifiable_claim"
  And every jsdom-verifiable criterion is actually proven by a test

Scenario: No unlisted dependency
  Given the verify suite's imports
  When the node deps allow-list check runs
  Then only already-allow-listed packages (vitest-axe/axe-core/testing-library) are used -> "unlisted_dependency" guarded
  And the allow-list is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# VERIFICATION task — no HTTP contract. This freezes the verification SHAPE: the
# acceptance bar (what counts as PASS) and the explicit residue (what jsdom cannot prove).

VERIFICATION ACCEPTANCE BAR (the gate) ──────────────────────────────────────
  axe(container) on each surface -> ZERO violations of impact "serious" | "critical"
    surfaces: UsagePage · SpendPage(+chart) · KeysPage(list) · CreateKeyDialog(open)
              · KeyGovernanceEditor(open) · PlaintextKeyBanner · AppShell
    ruleset:  axe default (WCAG 2.0/2.1/2.2 A + AA). color-contrast EXCLUDED (jsdom/no-canvas).
  keyboard:   each of the 3 /keys dialogs — focus moves inside on open, traps Tab/Shift-Tab,
              Escape closes, focus restores; AppShell skip-link→#main + nav[aria-label] + main#main.
  states:     each stateful surface renders loading(role=status) · empty(Empty) · error(role=alert) · success(data).
  responsive: redesigned surfaces carry responsive utilities (sm:/md: grid|flex), not fixed px widths.
  floor:      full vitest suite green (110 prior + new) · coverage ≥ 80% · lint clean · NO production
              component changed UNLESS an axe finding forces a scoped a11y fix (recorded as found-and-fixed).

EXPLICIT RESIDUE (browser-only, NON-security — declared, never silently passed) ─
  - color-contrast ratios (axe needs canvas pixel sampling — jsdom has none)
  - true visual rendering across desktop/tablet/mobile breakpoints (jsdom has no layout)
  Verification path: a real-browser axe + viewport pass (Playwright/agent-browser) — a separate
  infra task; tracked as a v13 follow-up / observe delta, not a v13 blocker for the jsdom-provable bar.

Reject codes (build/lint guards, not HTTP): a11y_serious_violation · keyboard_inoperable
  · state_pattern_gap · scope_creep · unverifiable_claim · unlisted_dependency
Schema: NONE TOUCHED. Tests-only task; no DB/route/component/data change (barring a found-and-fixed a11y bug).
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, verification-only)

**Least-sure flag surfaced at freeze:** `[spec]` — the acceptance bar deliberately EXCLUDES color-contrast
and visual-breakpoint rendering as browser-only residue. *Why it's the riskiest call:* a strict reading of
milestone exit criteria #4 ("axe zero serious/critical") and #6 ("renders across breakpoints") could be read
to REQUIRE a real browser, which this env can't run — so the bar proves everything jsdom can and names the
rest as residue. *Cost if wrong:* if the human wants the browser pass INSIDE v13, this task stays open and a
follow-up infra task (Playwright + a stub gateway) is added; nothing already proven is invalidated. Honest
disclosure over a faked green is the entire point of the `unverifiable_claim` reject. Second-most unsure
`[test]`: axe-in-jsdom may surface a REAL structural violation on a populated surface (duplicate id, dialog
w/o name, table w/o caption) — if so it's a found-and-fixed a11y bug (scoped component edit), not a bar change.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% line (the standing v13 gate — held, not raised; this task adds tests, not product code, so coverage must not regress).

RED-FIRST MODEL for a verify task (honest): this suite asserts the milestone gate by axe-scanning
every redesigned surface — something NO existing test does. Two legitimate outcomes when first run:
(a) axe surfaces a REAL serious/critical structural violation on a populated surface (e.g. a dialog
without an accessible name, a duplicate id from repeated rows, a region/list mis-nesting) → the suite is
RED for the RIGHT reason → Build fixes it in the OWNING component (the verify task doing its job, recorded
as found-and-fixed), re-running to green. (b) axe finds nothing → the surfaces were built a11y-correct in
tasks 1-3 and the suite is green on first run — a legitimate no-op Build for a verification pass, recorded
honestly as "floor already compliant", never a faked red. The true-red floor that ALWAYS holds before Build:
the new test files do not exist yet, so their assertions cannot pass. Which of (a)/(b) applies is the §1 ⚠.

Plan (one test per scenario, asserting the milestone gate not internals):
<test_plan>
  LEGACY project — `apps/dashboard/tests/ui-ux-verify.test.tsx`:
  - test_usage_journey_passes_axe: render UsagePage (success state, msw admin/usage+v1/models+admin/budget+auth/me) / axe(container) / assert ZERO serious|critical (color-contrast disabled) + totals data still present (no faked pass)
  - test_keys_list_passes_axe: render KeysPage (msw admin/keys list) / axe(container) / assert ZERO serious|critical + key rows still present
  - test_create_dialog_passes_axe: render CreateKeyDialog isOpen (pure props) / axe / assert ZERO serious|critical + role=dialog accessible name "Create API key"
  - test_plaintext_banner_passes_axe: render PlaintextKeyBanner (pure props) / axe / assert ZERO serious|critical + role=alert present
  - test_shell_landmarks_and_skiplink: render AppShell / assert skip-link href="#main" + nav[aria-label="Primary"] + main#main + axe ZERO serious|critical
  - test_usage_four_state_patterns: drive UsagePage loading(role=status)/error(role=alert)/empty(Empty)/success(data) via msw / assert each renders through the SHARED state component (not ad-hoc)
  - test_responsive_utilities_present_usage_shell: render UsageStatsCards + AppShell / assert responsive utilities in className (UsageStatsCards `sm:grid-cols-4`; AppShell `lg:flex-row`) — not fixed px widths
  - test_create_dialog_keyboard_operable: render CreateKeyDialog isOpen with outside focusables / assert initial focus inside + Escape calls onClose + Tab wraps inside (focus never escapes) — keyboard gate

  BFF project — `apps/dashboard/tests-bff/ui-ux-verify.test.tsx`:
  - test_spend_journey_passes_axe: render SpendPage (msw admin/spend month fixture w/ buckets) / axe(container) / assert ZERO serious|critical + spend-chart figure + bucket list still present
  - test_governance_editor_passes_axe: render KeyGovernanceEditor (apiKey prop, open) / axe / assert ZERO serious|critical + the governance form controls reachable by label
  - test_rotate_confirm_keyboard_operable: open the rotate-confirm dialog inside KeyGovernanceEditor / assert role=dialog accessible name + Escape closes + focus trapped (completes the 3-dialog keyboard gate with create+revoke already covered)
  - test_spend_three_state_patterns: drive SpendPage loading(role=status)/error(role=alert)/zero(spend-zero-state) via msw / assert each renders + responsive `sm:grid-cols-4` on the totals dl in the data state
</test_plan>

Tests live in: `apps/dashboard/tests/ui-ux-verify.test.tsx` `apps/dashboard/tests-bff/ui-ux-verify.test.tsx` · MUST run red (files absent / or a real axe finding) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/tests/` `apps/dashboard/tests-bff/` `apps/dashboard/components/usage/` `apps/dashboard/components/spend/` `apps/dashboard/components/keys/` `apps/dashboard/components/ui/` `apps/dashboard/lib/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/ui-ux-verify/`
<!-- The verify SUITE lives under tests/ + tests-bff/. The component/lib dirs are scoped ONLY so a
     found-and-fixed axe violation can be repaired in its OWNING component (the §1 ⚠ / "verify-only unless
     an axe finding forces a scoped a11y fix"). A non-a11y product change here would be scope_creep.
     .next/ + coverage/ + tsconfig.tsbuildinfo are gitignored tsc/build artifacts the scope-lock still
     flags (engine _SCOPE_EXCLUDE_DIRS = .git/.add/__pycache__/node_modules only). -->
Strategy (ordered batches): 1. write both verify suites RED. 2. run them — observe the actual axe result. 3a. IF a real serious/critical violation: fix the owning component minimally (markup-level, no data/contract change) → re-run green. 3b. IF none: record floor-already-compliant (no-op build). 4. full-suite + coverage + lint gate.
Safety rule (feature-specific): NEVER weaken the acceptance bar to manufacture a pass — a real axe finding is fixed at the source, never excluded; the only legitimately excluded rule is color-contrast (jsdom/no-canvas), already declared as residue.
Code lives in: `apps/dashboard/` (tests under tests/ + tests-bff/; any a11y fix in its owning component).
Constraints: do NOT change any existing test or the contract; allow-list packages only (vitest-axe/axe-core/testing-library already listed); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — **122/122** (110 prior floor + 12 new verify tests), ZERO regression. (`vitest run --coverage` exit 0.)
- [x] coverage did not decrease — **90.3% lines** (≥80% gate held; was 90.21% at task 3). Focus-trap branch coverage ROSE (73.91%→75%) — the new Shift+Tab assertions exercise the previously-uncovered wrap branch.
- [x] no test or contract was altered during build — **build was a verified NO-OP** (verify-only; outcome (b): the surfaces were built a11y-correct in tasks 1-3). The test STRENGTHENING (adversarial-review gaps) was done in the TESTS phase with a clean re-cross tests→build re-snapshot; `git diff` on `components/`+`lib/`+`app/` is EMPTY (only the two new test files are added).
- [x] the green was EARNED, not gamed — **adversarial earned-green refute-read by a subagent (model sonnet)** → VERDICT **EARNED-WITH-GAPS**: helper proven LIVE (mutation test — a deliberately-injected `<img>`-no-alt + empty `<button>` through the SAME `axeSeriousCritical` helper returned a non-empty list containing `image-alt`, then deleted), every axe scan runs on a POPULATED container (each gated by `findBy`/`waitFor`/`getByText` before the scan), no vacuous `toEqual([])`, no crash-swallowing (axe resolves, never throws-to-[]). The 3 ACTIONABLE gaps the reviewer found were CLOSED this loop: (1+2) Shift+Tab wrap now asserted on the Create dialog AND a Tab-trap exercise added to the rotate dialog; (3) the Loading/ErrorState/Empty state renders are now axe-scanned in ISOLATION (usage + spend), not only in the success-state full-page scans. Remaining gap = the responsive class-string check, which IS the declared browser-only residue (jsdom can't lay out) — honest, not a cheat.
- [x] concurrency / timing — N/A (verify-only; no IO mutation, no DB, no shared state). The only async is test-side msw + TanStack Query, handled deterministically with `findBy*`/`waitFor` and `retry:false`; loading-state asserted synchronously pre-resolution.
- [x] no exposed secrets, injection openings, or unexpected dependencies — the PlaintextKeyBanner test uses an obvious FAKE placeholder (`sk-live-PLACEHOLDER-not-a-real-secret`), no real key anywhere; node deps allow-list **34 packages clean** (only vitest/vitest-axe/msw/@tanstack/@testing-library/react — all pre-listed; `unlisted_dependency` satisfied); `next lint` clean.
- [x] layering & dependencies follow CONVENTIONS.md — RTL `within(section)` scoping, the four shared state components (Loading role=status · ErrorState role=alert · Empty · data), allow-listed imports only; the two new verify files are tsc-clean (the pre-existing tsc errors in `bff-client/bff-forms/govern/route-handlers` test files predate this task and are out of verify-only scope).
- [x] a person reviewed and approved the change — Tin (delegated auto mode, verification-only) + the adversarial earned-green subagent as the refute-read.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — both verify suites reference all 12 redesigned surface components + AppShell + the `axeSeriousCritical` helper; every test asserts a REAL rendered element (role/testid/text) before/at the scan. Confirmed by the 12/12 pass + the adversarial per-test population audit.
- [x] DEAD-CODE (code) — no new PRODUCT symbol introduced (verify-only); the throwaway discriminating scratch test was deleted after proving the helper live (`git status` shows only the 2 intended new files).
- [x] SEMANTIC (prose / non-code) — read the frozen §3 acceptance bar IN FULL: confirmed the EXPLICIT RESIDUE (color-contrast pixel sampling + true desktop/tablet/mobile visual rendering) is declared as browser-only / NON-security and NEVER claimed as passed (the `unverifiable_claim` reject guards this). Every jsdom-provable criterion (axe serious|critical, keyboard, 4 states, responsive-utility presence, floor green) is actually proven by a named test.

### MILESTONE EXIT-CRITERIA STATUS (honest accounting — what THIS task proves vs residue)
- PROVEN here (jsdom): #4 axe ZERO serious|critical on all 6 surfaces + AppShell (color-contrast excluded) · keyboard operability of all 3 /keys dialogs (focus-in, Tab + Shift+Tab trap, Escape, restore) · the 4 state patterns via shared components · #6 responsive-utility PRESENCE (sm:/lg: breakpoint classes, not fixed px) · the 122-test behavioral floor green.
- RESIDUE (browser-only, NON-security, declared — NOT a v13 blocker for the jsdom-provable bar): axe color-contrast RATIOS (needs canvas) · true VISUAL rendering across desktop/tablet/mobile viewports (needs a layout engine). Verification path: a real-browser axe+viewport pass (Playwright/agent-browser + a stub gateway) — tracked as a v13 follow-up / observe delta.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto mode) + adversarial earned-green subagent (model sonnet) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the axe-serious|critical count per surface (must stay 0); the keyboard-trap assertions (any regression = a dialog became inoperable); the 122-test floor + 80% coverage gate in CI.
Spec delta for the next loop: the browser-only residue (color-contrast ratios + true visual breakpoints) is the one criterion half this env cannot prove — a follow-up infra task (Playwright/agent-browser + a stub gateway) should close it; until then the residue is honestly carried, not silently passed. The deferred UI/UX surfaces (auth, model catalog, SSO/OIDC, routing-admin, team-governance) are the next UI/UX milestone.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] a VERIFY-ONLY task can be legitimately green-on-first-run (no product code to write); the honest red-first is file-absence, and integrity comes from a DISCRIMINATING MUTATION check — inject a known-critical violation (img-no-alt) through the SAME helper and confirm it's caught — not from manufacturing a red (evidence: img-no-alt → `image-alt` caught, then deleted; the 12/12 green is earned, not vacuous).
- [TDD · folded] axe in jsdom must filter on `impact ∈ {serious,critical}` rather than `toHaveNoViolations()` — the latter fails on MODERATE best-practice rules (region/landmark) that fire when a component is scanned in isolation, masking the real gate; color-contrast must be rule-disabled (no canvas) (evidence: `axeSeriousCritical` filters impact + disables color-contrast; isolated-state scans pass cleanly).
- [UDD · folded] the 4 state patterns + responsive intent are jsdom-verifiable only as PRESENCE proxies (role=status/alert, Empty, `sm:`/`lg:` classes); true contrast + visual breakpoints are browser residue — name the residue under an `unverifiable_claim` reject rather than faking a green (evidence: criterion #4/#6 split into a jsdom-proven half + a declared browser-residue half).
- [ADD · folded] strengthening tests mid-build (after an adversarial review finds coverage gaps) requires going BACK to the tests phase and RE-CROSSING tests→build to re-snapshot the tripwire — editing tests while in build trips `build_tampered` (evidence: phase tests → add Shift+Tab + isolated axe scans → advance re-snapshot → gate clean).
- [ADD · folded] the adversarial earned-green refute-read pays off AGAIN on a verify task: it returned EARNED-WITH-GAPS and surfaced 3 real coverage gaps (Shift+Tab wrap untested on both dialogs, isolated state renders un-scanned) that the green would otherwise have hidden (evidence: all 3 closed this loop, focus-trap branch coverage rose 73.91%→75%).
