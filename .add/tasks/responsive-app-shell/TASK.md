# TASK: Full-height sidebar + responsive AppShell across breakpoints

slug: responsive-app-shell · created: 2026-06-28 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
- `apps/dashboard/components/ui/app-shell.tsx:AppShell` — root `flex min-h-screen flex-col lg:flex-row`; desktop rail `hidden lg:flex lg:sticky lg:top-0 lg:h-screen` (`w-64` / `w-16` collapsed); mobile header `lg:hidden` + a `Dialog` left-drawer sheet (`DialogContent left-0 top-0 h-full max-w-xs`); `collapsed` useState; 18 `NAV_ITEMS`; `handleLogout` (window.location, not useRouter — see Honors). **Already full-height on desktop** (lg:h-screen sticky), so todo #1 is NOT a missing h-screen.
- `apps/dashboard/components/ui/sidebar.tsx` — `Sidebar` (`flex h-full w-64 shrink-0 flex-col border-r`); `SidebarContent` (`flex-1 overflow-y-auto p-2` — the nav scrolls internally when 18 items overflow a short viewport); `SidebarHeader`/`SidebarFooter`/`SidebarItem`/`SidebarTrigger`.
- `apps/dashboard/components/ui/dialog.tsx` — the mobile-sheet primitive (focus-trap, Escape, overlay).

Context (working folder):
- todos #1 (fix sidebar full height) + #3 (responsive/mobile pass) — this task owns AppShell (so #1 folds in, single owner; the per-page redesign tasks consume the result).
- v54 captures `tmp/captures/*_top.png` — desktop rail renders full-height with footer pinned; with 18 items + a short viewport the nav scrolls (only ~11 items visible before scroll).
- Behavioral floor to keep green: `tests/design-system/app-shell-sidebar.test.tsx`, `tests-bff/app-shell-logout.test.tsx`, and any test asserting the frozen v13 shell contract.

Honors (patterns / conventions):
- **FROZEN v13 shell contract** (must be preserved): skip-link to #main is the FIRST focusable element; exactly ONE Primary `<nav>` landmark by default (mobile sheet's nav is labelled "Site" and only mounted when open); a `<main id="main">` landmark; responsive `lg:flex-row` root (stacked → row from lg).
- **window.location, not next/navigation** — the legacy app-shell test mocks next/navigation WITHOUT useRouter, so the shell must not call useRouter.
- **Token-only styling (R3)**, four UI states, a11y by construction (collapsed item keeps its accessible name via sr-only label).

Anchors the contract cites:
- `AppShell` (the responsive layout + sidebar height behavior), `Sidebar`/`SidebarContent` (height + overflow), the breakpoint classes (`lg:*`), `DialogContent` (mobile sheet height).
- ⚠ Pending the human design-intake below: the SPECIFIC "full height" defect + the desired responsive behavior are not derivable from code (the desktop rail already implements full height). §1 will be drafted after that clarification.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Bulletproof full-height desktop shell + fluid large-screen layout
Framings weighed: fixed-viewport app shell + fluid content with scaled gutters (chosen) · keep the sticky rail and add a max-width cap (rejected — Tin chose re-architect for guaranteed height + no width cap) · full shell rebuild (rejected — "polish current behavior": keep drawer<lg / rail-from-lg)
Must:
<must>
  - At lg+ the layout is a FIXED-VIEWPORT app shell: the lg:flex-row container is exactly the viewport height and clips its own overflow, so the desktop rail ALWAYS fills 100% of viewport height — no sticky dependency, no gap on any browser/zoom (the "~75%" report, defensively).
  - At lg+ ONLY <main> scrolls (its own scroll region); the rail and the rail footer (identity + logout) stay put while content scrolls.
  - Page content stays FLUID (no max-width cap) and uses the full available width; horizontal gutters GROW on very wide screens (2xl) so content is not edge-glued — wide tables keep full width.
  - Below lg the layout is UNCHANGED: stacked, mobile header + hamburger→drawer sheet, the whole document scrolls (every new class is lg/2xl-scoped).
  - The FROZEN v13 shell contract is preserved EXACTLY: skip-link to #main is the first focusable element; exactly ONE Primary <nav> landmark; a <main id="main"> landmark; responsive lg:flex-row root.
  - The shell still uses window.location (never next/navigation — the legacy test mocks next/navigation without useRouter).
</must>
Reject:
<reject>
  - (pure presentational/layout change — no inputs to reject) -> the one failure mode to avoid: a DOUBLE scrollbar (document + main) at lg, or the rail scrolling away.
