# TASK: v15 milestone-exit verification (a11y/keyboard/state/responsive across new surfaces)

slug: feature-coverage-verify · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): a milestone-EXIT verification — ONE consolidated cross-surface verify suite; NO production change expected (each v15 surface was already gate-PASSed with its own axe/keyboard/state tests). Verified anchors:

NEW v15 SURFACES (each already has a dedicated RTL suite — this task proves they meet the bar TOGETHER):
- `components/models/ModelsPage.tsx` (/models) — model-mgmt.test.tsx
- `components/teams/TeamsPage.tsx` (/teams) — teams-governance.test.tsx
- `components/routing/RoutingPage.tsx` (/routing) — routing-health.test.tsx
- `components/settings/*` (/settings tabbed: Cache/Guardrails/OIDC) — tenant-settings.test.tsx
- `components/keys/KeyGovernanceEditor.tsx` depth + `components/spend/SpendPage.tsx` depth — govern-depth.test.tsx + spend-breakdown.test.tsx (v13 ui-ux-verify already sweeps the spend + key-editor surfaces)
- `app/api/auth/oidc/login/route.ts` SSO relay + LoginForm SSO link — oidc-login-relay.test.tsx

SHELL (`components/ui/app-shell.tsx`): a11y contract = a skip-link to #main as the FIRST focusable element, a Primary `<nav aria-label="Primary">` landmark, a `<main id="main">` landmark, 7 nav links (Usage/Spend/API Keys/Models/Teams/Routing/Settings) each keyboard-focusable with aria-current on the active one. PRESENTATIONAL (takes activePath; does NOT read role — see role-filter residue below).

ROLE / RBAC: `lib/hooks/use-current-user.ts` exposes `role` from GET /api/auth/me. The NAV is NOT role-filtered today — a `member` sees admin-only links (/models,/teams,/routing,/settings) that 403 on navigate. This is a UX nicety, NOT a security hole (the gateway enforces RBAC → 403 → each surface renders ErrorState). The role→surface visibility mapping is an unspecified product decision → DECLARED RESIDUE (a future `nav-role-filter` task), not guessed here.

Test harness (tests-bff/): RTL + msw at http://localhost:3000/api/gw/...; fresh QueryClient per test; `axeSeriousCritical(container)` = axe with color-contrast disabled (jsdom has no canvas), filtered to serious|critical. Current suite: 28 files / 223 tests green / 94.03% cov / next lint clean.

Context (working folder): v15 MILESTONE.md feature-coverage-verify (the LAST v15 task — milestone-exit gate; same shape as v13 ui-ux-verify).

Honors (patterns / conventions): the v13 ui-ux-verify pattern (consolidated axe + state + keyboard sweep); the four state patterns (loading=role=status · empty=Empty · error=role=alert · success=data); the v13/v15 a11y bar (WCAG 2.2 AA: labelled controls, keyboard-operable, focus-visible, skip-link, landmarks); the carried browser-only residue convention (color-contrast ratios + true viewport rendering are jsdom-unprovable — declared, not faked).

Anchors the contract cites: a NEW consolidated `tests-bff/feature-coverage-verify.test.tsx` (AppShell a11y + an axe/state/keyboard sweep over ModelsPage/TeamsPage/RoutingPage/settings tabs) · `axeSeriousCritical` · the four state patterns · the declared residue (browser-only axe/viewport + role-filtered NAV).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: v15 milestone-exit verification — a consolidated cross-surface gate proving every new v15 surface meets the WCAG 2.2 AA + four-state + responsive bar together, and the full behavioral suite stays green, with the browser-only residue + role-filtered-NAV honestly declared.
Framings weighed: One consolidated verify suite + AppShell a11y, leaning on the per-surface suites already green (chosen — mirrors v13 ui-ux-verify; proves the milestone bar without re-testing every branch) · Re-test every surface end-to-end here (rejected — duplicates 8 green suites, no added signal) · Implement role-filtered NAV now (rejected — unspecified role→surface mapping, no security hole; declared residue).
Must:
<must>
  - The full dashboard suite passes with coverage ≥80% (the global gate) and next lint is clean — the milestone's behavioral floor holds.
  - AppShell a11y: a skip-link to #main is the FIRST focusable element; a Primary nav landmark + a main landmark exist; all 7 nav links render and are keyboard-focusable; axe zero serious/critical.
  - Each NEW v15 surface (ModelsPage, TeamsPage, RoutingPage, the settings tabs) renders with data and is axe-clean (zero serious/critical, color-contrast excluded) AND exposes its four state patterns (a representative loading/empty/error/success path is reachable) AND a representative primary control is keyboard-operable + labelled.
  - The browser-only residue (axe color-contrast ratios + true visual breakpoint rendering) is DECLARED, not faked; the role-filtered-NAV gap is DECLARED as a follow-up (no security hole — gateway 403 + per-surface ErrorState).
  - No DATA-contract change to any consumed admin endpoint; no new dependency.
