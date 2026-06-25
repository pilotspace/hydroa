# TASK: Apply Aurora language to marketing + auth surfaces

slug: landing-fidelity · created: 2026-06-26 · stage: production
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
- `apps/dashboard/app/(marketing)/page.tsx` — the PUBLIC landing (frozen §3: server component, ordered sections with anchors #product/#pricing/#docs, exactly one h1, h2-per-section). Hero · FEATURES · pricing/docs teasers · TRUST band · final CTA.
- `apps/dashboard/components/marketing/feature-card.tsx` — `FeatureCard` (Card + emoji icon + h3 title + desc) used by the landing FEATURES grid.
- `apps/dashboard/components/ui/auth-shell.tsx` — `AuthShell` split-screen frame for /login + /signup; LEFT decorative brand panel (`bg-primary`, aria-hidden) + RIGHT `<main>`.
- `apps/dashboard/app/(auth)/login/page.tsx` + `signup/page.tsx` — host the page h1 + form inside AuthShell.
- Other `(marketing)` pages (pricing/docs/blog/legal/status) + `(marketing)/layout.tsx` inherit the uplift via the shared primitives already restyled by visual-language.
Context (working folder):
- Consumes the FROZEN `visual-language` Aurora token contract: `shadow-*`, `text-hero/display`, `from-brand-from/to-brand-to`, `bg-accent-soft`, `rounded-*`, `ease-standard` utilities in globals.css.
- Reference: `.add/design/captures/visual-language.png` (the hero + CTA composition to match).
Honors (patterns / conventions):
- FROZEN landing §3: PUBLIC, server component, ordered sections + anchors, exactly one h1, monotonic headings — PRESERVE structure/text/links/headings; restyle className + add aria-hidden decorative layers only.
- AuthShell brand panel stays `aria-hidden` decorative (no heading/landmark/focusable) — restyle is pure chrome.
- Presentation-only recipe ([[ui-restyle-recipe]]): token utilities only (R3 no raw hex/px), four-state + a11y intact, regression suite is the guard.
Anchors the contract cites:
- `(marketing)/page.tsx` hero + final CTA sections; `feature-card.tsx` `FeatureCard`; `auth-shell.tsx` brand panel.
- The frozen `tests/landing-page.test.tsx` (one h1, section#ids, monotonic headings) — stay green.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Apply the Aurora language to the marketing landing + auth surfaces.
Framings weighed: restyle-in-place preserving frozen contracts (chosen) · rebuild pages · new components — in-place keeps the frozen landing §3 + tests intact while delivering the visual uplift.
Must:
<must>
  - Restyle the landing HERO with the Aurora treatment: soft indigo/violet radial gradient + dot-grid (aria-hidden decorative layers), a gradient headline accent span, larger display type — PRESERVING the frozen single h1, section order, anchors, text, and links.
  - Elevate `FeatureCard`: a soft-accent icon chip container (token utilities) on top of the already-elevated Card.
  - Restyle the final CTA section as a brand-gradient surface (`from-brand-from`→`to-brand-to`), text + link unchanged.
  - Restyle the `AuthShell` decorative brand panel as a brand gradient + texture — stays `aria-hidden`, no heading/landmark/focusable child.
  - Other `(marketing)` pages inherit the uplift via the shared primitives (no per-page edits) — verify they stay green.
</must>
Reject:
<reject>
  - changing the landing's frozen structure (h1 count, section order, anchors, text, links) -> "frozen_contract_change"
  - a raw hex/px literal in a page/component (token utilities only) -> "raw_value"
  - adding a heading/landmark/focusable child to the aria-hidden auth brand panel -> "a11y_decorative_violation"
</reject>
After:
<after>
  - the landing + auth surfaces render in the Aurora language; `tests/landing-page` + auth tests stay green; no structure/behaviour change.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The frozen landing tests assert STRUCTURE not classes, so a className restyle + aria-hidden decorative wrapper won't break them — lowest confidence because a decorative wrapper could accidentally alter section nesting or inject a heading; if wrong: the suite breaks immediately (cheap to catch + fix).
  - [ ] gradient-text + brand-gradient utilities (`from-brand-from`/`to-brand-to`, `bg-clip-text`) resolve from the bridged Aurora tokens — if wrong: fall back to `bg-primary` (one-line).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Landing hero wears the Aurora treatment
  Given the public landing page
  When it renders
  Then the hero has a gradient/dot-grid decorative background + a gradient-clipped headline accent + display-scale type
  And it still has exactly one h1, the #product/#pricing/#docs sections in order, and the /signup + /login + /pricing + /docs links

Scenario: Final CTA is a brand-gradient surface
  Given the landing final CTA section
  When it renders
  Then its background is the brand gradient (from-brand-from → to-brand-to)
  And its heading text + /signup link are unchanged

Scenario: Auth brand panel is elevated but still decorative
  Given /login and /signup via AuthShell
  When they render
  Then the left brand panel shows the brand gradient + texture
  And it remains aria-hidden with no heading/landmark/focusable child (single main + single h1 per page preserved)

Scenario: Regression guard
  Given the full dashboard suite
  When it runs after the restyle
  Then tests/landing-page.test.tsx + the auth page tests stay green
  And no page structure, text, link, or behaviour changed
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Presentation contract (no endpoint). The frozen shape = the surfaces restyled + the invariants held.

```
SURFACES (restyle className + aria-hidden decorative layers only)
  (marketing)/page.tsx   HERO  -> gradient+dot-grid bg · gradient headline span · display type
                         CTA   -> brand-gradient surface
  components/marketing/feature-card.tsx  -> soft-accent icon chip
  components/ui/auth-shell.tsx           -> brand-gradient + texture brand panel (stays aria-hidden)
INHERIT (no edit): pricing/docs/blog/legal/status + login/signup bodies (via shared primitives)

INVARIANTS (must hold — the regression guard)
  landing: exactly one h1 · sections #product/#pricing/#docs in order · all links + text unchanged
  auth:    one <main> + one h1 per page · brand panel aria-hidden, no heading/landmark/focusable
  global:  token utilities only (R3 no raw hex/px) · four-state + a11y intact · no behaviour change
  evidence: tests/landing-page.test.tsx + auth tests GREEN; full suite stays green
```

Status: FROZEN @ v1 — approved by Tin (auto-mode delegation; consumes the design-confirmed visual-language reference)
Least-sure flag surfaced at freeze: [scenario] the only real risk is a decorative wrapper accidentally altering the landing's frozen structure (section nesting / an injected heading) — caught immediately by `tests/landing-page.test.tsx`; if wrong, revert the wrapper (cheap). [contract] gradient-text utilities must resolve from the bridged brand tokens — fallback `bg-primary` if not.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: regression-hold (landing + auth suites) + a small Aurora-treatment smoke test (new).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_hero_aurora: render landing → assert hero has a gradient/decorative bg marker + a bg-clip-text gradient span — RED now (flat hero)
  - test_cta_brand_gradient: render landing → assert final CTA uses from-brand-from/to-brand-to — RED now (bg-primary)
  - test_structure_unchanged: landing still has exactly one h1 + #product/#pricing/#docs sections — GREEN guard (must stay)
  - test_auth_panel_gradient: render AuthShell → brand panel uses from-brand-from gradient AND stays aria-hidden — RED now (bg-primary)
  - regression: full `npm test` stays green (landing-page + auth + a11y suites) — GREEN guard
</test_plan>

Tests live in: `apps/dashboard/tests/design-system/landing-fidelity.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/(marketing)/page.tsx` `apps/dashboard/components/marketing/feature-card.tsx` `apps/dashboard/components/ui/auth-shell.tsx` `apps/dashboard/tests/design-system/landing-fidelity.test.tsx`
Strategy (ordered batches): 1. red test (landing-fidelity.test.tsx). 2. landing hero gradient/dot-grid bg + gradient headline span + display type; final CTA brand gradient. 3. FeatureCard soft-accent icon chip. 4. AuthShell brand-gradient panel + texture. 5. full suite green + real-app capture.
Safety rule (feature-specific): presentation-only — preserve the frozen landing structure (one h1, section order, anchors, text, links) + AuthShell aria-hidden decorative panel. Token utilities only.
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

- [x] all tests pass — vitest 512/512 (508 prior + 4 new landing-fidelity)
- [x] coverage did not decrease — presentation-only; +4 tests, 0 removed
- [x] no test or contract was altered during build — only the NEW landing-fidelity.test.tsx authored at §4; frozen landing-page.test.tsx + auth tests untouched + green
- [x] the green was EARNED — tests assert rendered DOM (data-slot, gradient class on h1 span, CTA/panel className) + structure invariants; corroborated by a REAL-APP capture (not just jsdom): landing-fidelity.png + auth-fidelity.png
- [x] concurrency / timing — N/A (static server components, no async/IO changed)
- [x] no exposed secrets / injection / unexpected deps — className-only edits, no new imports/deps
- [x] layering & dependencies follow CONVENTIONS.md — token utilities only; R3 (no raw hex/px in components/ui) GREEN (auth-shell sheen uses % + rgba, no px); page-level px lives in app/(marketing), outside R3 scope
- [x] reviewed & approved — auto-gate (autonomy: auto), presentation-only, no security/concurrency/arch residue; real-app captures inspected first-hand

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] the landing hero shows a soft indigo wash + dot-grid + a gradient-clipped "Enterprise Teams" headline — confirmed in landing-fidelity.png (real `next start` capture)
- [x] feature cards carry a soft-accent icon chip + lift on the elevated Card — confirmed in landing-fidelity.png
- [x] the final CTA is a brand indigo→violet gradient surface — confirmed in landing-fidelity.png
- [x] the auth brand panel is a brand gradient with a soft sheen, form card elevated; still one main + one h1 — confirmed in auth-fidelity.png + test_auth_panel_gradient
- [x] frozen structure intact (one h1, #product/#pricing/#docs in order, /signup·/login·/pricing·/docs links) — confirmed by test_structure + landing-page.test.tsx green + `next build` exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [x] DEAD-CODE (code) — no new symbol/export; className edits + one decorative div per surface, all rendered
- [x] WIRING (code) — `data-slot="hero-aurora"` rendered in the hero; gradient utilities resolve from the bridged Aurora tokens (confirmed in the real-app capture, not just class strings)
- [x] SEMANTIC — landing copy/headings/links + AuthShell a11y contract read in full and unchanged; only presentation classes differ

### GATE RECORD
Outcome: PASS
Evidence: vitest 512/512 (4 new landing-fidelity, RED→GREEN) · tsc --noEmit clean · `next build` exit 0 (all (marketing)+(auth) routes) · R3/tokens guard GREEN · real-app captures landing-fidelity.png + auth-fidelity.png inspected first-hand. Frozen landing structure + AuthShell decorative-panel a11y preserved.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: auto-gate (autonomy: auto; presentation-only, no security/concurrency/arch residue) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): landing-page.test.tsx (structure) + landing-fidelity.test.tsx (Aurora treatment) stay green on every future dashboard change; the real-app captures are the visual baseline.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] admin-fidelity (next task) applies the Aurora composition to the (app) shell + 14 admin pages — the shared-primitive uplift already shows there; per-page composition (page headers, stat-card sparklines, table polish) remains. Evidence: this task covered marketing+auth only.
- [SPEC · open] the pricing/docs/blog/legal/status marketing pages inherit only the primitive uplift — a later pass could give them the same hero/section-depth treatment as `/`. Evidence: only `/` got the bespoke hero here.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [UDD · open] a frozen page §3 (structure: one h1, ordered anchors) and a visual uplift coexist cleanly — restyle = className + aria-hidden decorative layers, asserted by structure-invariant tests, so the freeze never blocks the polish (evidence: landing-page.test.tsx stayed green through the Aurora hero).
- [TDD · open] rendering the real component + asserting DOM (data-slot, gradient class on the h1 span, panel className + aria-hidden) is a stronger red→green than reading source strings — and a real-app Playwright capture corroborates what jsdom can't (true gradient render) (evidence: 4 tests RED→GREEN + landing/auth captures).
- [ADD · open] the R3 guard scopes raw-px bans to components/ui only — page-level arbitrary CSS (the hero grid/wash) is legitimately allowed in app/(marketing); know the guard's scope before relocating decorative CSS (evidence: moved the dot-grid OUT of auth-shell to dodge R3, kept it in the page).