</reject>
After:
<after>
  - On a 2560-wide screen the rail is full height with the footer pinned; content is fluid with comfortable gutters; only the main region scrolls.
  - Below lg, behavior is identical to today (the unchanged mobile-sheet / drawer / logout tests stay green).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The literal "sidebar ~75% height" did NOT reproduce in Chromium at 1920/2560 (rail is already full-height; live captures in `tmp/shellcaps/`) — lowest confidence because Tin reported it on their screen; if the cause is environment-specific (Safari/zoom), the fixed-viewport re-architecture still GUARANTEES full height (no sticky), so the fix holds regardless. Cost: low.
  - [ ] Making <main> the scroll container could retain scroll position across route changes (Next restores window scroll, not an inner scroller) — confirm at verify; if it bites, seed an observe delta (reset main scroll on activePath change).
  - [ ] "Scale gutters, no cap" reads well on ultrawide — confirmed by the after-capture at verify.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Desktop rail is full height without sticky
  Given the AppShell renders at the lg breakpoint or wider
  When the page loads
  Then the desktop rail fills the full viewport height via the fixed-viewport layout (lg:h-full inside an lg:h-screen, lg:overflow-hidden container)
  And the rail no longer relies on lg:sticky

Scenario: Only main scrolls on desktop
  Given the AppShell at lg+ with content taller than the viewport
  When the user scrolls
  Then <main> is the scroll region (lg:h-full, lg:overflow-y-auto)
  And the rail and its footer stay in place (no document-level scroll, no double scrollbar)

Scenario: Content is fluid with scaled gutters on wide screens
  Given a very wide (2xl) screen
  When a page renders
  Then <main> applies larger horizontal gutters at the 2xl breakpoint
  And no max-width cap is applied (content stays fluid, tables keep full width)

Scenario: Mobile layout unchanged
  Given a viewport below lg
  When the shell renders
  Then the mobile header + hamburger→drawer sheet behave exactly as before
  And the desktop fixed-viewport classes (lg:*) do not apply

Scenario: Frozen v13 shell contract preserved
  Given the AppShell renders
  When inspected
  Then the skip-link is the first focusable element, there is exactly ONE Primary nav, a main#main landmark, and an lg:flex-row root
  And no new serious/critical axe violations are introduced
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Component: AppShell (apps/dashboard/components/ui/app-shell.tsx) — presentational LAYOUT only; NO HTTP / schema change.

Layout container (the lg:flex-row root):
  ADD   lg:h-screen  lg:overflow-hidden        (keeps: flex min-h-screen flex-col lg:flex-row)
Desktop rail (Sidebar, role=navigation "Primary"):
  BECOMES   hidden lg:flex lg:h-full           (REMOVES lg:sticky lg:top-0 lg:h-screen — superseded)
<main id="main">:
  ADD   lg:h-full  lg:overflow-y-auto  +  a 2xl horizontal-gutter step (2xl:px-16)   (keeps: flex-1 p-4 lg:p-8; NO max-width cap)

