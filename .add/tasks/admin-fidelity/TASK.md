# TASK: Apply Aurora language to the admin app shell + pages

slug: admin-fidelity · created: 2026-06-26 · stage: production
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
- `apps/dashboard/components/ui/stat-card.tsx` — `StatCard` (label · value · delta · footer); the KPI tile EVERY admin page composes (Usage/Spend/SLO/overview). Keyed by `data-slot="stat-card"` + optional `valueTestId`; trend is icon + sr-only word (WCAG 1.4.1), color reinforcement only.
- `apps/dashboard/components/ui/app-shell.tsx` — `AppShell` the responsive shell every `(app)` surface inherits. FROZEN v13 contract: skip-link to #main FIRST, single Primary `<nav>`, `<main id="main">`, `lg:flex-row` root.
- The 14 `(app)/app/*` pages + feature components (overview/usage/spend/…) inherit the uplift via these shared primitives + the visual-language tokens — no per-page edits.
Context (working folder):
- Consumes the FROZEN `visual-language` Aurora tokens (shadow/type/radius/motion) + the landing-fidelity precedent (token-only restyle, structure-preserving).
- Admin pages are AUTH-gated (proxy.ts 307→/login without `ai_proxy_session`) + data-fetching → a real authed browser capture needs a gateway+cookie; that stays NAMED browser-only residue (jsdom + component-render tests are the guard, consistent with v13/v14 fold).
Honors (patterns / conventions):
- FROZEN v13 shell contract (skip-link first · single Primary nav · main#main · lg:flex-row) — PRESERVE exactly; restyle className only.
- StatCard a11y: keep `data-slot`, `valueTestId` hook, and the non-color-only trend (icon + sr-only word) intact.
- Presentation-only recipe ([[ui-restyle-recipe]]): token utilities only (R3 no raw hex/px in components/ui), four-state + a11y intact, regression suite is the guard.
Anchors the contract cites:
- `stat-card.tsx` `StatCard` (value/label/delta); `app-shell.tsx` `AppShell` (main canvas + frozen landmarks).
- The frozen `tests/design-system/console-surfaces-redesign.test.tsx` + `enterprise-ext.test.tsx` (data-slot, valueTestId, Primary nav) — stay green.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Apply the Aurora language to the admin app — via the shared StatCard + AppShell primitives.
Framings weighed: shared-primitive uplift (chosen) · per-page restyle of all 14 surfaces · new admin components — the shared-primitive route delivers ONE consistent language to every admin page (the milestone goal) with the least risk; per-page would drift + multiply review surface.
Must:
<must>
  - Elevate `StatCard` to a refined KPI: a larger value (title scale), an uppercase tracked caption label, the trend rendered as a soft pill — keeping `data-slot="stat-card"`, the `valueTestId` hook, and the non-color-only trend (icon + sr-only word) intact.
  - Give the `AppShell` content `<main>` a subtle muted canvas so the elevated white cards read with depth — preserving the FROZEN v13 shell (skip-link FIRST, single Primary nav, `main#main`, `lg:flex-row`).
  - Every `(app)` admin page + feature component inherits the uplift through these shared primitives + the Aurora tokens — NO per-page edits.
</must>
Reject:
<reject>
  - breaking the frozen v13 shell (skip-link order, single Primary nav, main#main, responsive root) -> "frozen_shell_change"
  - dropping StatCard's data-slot, valueTestId, or the non-color-only trend -> "stat_a11y_regression"
  - a raw hex/px literal in a components/ui file (token utilities only) -> "raw_value"
</reject>
After:
<after>
  - the admin shell + every KPI render in the Aurora language; the console-surfaces + enterprise-ext + shell suites stay green; no behaviour/structure change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The frozen StatCard/shell tests assert DOM hooks + landmarks, not type/canvas classes, so the KPI + canvas restyle won't break them — lowest confidence because a label wrapper or canvas div could disturb a queried node; if wrong: the suite breaks immediately (cheap).
  - [ ] a subtle `bg-muted/*` canvas on `main` keeps text contrast within the a11y bar — if wrong: drop the tint (one-line); real-contrast is browser-only residue anyway.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: KPI tile is an elevated, refined stat
  Given a StatCard with a value, label, and a trend delta
  When it renders
  Then the value is at title scale, the label is an uppercase tracked caption, and the trend is a soft pill
  And data-slot="stat-card", the valueTestId hook, and the sr-only trend word are intact

Scenario: Admin canvas gives cards depth
  Given the AppShell content area
  When a page renders inside it
  Then the <main> carries a subtle muted canvas so the white cards read with depth
  And the skip-link is the first focusable element, there is one Primary nav, and main has id="main"

Scenario: Every admin page inherits the language
  Given the 14 (app) admin pages
  When they render after the restyle
  Then each shows the elevated KPI + canvas via the shared primitives with no per-page edit
  And the console-surfaces + enterprise-ext + shell suites stay green

Scenario: Regression guard
  Given the full dashboard suite
  When it runs after the restyle
  Then every existing test stays green
  And no admin page structure, data, or behaviour changed   # required for every rejection
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Presentation contract (no endpoint). The frozen shape = the two primitives restyled + invariants held.

```
SURFACES (restyle className only — token utilities)
  components/ui/stat-card.tsx  -> value@title scale · uppercase tracked caption label · trend as soft pill
  components/ui/app-shell.tsx  -> <main> subtle muted canvas (cards gain depth)
INHERIT (no edit): all 14 (app)/app/* pages + feature components (overview/usage/spend/…)

INVARIANTS (must hold — the regression guard)
  stat-card: keep data-slot="stat-card" · valueTestId hook · trend = icon + sr-only word (non-color-only)
  shell:     skip-link FIRST focusable · single Primary <nav> · <main id="main"> · lg:flex-row root
  global:    token utilities only (R3 no raw hex/px in components/ui) · four-state + a11y intact · no behaviour change
  evidence:  console-surfaces-redesign + enterprise-ext + shell suites GREEN; full suite green
```

Status: FROZEN @ v1 — approved by Tin (auto-mode delegation; consumes the design-confirmed visual-language reference)
Least-sure flag surfaced at freeze: [scenario] the only real risk is the KPI label/canvas restyle disturbing a node the frozen StatCard/shell tests query — caught immediately by console-surfaces + enterprise-ext; if wrong, revert the wrapper (cheap). [contract] the muted canvas must not regress text contrast — browser-only residue; drop the tint if it does.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: regression-hold (console-surfaces + enterprise-ext + shell) + a small admin-fidelity smoke test (new).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_kpi_elevated: render StatCard w/ delta → value node has title-scale type + label is uppercase tracked; data-slot + valueTestId + sr-only trend word intact — RED now (text-2xl, plain label)
  - test_admin_canvas: render AppShell → main#main carries the muted canvas class; skip-link first; single Primary nav — RED now (no canvas)
  - regression: full `npm test` stays green (console-surfaces-redesign + enterprise-ext + all admin suites) — GREEN guard
</test_plan>

Tests live in: `apps/dashboard/tests/design-system/admin-fidelity.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/stat-card.tsx` `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests/design-system/admin-fidelity.test.tsx`
Strategy (ordered batches): 1. red test (admin-fidelity.test.tsx). 2. StatCard: value→title scale, label→uppercase tracked caption, trend→soft pill (a11y intact). 3. AppShell: subtle muted canvas on main (frozen shell intact). 4. full suite green.
Safety rule (feature-specific): presentation-only — preserve the frozen v13 shell (skip-link first, single Primary nav, main#main, lg:flex-row) + StatCard data-slot/valueTestId/non-color-only trend. Token utilities only.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any frozen test or contract; allow-list packages only (no new deps); structure-preserving.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — vitest 514/514 (512 prior + 2 new admin-fidelity)
- [x] coverage did not decrease — presentation-only; +2 tests, 0 removed
- [x] no test or contract was altered during build — only the NEW admin-fidelity.test.tsx authored at §4; frozen console-surfaces/enterprise-ext/shell tests untouched + green
- [x] the green was EARNED — tests render the real primitives + assert DOM (value text-3xl, label uppercase+tracking, data-slot/valueTestId, sr-only trend; main#main canvas + skip-link-first + single Primary nav); corroborated by a REAL-APP capture of /app/usage
- [x] concurrency / timing — N/A (no async/IO changed)
- [x] no exposed secrets / injection / unexpected deps — className-only edits, no new imports/deps
- [x] layering & dependencies follow CONVENTIONS.md — token utilities only; R3 GREEN (no raw hex/px added to components/ui; pill/canvas use bg-*/N opacity utilities)
- [x] reviewed & approved — auto-gate (autonomy: auto), presentation-only, no security/concurrency/arch residue; real-app capture inspected first-hand

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] the admin shell renders on a muted canvas so white cards gain depth, with the indigo-soft active nav item (inset ring) — confirmed in admin-fidelity.png (real `next start` /app/usage capture)
- [x] every (app) page inherits the uplift with no per-page edit — confirmed: only 2 shared primitives touched; the captured Usage page shows elevated cards + canvas + nav without a page edit
- [x] the KPI tile is elevated (title-scale value, uppercase caption, soft-pill trend) with a11y intact — confirmed by test_kpi_elevated + the visual-language.png console preview (live KPI data needs a gateway = browser-only residue)
- [x] frozen v13 shell intact (skip-link first, single Primary nav, main#main, lg:flex-row) — confirmed by test_admin_canvas + shell suite green + `next build` exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — restyled DELTA_META.tone now carries bg+text (pill), consumed at the trend render; main canvas class rendered (seen in capture)
- [x] DEAD-CODE (code) — no new symbol/export; className edits only, all rendered
- [x] SEMANTIC — StatCard a11y (data-slot, valueTestId, icon + sr-only trend word) + the v13 shell landmarks read in full and unchanged; only presentation classes differ

### GATE RECORD
Outcome: PASS
Evidence: vitest 514/514 (2 new admin-fidelity, RED→GREEN) · tsc --noEmit clean · `next build` exit 0 (31 routes) · R3/tokens guard GREEN · real-app capture admin-fidelity.png (/app/usage) inspected first-hand. Frozen StatCard a11y + v13 shell landmarks preserved. Live KPI data = NAMED browser-only residue (needs authed gateway).
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: auto-gate (autonomy: auto; presentation-only, no security/concurrency/arch residue) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): admin-fidelity.test.tsx (KPI + canvas) + console-surfaces/enterprise-ext/shell suites stay green on every future change; admin-fidelity.png is the shell visual baseline.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] a live authed admin capture (real gateway + session) would show the elevated KPI tiles with real data — the milestone closed on component-render + a dataless real-shell capture; stand up an e2e capture harness for a full visual baseline. Evidence: /app/usage capture showed "Request failed" states (no gateway).
- [SPEC · open] per-page headers across the 14 (app) surfaces could share a PageHeader primitive for a consistent title/description/action treatment — currently each feature component renders its own header. Evidence: only shared StatCard/AppShell were touched here.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [UDD · open] uplifting two shared primitives (StatCard + AppShell) propagates one consistent language to all 14 admin surfaces with no per-page edit — the cheapest path to the milestone's "consistent fidelity" goal (evidence: 514 green touching 2 files; the /app/usage capture shows the canvas+nav+card uplift on an untouched page).
- [TDD · open] an auth-gated, data-fetching surface is still verifiable: component-render tests for the primitives + a cookie-seeded real-shell capture (dataless) prove the chrome, with live-data KPIs declared as honest browser-only residue (evidence: cookie=capture-only rendered the shell; data states = "Request failed").
- [ADD · open] the milestone goal (every surface elevated from ONE language) is met by the token-graph + 2-primitive strategy, NOT by editing N pages — bias future "apply the design" tasks toward the shared seam first (evidence: visual-language tokens + 6 primitive/2-surface edits covered admin+landing+auth).
