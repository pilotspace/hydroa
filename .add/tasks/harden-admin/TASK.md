# TASK: Apply motion + verify failure/loading/a11y states across admin routes

slug: harden-admin · created: 2026-06-26 · stage: production
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
- `apps/dashboard/components/ui/app-shell.tsx` : AppShell — the SHARED authed chrome. Its `<main id="main">` (line 181) renders `{children}` directly. ADD a `Reveal` wrap keyed by `activePath` so each of the 13 admin routes gets a progressive entrance that RE-TRIGGERS on navigation. `Reveal` is imported from `./motion` (sibling, task 5). AppShell is a client component (sidebar useState); Reveal is hookless → safe.
- `apps/dashboard/app/(app)/app/error.tsx` + `loading.tsx` : EXIST (task 4) — the /app-subtree failure + loading boundary. All 13 admin routes (`/app`, `/app/{alerts,audit,health,keys,members,models,routing,settings,slo,spend,teams,usage}`) inherit them. This task VERIFIES that coverage; it does not re-add.

Context (working folder):
- The 13 admin routes are all `○` Static leaves rendering a component under DashboardShell→AppShell. Data-fetch + loading/error/empty states already live in each feature component (states.tsx + bff-client, task 1 resilient fetch). So harden-admin's NET-NEW = motion (Reveal entrance) + a verification net; resilience/failure/a11y are foundation-delivered.
- `Reveal` from `./motion` / `@/components/ui` barrel (task 5). `activePath` already a prop of AppShell (route highlight).

Honors (patterns / conventions):
- Aurora language; reduced-motion safe (Reveal is `motion-safe:` only + the global net). No copy/behaviour change. Shell owns the entrance once → uniform across all admin routes (no per-page churn).

Anchors the contract cites: `AppShell` `<main>` `Reveal` (keyed by `activePath`), the existing `(app)/app/error.tsx`+`loading.tsx` boundary.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Progressive entrance motion across all admin routes + verified failure/loading coverage
Framings weighed: wrap once in the shared AppShell main, keyed by route (chosen) · wrap each of 13 pages individually (rejected — churn, drift) · a route-transition library (rejected — new dep, overkill)
Must:
<must>
  - M1 AppShell wraps its `<main>` content in `Reveal` keyed by `activePath`, so every admin route shows a subtle entrance that RE-TRIGGERS on navigation. Children render unconditionally (Reveal passthrough).
  - M2 Under reduced motion, content appears immediately with no movement (Reveal is `motion-safe:`-gated + the global net). No layout shift, no copy change.
  - M3 The admin route group retains its failure boundary (`(app)/app/error.tsx`) and loading boundary (`(app)/app/loading.tsx`) — verified present, covering all 13 routes.
  - M4 No existing AppShell/admin test or behaviour changes; full suite green; the `<main id="main">` landmark and its children are unchanged structurally.
</must>
Reject:
<reject>
  - motion that defers or hides admin content -> Reveal renders children unconditionally (M1/M2) — the verify test asserts children present
  - a missing admin failure/loading boundary -> the coverage test FAILS (M3)
  - a second Primary landmark or lost `<main>` -> existing AppShell a11y/landmark tests FAIL (M4)
</reject>
After:
<after>
  - Every admin route enters with a subtle, route-keyed motion-safe entrance that vanishes under reduced motion; the failure + loading boundaries verifiably cover all 13 routes; nothing structural/behavioural changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Wrapping AppShell's `<main>` children in a keyed `Reveal` div doesn't break the suite's landmark/a11y/sidebar tests — lowest confidence because AppShell is a heavily-tested shared primitive; if wrong: those tests fail and I reconcile (Reveal renders a plain div wrapper inside `<main>`, landmark unchanged). Confirmed by running the full suite.
  - [ ] `activePath` is always defined enough to key on — it's optional; fall back to a stable key when undefined so SSR/first-render is stable.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 admin content renders inside a keyed Reveal
  Given AppShell rendered with activePath="/app/keys" and child content
  When mounted
  Then the child content is present inside the <main> landmark (Reveal passthrough)

Scenario: M1 entrance re-triggers on route change
  Given AppShell rendered at activePath="/app"
  When re-rendered at activePath="/app/usage"
  Then the Reveal wrapper is keyed by activePath (remounts → re-animates)

Scenario: M3 admin failure + loading boundaries cover the group
  Given the (app)/app route segment
  When inspected
  Then app/(app)/app/error.tsx and app/(app)/app/loading.tsx both exist

Scenario: M4 single landmark preserved
  Given AppShell rendered
  When queried
  Then exactly one <main id="main"> landmark exists and existing AppShell tests stay green
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// components/ui/app-shell.tsx — inside <main id="main">:
import { Reveal } from "./motion";
<main id="main" className="flex-1 p-4 lg:p-8">
  <Reveal key={activePath ?? "shell"} className="h-full">
    {children}
  </Reveal>