</must>
Reject:
<reject>
  - A new surface with a serious/critical axe violation (missing label, no landmark, bad role) -> the gate FAILS, fix before PASS -> "a11y_regression"
  - A primary control reachable only by mouse (not keyboard-focusable) -> "keyboard_trap_or_unreachable"
  - Faking the browser-only residue (asserting a real color-contrast ratio or pixel breakpoint in jsdom) -> WRONG; declare it -> "faked_residue"
  - Silently shipping the role-filtered-NAV gap with no record -> "undocumented_gap"
  - Any change to a consumed gateway/BFF data contract sneaked in under "verification" -> "scope_creep"
</reject>
After:
<after>
  - The full suite is green at ≥80% coverage + lint clean; AppShell + every new v15 surface is axe-clean, keyboard-operable, and shows the four state patterns; the browser-only residue + the role-filtered-NAV follow-up are recorded; v15's exit criteria are all satisfiable from the evidence.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ jsdom axe coverage is a PROXY for real accessibility — color-contrast + true viewport/visual rendering cannot be proven in jsdom (no canvas/layout). Lowest confidence because a surface could pass the jsdom bar yet have a real-browser contrast/overflow issue. Cost if wrong: a visual a11y defect ships undetected. Mitigation: assert everything jsdom CAN prove (roles, labels, landmarks, focusability, responsive utility classes) + carry the real-browser axe+viewport pass as the SAME declared follow-up shared with v13 (not re-litigated per task).
  - [ ] each new page renders standalone under a QueryClientProvider + msw (the per-surface suites already do this) — confirmed.
  - [ ] role-filtered NAV is UX-only, not security (gateway 403 enforces RBAC) — confirmed (every admin surface hits /admin/* which the gateway guards).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Full suite + coverage gate holds
  Given the complete v15 dashboard
  When vitest run --coverage + next lint run
  Then all tests pass, coverage ≥80%, lint clean

Scenario: AppShell is accessible
  Given the AppShell renders
  When axe runs and a keyboard user tabs
  Then the skip-link is first focusable (href #main), Primary nav + main landmarks exist, all 7 nav links are focusable, zero serious/critical axe

Scenario: Each new surface is axe-clean with data
  Given ModelsPage / TeamsPage / RoutingPage / the settings tabs render with data
  When axe runs
  Then each has zero serious/critical violations

Scenario: Each new surface is keyboard-operable + labelled
  Given a new surface with its primary control (model Switch / team action / routing read view / settings tab+toggle)
  When a keyboard user reaches it
  Then the control has an accessible name and is focusable

Scenario: Four state patterns reachable
  Given a new surface
  When its query is loading / empty / error / success
  Then the matching pattern renders (role=status / Empty / role=alert / data)

Scenario: Residue + role-filter declared, not faked
  Given jsdom cannot prove color-contrast / pixel breakpoints, and the NAV is not role-filtered
  When the gate is recorded
  Then both are DECLARED as follow-ups (no faked assertion, no undocumented gap)
  And no consumed data contract changed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
VERIFICATION CONTRACT (no runtime surface — a milestone-exit gate; same shape as v13 ui-ux-verify)

NEW SUITE  tests-bff/feature-coverage-verify.test.tsx:
  AppShell (components/ui/app-shell.tsx):
    - skip-link: getByRole("link", {name:/skip to main/i}) has href "#main" and is the FIRST <a> in the tree
    - landmarks: getByRole("navigation", {name:/primary/i}) + the <main id="main"> present
    - all 7 nav links render (Usage/Spend/API Keys/Models/Teams/Routing/Settings) as role=link, each focusable
    - axeSeriousCritical(container) === []
  Per new surface — ModelsPage · TeamsPage · RoutingPage · TenantSettings tabs (rendered with msw data + QueryClientProvider):
    - axeSeriousCritical(container) === []   (data-loaded state)
    - a representative primary control is reachable by role + accessible name (Switch / button / tab) and focusable
  Four-state spot-check: at least one surface asserts loading (role=status) AND error (role=alert) renders the right pattern
    (the exhaustive per-state coverage already lives in each surface's own suite — this is the consolidated milestone gate)

GATE EVIDENCE (recorded in §6, not a test):
  - `vitest run --coverage` EXIT=0, coverage ≥80%   ·   `next lint` clean   ·   production tsc-clean for any touched file

DECLARED RESIDUE (NOT faked, NOT a gap to fix here):
  - browser-only: axe color-contrast ratios + true visual breakpoint rendering (jsdom has no canvas/layout) — shared
    real-browser axe+viewport follow-up (carried from v13, not re-litigated)
  - role-filtered NAV: a `member` sees admin-only nav links that 403 on navigate — UX-only (gateway enforces RBAC;
    each surface ErrorStates) → follow-up task `nav-role-filter` (role→surface mapping is an unspecified product decision)

NO DATA-contract change to any consumed admin endpoint. NO new dependency.
```

Least-sure flag surfaced at freeze: [test] jsdom axe is a PROXY — it proves roles/labels/landmarks/focusability but NOT color-contrast or true viewport rendering. Why least-sure: a surface can pass the jsdom bar yet have a real-browser contrast/overflow defect. Cost if wrong: a visual a11y issue ships. Decision (auto): assert everything jsdom can prove + carry the real-browser axe+viewport pass as the SAME declared follow-up shared with v13 (not faked, not re-litigated). Secondary [spec]: role-filtered NAV is declared residue (a `nav-role-filter` follow-up), not built — no security hole (gateway 403), and the role→surface visibility mapping is an unspecified product decision (don't guess).

Status: FROZEN @ v1 — approved by ADD auto (autonomy=auto; verification-only, no runtime/data-contract change; the two residues are honestly declared follow-ups, not security gaps; mirrors the v13 ui-ux-verify gate that already closed v13).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the dashboard global gate must still hold milestone-wide).
Plan (RTL + msw; fresh QueryClient/test; `axeSeriousCritical` helper):
<test_plan>
  tests-bff/feature-coverage-verify.test.tsx:
  - test_appshell_skiplink_is_first_focusable: render AppShell → first <a> is "Skip to main content" with href "#main"
  - test_appshell_landmarks_and_nav_links: navigation(name=Primary) + main#main present; all 7 nav links render as role=link + focusable
  - test_appshell_axe_clean: axeSeriousCritical === []
  - test_models_surface_axe_and_control: ModelsPage (msw data) axe clean + the enable/disable Switch has an accessible name + is focusable
  - test_teams_surface_axe_and_control: TeamsPage (msw data) axe clean + a primary action control reachable by name
  - test_routing_surface_axe: RoutingPage (msw data) axe clean (read-only) + a region/heading reachable
  - test_settings_surface_axe_and_tabs: TenantSettings (msw data) axe clean + the tablist + first tab reachable by role/name
  - test_state_patterns_spotcheck: a surface renders role=status while loading AND role=alert on error (the four-state floor)
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` (NEW feature-coverage-verify.test.tsx) · MUST run red (file absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/tests-bff/` `apps/dashboard/components/ui/` `apps/dashboard/components/models/` `apps/dashboard/components/teams/` `apps/dashboard/components/routing/` `apps/dashboard/components/settings/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/feature-coverage-verify/`
<!-- SCOPE NOTE: a verification task — the ONLY expected write is the NEW tests-bff/feature-coverage-verify.test.tsx. The component dirs are declared ONLY so a real a11y DEFECT surfaced by the consolidated axe sweep can be fixed in place (no fix is anticipated — every surface already gate-PASSed its own axe). .next/coverage/tsbuildinfo are verify artifacts (coverage gitignored). NO gateway/BFF/data change, NO new dependency, NO role-filtered-NAV build (declared residue). -->
Strategy (ordered batches): 1. RED suite (feature-coverage-verify.test.tsx). 2. If — and only if — the consolidated axe/keyboard sweep flags a real a11y DEFECT, fix it in the owning component. 3. full vitest --coverage + next lint green; record the gate + the two declared residues.
Safety rule (feature-specific): assert ONLY what jsdom can prove (roles/labels/landmarks/focusability/utility-classes); never fake a color-contrast ratio or pixel breakpoint — declare those. Do not implement role-filtered NAV (declared follow-up). Touch a component ONLY to fix a real surfaced a11y defect.
Code lives in: `apps/dashboard/tests-bff/` (+ a v15 component dir only if a real a11y fix is needed)
Constraints: do NOT change any other test or the contract; reuse axeSeriousCritical + the per-surface render setups; NO new dependency; NO data-contract change.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 29 files / 231 tests green (was 28/223; +1 file, +8 tests)
- [x] coverage did not decrease — 94.03% (≥80% global gate; `vitest run --coverage` EXIT=0); app-shell.tsx 100%
- [x] no test or contract was altered during build — the ONLY write is the NEW tests-bff/feature-coverage-verify.test.tsx (no production code touched — no a11y defect surfaced); §3 untouched. The post-audit strengthening was an honest re-cross (verify→tests→build→verify), never an in-place build edit.
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) ran. Verdict NOT-EARNED on first pass; the 3 substantive findings were triaged: D1 (skip-link "first focusable" proven for anchors only) FIXED → now queries ALL focusable types; D2 (loading assert untethered) FIXED → now proves spinner RESOLVES to data + role=status clears; D5 (shared `bffHandlers` `/api/gw/:path*` wildcard defeats onUnhandledRequest:"error") is NOT a cheat in this file — every surface test registers an explicit `server.use()` and asserts on real fixture content (GPT-4o/platform/retry policy/response cache), provably served by the specific handler not the keys fallback; hardening the SHARED fallback risks the 28 existing suites that depend on it → declared follow-up (§7), not snuck under a verification gate. Also fixed G1 (negative aria-current on inactive links) + G4 (full switch accessible name pinned). Re-ran strengthened suite: 8/8 green for the right reasons.
- [x] concurrency / timing — N/A (read-only render-time verification; no shared mutable state, no IO write); the four-state spot-check asserts loading→data transition (no race).
- [x] no exposed secrets, injection openings, or unexpected dependencies — fixtures only; OIDC client_secret never touched here (settings axe uses the cache tab); no new dependency (reuses vitest-axe / RTL / msw).
- [x] layering & dependencies follow CONVENTIONS.md — a test-only file in tests-bff/; reuses the established axeSeriousCritical helper + per-surface render/fixture idioms; no cross-layer reach.
- [x] a person reviewed and approved the change — ADD auto-gate (autonomy=auto): verification-only, no runtime/data-contract change, no security finding; the two residues are honestly declared follow-ups. (Human visibility at v15 milestone-close report.)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the new suite imports & renders every claimed symbol: AppShell (@/components/ui), ModelsPage, TeamsPage, RoutingPage, SettingsPage; each role/name asserted was confirmed against the owning component (app-shell.tsx skip-link+Primary nav+main#main+7 links+aria-current; ModelsPage `aria-label={`Enable ${name}`}`; SettingsPage tablist tabs Cache/Guardrails/SSO).
- [x] DEAD-CODE (code) — no new production symbol introduced (test-only file); no unused import (all four pages + AppShell + axe + msw used).
- [x] SEMANTIC (prose / non-code) — read in full: the refute-read report (7 findings) was read assertion-by-assertion, each triaged DEFECT/GAP/NIT with a fix-or-defer decision recorded above; the wildcard claim was verified directly against tests-bff/mocks/handlers.ts:119-133.

### GATE RECORD
Outcome: PASS
Evidence: `vitest run --coverage` EXIT=0 · 231/231 green · 94.03% coverage (≥80%) · `next lint` clean · new file tsc-clean · adversarial refute-read run, 4 findings fixed in-file, 1 (shared-harness wildcard) deferred as a documented follow-up (no cheat in this file).
Reviewed by: ADD auto-gate (autonomy=auto) · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the consolidated axe/keyboard/state sweep is the milestone-exit regression monitor — a serious|critical axe finding or a missing state pattern on any v15 surface re-opens the gate. Carried browser-only monitor: a real-browser axe + viewport pass (color-contrast ratios + true breakpoint layout) — jsdom-unprovable.
Spec delta for the next loop: the v15 dashboard reaches the WCAG 2.2 AA + four-state bar across all surfaces in jsdom; the remaining a11y truth (contrast, overflow) needs a real-browser harness — a standing UDD/TDD residue shared with v13, not a per-task gap.

DECLARED RESIDUE / FOLLOW-UPS (honest, not faked, not fixed here):
- [browser-only axe+viewport] color-contrast ratios + true visual breakpoint rendering are jsdom-unprovable (no canvas/layout) → shared real-browser axe+viewport pass (carried from v13, not re-litigated per task).
- [nav-role-filter] a `member` sees admin-only nav links (/models,/teams,/routing,/settings) that 403 on navigate — UX-only, NOT a security hole (the gateway enforces RBAC → 403 → each surface ErrorStates). The role→surface visibility mapping is an unspecified product decision → future `nav-role-filter` task, not guessed here.
- [test-harness hardening] the shared `tests-bff/mocks/handlers.ts` `/api/gw/:path*` GET/POST/PUT/DELETE wildcards (lines 119-133) return generic key/budget fixtures, which means a test that FORGETS its `server.use()` silently gets wrong-shaped data instead of the `onUnhandledRequest:"error"` failure the suite assumes. Surfaced by the v15-exit refute-read. Not a defect in any current suite (all register explicit handlers + assert on real fixture content), and not fixed under this verification gate (the 28 existing suites depend on the keys fallback) → follow-up `bff-test-harness-strict-handlers` (scope the wildcards or drop them so the no-handler guard becomes load-bearing).

### Competency deltas
- [TDD · open] a "loading shows role=status" assertion is vacuous unless it also proves the spinner RESOLVES (a permanent role=status node would pass it); assert the loading→data transition (evidence: refute-read D2 — fixed by adding `findByText` + `queryByRole("status").not...` after the T=0 assert).
- [TDD · open] a "skip-link is first focusable" assertion via `querySelector("a")` only proves first ANCHOR; a preceding focusable button/input/[tabindex] would slip through — query ALL focusable types to match the WCAG Must (evidence: refute-read D1 — fixed).
- [TDD · open] a permissive shared msw wildcard (`/api/gw/:path*`) silently defeats `onUnhandledRequest:"error"`; a forgotten per-test handler returns wrong data, not a loud failure — scope mock fallbacks to the paths that truly need them (evidence: refute-read D5 — deferred to `bff-test-harness-strict-handlers`).
- [UDD · open] jsdom axe is a PROXY: it proves roles/labels/landmarks/focusability but never color-contrast or true viewport layout; the real-browser a11y pass must be a standing milestone residue, not re-litigated per task (evidence: this gate + the v13 ui-ux-verify gate share the identical carried follow-up).
- [ADD · open] a milestone-EXIT verification suite legitimately lands GREEN, not RED (the behavior already shipped + gate-PASSed per-surface); "RED for the right reason" maps to "the consolidated bar is newly codified and provably held," with the earned-green proven by an adversarial refute-read rather than a first-run failure (evidence: 8/8 green on first run, then hardened after audit).