Invariants preserved (frozen v13): skip-link first · exactly ONE Primary nav · main#main · lg:flex-row · window.location.
Below-lg behavior: UNCHANGED (every added class is lg/2xl-scoped).
```

Status: FROZEN @ v1 — approved by Tin
<!-- design-confirm via AskUserQuestion (2026-06-28), before/after captures shown:
     content width = "No cap — scale gutters"; sidebar height = "Re-architect (bulletproof)".
     Lowest-confidence flag surfaced at freeze:
       [spec] the "~75% height" did NOT reproduce (rail already full-height in Chromium 1920/2560) —
         the fix is robust regardless because the fixed-viewport model removes the sticky dependency. why: Tin saw it on their screen; cost if wrong: low.
       [contract] the v23 test_desktop_rail_is_full_height_STICKY assertion is REPLACED by a fixed-viewport
         assertion — sanctioned by this design decision (intent "rail is full height" preserved/strengthened),
         NOT a build-driven test weakening. -->
Least-sure flag surfaced at freeze: [spec] ~75% height did not reproduce — fixed-viewport model guarantees full height regardless; [contract] sticky-assertion test replaced (not weakened) per Tin's re-architect decision.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% lines (the dashboard gate) — the app-shell-sidebar suite already exercises AppShell heavily.
Plan (one test per scenario, asserting the behavior-encoding classes — jsdom cannot compute layout):
<test_plan>
  - REWRITE test_desktop_rail_is_full_height_sticky → test_desktop_rail_full_height_fixed_viewport: assert rail.className has "lg:h-full" and NOT "lg:sticky"; assert the lg:flex-row container also has "lg:h-screen" + "lg:overflow-hidden"; assert main#main has "lg:overflow-y-auto". (RED: today has sticky, no lg:h-full / overflow.)
  - ADD test_main_scales_gutters_on_wide_screens: assert main#main className contains the 2xl gutter class "2xl:px-16". (RED: not present today.)
  - PRESERVE green-by-design (unchanged): test_frozen_v13_shell_contract_holds · test_single_primary_landmark · test_responsive_root_class · mobile sheet/drawer/logout · collapse · theme · footer · active-route.
</test_plan>

Tests live in: `tests/design-system/app-shell-sidebar.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/tests/design-system/app-shell-sidebar.test.tsx`
Strategy (ordered batches): 1. rewrite the §4 sticky test red + add the gutter test red 2. apply the 3 className edits (layout container · rail · main) 3. green the suite + tsc + next build 4. re-capture at 2560 to confirm full-height rail + fluid gutters + main-only scroll.
Known-problem fixes: double-scrollbar at lg → layout container `lg:overflow-hidden` and `<main>` owns the ONLY lg scroll (`lg:overflow-y-auto`); mobile regression → every new class is lg/2xl-scoped, below-lg DOM untouched.
Strategy actually used: as planned (3 className edits) PLUS a verify-surfaced fix: the refute-read (frontend-expert, BLOCK 0.87) caught that making <main> the sole scroll container makes Next's window-scroll reset a no-op → route changes would inherit the prior page's scroll. Closed by a mainRef + useEffect that resets main scroll on activePath change, and tabIndex={-1} so the skip-link/keyboard can focus the scroll region (red→green regression tests added). This was the open §1 assumption, confirmed real.
Safety rule (feature-specific): lg/2xl-scope every new class; never touch the frozen v13 invariants or the below-lg DOM.
Code lives in: `apps/dashboard/components/ui/app-shell.tsx`
Constraints: do NOT change any test (beyond the §4-declared sticky-test rewrite) or the frozen contract; no new dependency; ask if unclear.

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

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] On a 2560 capture the rail is full height (footer pinned, no gap) — confirmed by `tmp/shellcaps/after_fix_top.png` + `after_fix_scrolled.png`
- [x] Content is fluid with no max-width cap; 2xl gutters present — confirmed by after-captures + test_main_scales_gutters (no max-w-). NOTE: per Tin's "content stays wide" choice the px-16 gutter is modest by design (clearly visible on a laptop, a thin band on a 2560 monitor) — a one-line bump if Tin wants more.
- [x] Only the main region scrolls at lg (rail + footer stay put, no double scrollbar) — confirmed LIVE: main.scrollTop moved to 816 while window.scrollY stayed 0; after-scroll capture shows the rail+footer pinned with content at cards 9–24
- [x] Below-lg mobile sheet/drawer/logout behavior identical — every new class is lg/2xl-scoped; the unchanged mobile-sheet/drawer/logout tests stay green
- [x] No new serious/critical axe violations — the preserved axe assertions (incl. with tabindex=-1 main) pass

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `mainRef` is bound to `<main ref={mainRef}>` and read by the scroll-reset `useEffect([activePath])`; the 3 layout classes render on the layout container / rail / main; all exercised by the suite (app-shell.tsx 100% lines)
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; the removed `lg:sticky/lg:top-0/lg:h-screen` rail classes have no other reference; throwaway preview page + `.next` cleared
- [x] SEMANTIC (prose / non-code) — n/a (code change); the §3 contract value (2xl:px-16) matches the shipped class exactly

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: auto-gate (autonomy: auto) on evidence + adversarial refute-read (frontend-expert: BLOCK 0.87 → blocker fixed + regression-tested → resolved); design approved by Tin at the freeze · date: 2026-06-28
Refute-read residue: nits accepted — redundant `min-h-screen` (needed for mobile; harmless at lg) · jsdom can't assert true layout geometry (standing browser-only residue, mitigated by live captures).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose fixed-viewport app shell + fluid content with scaled gutters; rejected keep the sticky rail and add a max-width cap (rejected — Tin chose re-architect for guaranteed height + no width cap) · full shell rebuild (rejected — "polish current behavior": keep drawer<lg / rail-from-lg)
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned (3 className edits) PLUS a verify-surfaced fix: the refute-read (frontend-expert, BLOCK 0.87) caught that making <main> the sole scroll container makes Next's window-scroll reset a no-op → route changes would inherit the prior page's scroll. Closed by a mainRef + useEffect that resets main scroll on activePath change, and tabIndex={-1} so the skip-link/keyboard can focus the scroll region (red→green regression tests added). This was the open §1 assumption, confirmed real.
- [AI] verify — gate PASS (reviewed by auto-gate (autonomy: auto) on evidence + adversarial refute-read (frontend-expert: BLOCK 0.87 → blocker fixed + regression-tested → resolved); design approved by Tin at the freeze)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