</main>
// Reveal: motion-safe fade/slide-up entrance; children unconditional; key=activePath re-triggers on nav.
// No prop/landmark change; failure+loading boundaries (app/(app)/app/{error,loading}.tsx) untouched (verified, not edited).
```

Schema: none — a presentational wrapper inside the existing landmark. No DB/network/dep/prop change.

Least-sure flag surfaced at freeze: [test] the keyed `Reveal` div inside `<main>` must not perturb AppShell's landmark/sidebar/a11y tests — verified by the full suite. Cost if wrong: low, reconcile the wrap. · [contract] `activePath` is optional → key falls back to `"shell"` so first render is stable.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; additive motion in the shared shell, reduced-motion safe, no behaviour change)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — the admin entrance + boundary coverage net.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_admin_content_renders_in_shell: render AppShell with activePath + child → child text present inside the main landmark.
  - test_main_landmark_single: render AppShell → exactly one element with role main / id="main".
  - test_admin_failure_loading_boundaries_exist: assert app/(app)/app/error.tsx AND loading.tsx exist on disk (fs check).
  - (re-trigger keying is covered structurally by the keyed wrapper; asserted indirectly via content-present after activePath change render.)
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/admin-hardening.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/components/ui/motion.tsx` `apps/dashboard/tests/admin-hardening.test.tsx`
Strategy (ordered batches): 1. add `data-slot="reveal"` marker to Reveal (clean test hook + DS convention). 2. wrap AppShell `<main>` children in `<Reveal key={activePath ?? "shell"}>`. 3. green.
Safety rule (feature-specific): children render unconditionally; landmark + props unchanged; failure/loading boundary files NOT edited (verify-only).
Code lives in: `apps/dashboard/components/ui/`
Constraints: do NOT change any test or the contract; NO new dep; no copy/behaviour change; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 562 green (70 files); +4 new admin-hardening tests
- [x] coverage did not decrease — additive (562 from 558); no behavioural test removed
- [x] no test or contract was altered during build — only a `data-slot` marker + a Reveal wrap added; no existing test touched
- [x] the green was EARNED — the test asserts the admin content renders INSIDE a `[data-slot="reveal"]` within the single `<main>` landmark (not a vacuous truthy), and the failure/loading boundaries are asserted to exist on disk. Was RED on the marker before the wrap. Presentation/motion, no logic → no subagent refute-read
- [x] concurrency / timing safe — N/A: a keyed presentational wrapper; no async
- [x] no exposed secrets, injection openings, or unexpected dependencies — ZERO new deps; no data flow
- [x] layering & dependencies follow CONVENTIONS.md — Reveal (DS primitive) reused in the shared shell; `data-slot` marker matches the design-system convention
- [x] a person reviewed — Tin approved the freeze; additive motion, reduced-motion safe. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Admin content renders inside a route-keyed Reveal — confirmed: `test_admin_content_renders_in_keyed_reveal` (content present in `main [data-slot="reveal"]`)
- [x] Exactly one `<main id="main">` landmark preserved — confirmed: `test_main_landmark_single` + existing AppShell landmark/a11y/sidebar tests stay green (562 suite)
- [x] Failure + loading boundaries cover the group — confirmed: `test_admin_failure_loading_boundaries_exist` (fs: `(app)/app/error.tsx` + `loading.tsx`)
- [x] Admin routes stay statically rendered — confirmed: `next build` shows 13 `○ /app*` routes, compile ✓
- [x] No regression — 562 green, tsc 0, eslint 0 errors, next build exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `Reveal` imported into AppShell and used in `<main>`; `data-slot="reveal"` consumed by the test + becomes the live marker.
- [x] DEAD-CODE — no orphan; the Reveal import is used; `data-slot` is additive on an existing-used primitive.
- [x] SEMANTIC — re-read the AppShell main region: landmark id/className unchanged, children unconditional, key falls back to `"shell"` when activePath is undefined.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · auto-resolved under autonomy:auto (additive motion/verification) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-route a11y in a real browser (jsdom can't measure contrast/focus-visible); that the entrance feels subtle, not janky, on slow admin pages.

### Spec delta
- [SPEC · open] Stagger child sections within heavy admin pages (usage/spend dashboards) with `Reveal delay=` for a richer cascade — this task does shell-level entrance only (evidence: one Reveal wraps the whole route; per-section delay unused).
- [SPEC · open] EC5 real-browser axe/playwright in CI is org-billing-blocked (same as task 6) — the jsdom landmark/a11y net is the in-CI gate; per-route browser audit is the deferred half (evidence: 13 routes not browser-audited).

### Competency deltas
- [UDD · folded] Owning the route entrance ONCE in the shared shell (keyed by activePath) beats wrapping N pages — uniform motion, zero per-page churn, re-triggers on nav via React key remount (evidence: 13 routes covered by one wrap). [folded foundation-version 37]
- [TDD · folded] A `data-slot` marker on a presentational primitive gives a clean, non-brittle test hook (vs matching Tailwind class strings) and doubles as a DS adoption marker (evidence: admin test asserts `[data-slot="reveal"]`, red before the wrap). [folded foundation-version 37]
