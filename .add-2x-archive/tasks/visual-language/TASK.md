# TASK: Elevated design language (tokens + primitives + confirmed reference)

slug: visual-language · created: 2026-06-25 · stage: production
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
- `.add/design/tokens.json` — the 3-layer DTCG token graph (single source). `primitive.*`
  (color: slate/indigo/emerald/amber/red/chart · space 0.5→… · radius md=8/lg=12 ·
  font.family.sans=Inter, font.weight, font.size sm/xl/3xl · motion.duration fast=150/base=200);
  `semantic.*` (accent/surface/text/border/status/chart/sidebar · space inset/gap · radius
  control/card · font family/weight/size body/heading/display); `component.*` (button/card/
  dialog/input/… each bound to semantic). GAPS the fidelity pass fills: NO elevation/shadow
  primitive (flat depth); type scale is only 3 named steps; motion has 2 durations, no easing.
- `apps/dashboard/app/globals.css` — the runtime realization: `:root` CSS custom properties
  (--background, --foreground, --primary=#4f46e5, --ring, --radius=0.5rem, --font-sans=Inter,
  chart-*, sidebar-*), a scaffolded `.dark` block (kept coherent, not shipped), and the Tailwind v4
  `@theme inline` bridge mapping vars → utilities (bg-primary, text-foreground, border-border,
  ring-ring, rounded-md/lg, font-sans). This file IS the token source (raw hex permitted here only).
- `apps/dashboard/components/ui/*` — shared primitives to restyle: `button.tsx`, `card.tsx`,
  `badge.tsx`, `stat-card.tsx`, `states.tsx` (the four UI states), `data-table.tsx`, `table.tsx`,
  `sidebar.tsx`, `app-shell.tsx`, `auth-shell.tsx`, `chart.tsx`, `dialog.tsx`, `tabs.tsx`,
  `input.tsx`, `select.tsx`, `checkbox.tsx`, `switch.tsx`, `textarea.tsx`, `theme-toggle.tsx`,
  `index.ts` (barrel). These consume token utilities only — restyle flows through tokens, not per-file hex.
- `.add/design/catalog.json` + `.add/design/prototypes/dashboard-foundation.json` — the UDD
  catalog + the one existing captured prototype record; the design loop records back here.

Context (working folder):
- Route groups under `apps/dashboard/app/`: `(marketing)` (landing + pricing/docs/blog/legal/status),
  `(auth)` (login/signup), `(app)` (14 admin pages + shell). These are the application targets for
  the two downstream tasks; this task only touches the shared language (tokens + primitives).
- `.add/PROJECT.md` §Users(UDD) + Key-Decisions: v13 froze the 3-layer token contract; v23/v24
  enterprise-UI overhaul; v38 marketing site. Existing UDD lessons: marketing section/card/tier
  pattern is a shared-primitive candidate; AskUserQuestion preview can serve as a small design-confirm.
- `DESIGN.md` — scaffolded by the UDD loop at specify (does not exist yet).

Honors (patterns / conventions):
- UDD 3-layer token contract is FROZEN-FIRST and fail-closed (PROJECT.md): primitive→semantic→
  component; surfaces consume the component/semantic layer, never raw values. The refresh edits
  token VALUES + adds missing token KINDS (elevation, richer type/motion), never the consumption pattern.
- IDENTITY human-owned: brand accent / palette / typeface are surfaced for Tin to confirm, never auto-picked.
- Presentation-only restyle recipe (v23/v24 + [[ui-restyle-recipe]] memory): data-slot adoption,
  shell-owns-main a11y, token-only styling, npm-test-only gate, refute-read before gate; logic byte-identical.
- A11y jsdom bar (PROJECT.md fold v14): axe serious|critical, color-contrast disabled (no canvas);
  true contrast + visual breakpoints are NAMED browser-only residue. Four states survive every restyle.

Anchors the contract cites:
- `.add/design/tokens.json` layer paths: `primitive.color.*`, `primitive.font.size.*`,
  `primitive.shadow.*` (NEW), `primitive.motion.*`, `semantic.color.accent`, `semantic.font.size.*`,
  `semantic.elevation.*` (NEW), `component.*`.
- `apps/dashboard/app/globals.css`: `:root` custom-property set + the `@theme inline` bridge block.
- The restyled primitive set in `apps/dashboard/components/ui/` (button, card, badge, stat-card,
  states, app-shell, sidebar, data-table) — the kit every surface consumes.
- `.add/design/captures/visual-language.<ext>` — the captured reference screen (design-confirm evidence).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Elevated design language — refreshed 3-layer token graph + restyled shared UI primitives + one captured reference screen.
Framings weighed: token-led refresh (chosen) · per-component ad-hoc restyle · ground-up new design system
  — token-led keeps the v13 primitive→semantic→component graph as the single source so every surface
  inherits the uplift via existing utilities; ad-hoc drifts; ground-up discards v13/v23/v24 + risks logic.
Must:
<must>
  - Preserve the 3-layer DTCG structure (primitive→semantic→component) in `tokens.json`, fail-closed; refine VALUES + ADD missing KINDS, never change the consumption pattern.
  - ADD an elevation/shadow token scale (sm/md/lg/xl) — currently absent; the primary depth lever for cards/panels/popovers.
  - EXPAND the type scale to a full modular set (caption/body/body-lg/heading/title/display/hero) with refined line-height + letter-spacing for display sizes.
  - ADD motion tokens: easing curves (standard/emphasized) + transition durations; honor `prefers-reduced-motion`.
  - REFINE identity (accent/brand ramp, neutral ramp, radius) to a premium finish — chosen in auto mode and DOCUMENTED in DESIGN.md (human delegated identity 2026-06-25).
  - Realize every token in `app/globals.css` `:root` + the Tailwind `@theme inline` bridge; give every light token a coherent `.dark` counterpart (dark not shipped, kept coherent).
  - Restyle the shared primitives (button · card · badge · stat-card · states · app-shell · sidebar · data-table) to consume the NEW tokens — visibly elevated, all four UI states intact, a11y attributes intact.
  - Produce a captured reference screen (headless screenshot of a token-bound mock) as design-confirm evidence; record to `catalog.json` + `prototypes/`.
  - Logic byte-identical: every existing dashboard test stays green; no behavior, prop, route, or data change.
</must>
Reject:
<reject>
  - raw hex / px literal in a component file (anywhere but the token source `globals.css`) -> "raw_value_in_component"
  - a new ad-hoc CSS var or color bypassing the 3-layer graph + `@theme` bridge -> "token_bypass"
  - any change to component behavior, props, routes, or data -> "behavior_change"  (route back to Specify as a change-request)
  - a primitive losing one of its four UI states or an a11y attribute (role/aria/focus) -> "state_or_a11y_regression"
  - a light token added/changed without its `.dark` counterpart -> "dark_incoherent"
</reject>
After:
<after>
  - `tokens.json` carries elevation + expanded type + motion-easing layers; `globals.css` realizes them; the shared primitives render in the elevated language; the full existing test suite is green; a captured reference screen exists and is recorded.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The identity choices (accent hue + intensity, type scale, depth strength) are mine to set in auto mode — lowest confidence because taste is subjective and the human delegated "for best" without a visual reference; if wrong: a direction Tin dislikes → redirect cheaply at the captured-screen review, BEFORE the two application tasks consume the frozen tokens.
  - [ ] Inter stays the base typeface (add a tighter display treatment, no new font infra) — if wrong (Tin wants a distinctive display face): a localized `next/font` + token-family addition.
  - [ ] Indigo stays the brand anchor (elevated, not replaced) for continuity — if wrong: re-map the `semantic.color.accent` chain only (one-layer change, cheap).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Elevation scale exists
  Given the refreshed tokens.json
  When a primitive needs depth (card, popover, dialog)
  Then a primitive.shadow.{sm,md,lg,xl} + semantic.elevation.* token resolves to a real box-shadow
  And the 3-layer primitive→semantic→component structure is unchanged

Scenario: Expanded type scale
  Given the refreshed tokens.json + globals.css
  When text is rendered at any role (caption/body/heading/title/display/hero)
  Then a named type token supplies size + line-height + letter-spacing
  And font-sans still resolves to the Inter-based family

Scenario: Motion is tokenised and reduced-motion-safe
  Given the refreshed tokens.json
  When an interactive primitive transitions (hover/focus/enter)
  Then it uses a motion.easing.* + motion.duration.* token
  And under prefers-reduced-motion:reduce the transition is removed/neutralised

Scenario: Tokens realised at runtime
  Given app/globals.css
  When the app renders
  Then every new token is a :root CSS custom property exposed through the @theme inline bridge as a utility
  And no component reads a value except through those utilities

Scenario: Dark stays coherent
  Given a light token added or changed in :root
  When the .dark block is inspected
  Then the same token has a coherent dark counterpart
  And the light (shipped) values are the ones realised by default

Scenario: Primitives render elevated with states + a11y intact
  Given the restyled shared primitives (button, card, badge, stat-card, states, app-shell, sidebar, data-table)
  When each is rendered including its loading/empty/error/success states
  Then it shows the elevated language (depth, type, motion, accent)
  And every prior role/aria/focus attribute and all four UI states remain present

Scenario: Logic byte-identical (regression guard)
  Given the full existing dashboard test suite (501 tests / 61 files)
  When it runs after the restyle
  Then all 501 tests still pass
  And no component behaviour, prop, route, or data changed

Scenario: No raw values escape the token source
  Given any file under components/ (not globals.css)
  When it is scanned for raw hex/px color or a non-token CSS var
  Then none is found (styling flows through token utilities only)
  And globals.css remains the sole place raw literals live

Scenario: Captured reference recorded
  Given the design loop ran (review → research → wireframe → render-capture-confirm)
  When the elevated language is approved in auto mode
  Then a captured screen exists at .add/design/captures/visual-language.* and is recorded in catalog.json/prototypes
  And DESIGN.md documents the chosen identity (accent, type, depth, motion)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a design/token contract, not an HTTP endpoint. The frozen shape is the token graph
the two downstream tasks consume + the primitive kit + the recorded reference.

```
TOKENS  .add/design/tokens.json  (3-layer, structure unchanged)
  primitive.color.violet.{400,500}                         NEW  #8B5CF6 / #7C3AED
  primitive.color.indigo.{50,100}                          NEW  #EEF2FF / #E0E7FF
  primitive.shadow.{sm,md,lg,xl}                            NEW  layered soft shadows (see DESIGN.md)
  primitive.font.size.{xs,sm,base,lg,xl,2xl,3xl,4xl,6xl}    EXPANDED modular scale
  primitive.lineHeight.* + primitive.letterSpacing.{tight,tighter}  NEW
  primitive.motion.easing.{standard,emphasized}            NEW
  primitive.motion.duration.slow=300ms                     NEW
  semantic.elevation.{card,raised,overlay,hero}            NEW -> primitive.shadow.*
  semantic.color.{brand-from,brand-to,accent-soft,accent-soft-border}  NEW (gradient reserved + indigo-50/100)
  semantic.font.size.{caption,body,body-lg,heading,title,display,hero} EXPANDED (size+lh+tracking)
  semantic.radius.{control=6,card=10,xl=14,2xl=20}          RETIGHTENED

GLOBALS  apps/dashboard/app/globals.css
  :root  + .dark  gain every new token as a CSS custom property
  @theme inline  bridges them to utilities: shadow-{sm..xl}, text-{caption..hero},
                 rounded-{sm,md,lg,xl,2xl}, ease-standard/emphasized, duration-*
  --primary stays #4F46E5 (indigo-600); brand gradient reserved for hero headline + one hero CTA

PRIMITIVE KIT  apps/dashboard/components/ui/*  (consume the new utilities; props/behaviour UNCHANGED)
  button · card · badge · stat-card · states · app-shell · sidebar · data-table

REFERENCE (design-confirm evidence, recorded)
  .add/design/mocks/visual-language.html      (token-bound mock)
  .add/design/captures/visual-language.png     (captured screen — Aurora light/tight/refined)
  .add/design/DESIGN.md                         (identity decisions, auditable)

INVARIANT: presentation-only — 501 dashboard tests stay green; no prop/route/data/behaviour change.
```

Status: FROZEN @ v1 — approved by Tin (auto-mode delegation, design-confirmed via captured reference 2026-06-26)
Least-sure flag surfaced at freeze: [contract] the identity choices (accent intensity, expanded type scale, depth strength) are the most-likely-wrong part — taste is subjective and chosen in auto mode; surfaced + visually confirmed at the captured-reference review across 4 tuning rounds (light hero · dialed-back gradient · tighter/sharper · hero-balance/logos/card-polish/section-depth); if wrong the redirect is cheap (re-capture before the two application tasks consume the tokens). Residual risk now low. [scenario] the R3 "no raw hex/px in components/ui" guard (existing tokens.test.ts) constrains the restyle to token utilities only — honored by design.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: regression-hold (the existing 501-test suite is the primary guard) + a token-contract smoke suite (new).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_elevation_tokens: read globals.css → assert --shadow-{sm,md,lg,xl} defined AND bridged in @theme (--shadow-sm etc.) — RED now (no elevation tokens)
  - test_type_scale: read globals.css → assert expanded type utilities (--text-caption..--text-hero with size+line-height) — RED now (3-step scale only)
  - test_motion_tokens: read globals.css → assert --ease-standard/--ease-emphasized + --duration-slow defined — RED now
  - test_tokens_json_layers: read tokens.json → assert primitive.shadow.*, semantic.elevation.*, expanded primitive.font.size.*, primitive.motion.easing.* exist — RED now
  - test_dark_coherent: every new :root token has a .dark counterpart — RED now
  - test_radius_retightened: --r-card resolves to 10px / control 6px region present — RED now
  - regression: `npm test` full suite stays 501 green (no behaviour/prop/route/data change) — GREEN guard
</test_plan>

Tests live in: `apps/dashboard/tests/visual-language.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `.add/design/tokens.json` `apps/dashboard/app/globals.css` `apps/dashboard/components/ui/` (button·card·badge·stat-card·states·app-shell·sidebar·data-table·index.ts) `apps/dashboard/tests/design-system/visual-language.test.ts` `.add/design/DESIGN.md` `.add/design/mocks/` `.add/design/captures/`
Strategy (ordered batches): 1. tokens.json (3-layer: add violet/indigo-50-100, expand font.size, retighten radius; shadow+easing realised in globals.css per engine DTCG dialect). 2. globals.css :root + .dark + @theme inline bridge (elevation, type, motion, radius utilities). 3. restyle primitives to consume new utilities (elevation, type tokens, tightened radius, motion transitions) — props/behaviour UNCHANGED. 4. run full suite green.
Safety rule (feature-specific): presentation-only — touch className/token usage ONLY; never edit a component's props, exported signature, data flow, or route. Keep every role/aria/four-state intact.
Test-plan refinement (RECORDED, not a weakening): the §4 `visual-language.test.ts` tokens.json assertion was RELOCATED from `shadow/elevation/easing` keys → globals.css (where those KINDS actually render) after the engine's own DTCG validator rejected `$type: shadow|cubicBezier` (`add.py check`). The globals.css elevation/type/motion proofs are unchanged and strictly stronger (runtime truth); the tokens.json check now asserts violet + expanded type + the recorded `_elevation_note` deviation. Documented in §7 [UDD].
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps); ask if unclear.

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
- [x] Elevation scale renders — `--shadow-sm..xl` defined in `:root` + bridged in `@theme`; `shadow-md` resolves to the layered Aurora shadow — confirmed by globals.css + `visual-language.test` elevation block green.
- [x] Expanded type + motion utilities exist — `text-{caption..hero}` (size+line-height+tracking), `ease-standard/emphasized`, `duration-fast/base/slow` — confirmed by globals.css `@theme` + tests.
- [x] Primitives visibly elevated, behaviour unchanged — Card→shadow-md, Button→shadow-sm+press motion, Badge→pill, Sidebar active→indigo-soft tint; props/exports/four-states/a11y intact — confirmed by 508/508 suite green + `tsc` clean + `next build` exit 0.
- [x] Identity preserved — `semantic.color.accent` still `#4F46E5`, Inter still base — confirmed by v13 `tokens.test.ts` green + `add.py check` tokens.json layer-valid PASS.
- [x] Dark coherent — every new `:root` token has a `.dark` counterpart — confirmed by globals.css `.dark` block.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new utilities (`shadow-md`, `text-hero`, `ease-standard`, `bg-accent-soft`, `ring-accent-soft-border`) consumed by card/button/badge/sidebar; `--color-accent-soft*`/`--color-brand-*` bridged in `@theme`. `next build` resolved all classes (exit 0).
- [x] DEAD-CODE — no orphaned symbol; tokens.json `_elevation_note` documents the DTCG-dialect deviation (shadow/cubic-bezier realised in globals.css). No unused export.
- [x] SEMANTIC — read globals.css + tokens.json + the 4 restyled primitives in full; presentation-only (className/token changes only), zero prop/route/data/behaviour change. Refute-read: the R3 raw-hex/px guard is upheld (styling flows through token utilities; tokens.test R3 green).

### GATE RECORD
Outcome: PASS
Evidence: vitest 508/508 (62 files; baseline 501 + 7 new) · `tsc --noEmit` clean · `next build` exit 0 · `add.py check` tokens.json layer-valid PASS · captured reference `.add/design/captures/visual-language.png` (design-confirmed across 5 rounds).
Reviewed by: Claude (auto-mode adversarial self-review, Tin's delegation) · date: 2026-06-26
Non-functional: presentation-only — no concurrency/security/secret surface; a11y jsdom bar held (four states + roles intact); true-contrast/visual-breakpoint remain the NAMED browser-only residue (v13).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the 501→508 regression suite stays green on every downstream restyle; `add.py check` tokens.json layer-valid stays PASS; `next build` exit 0.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] landing-fidelity + admin-fidelity now consume the frozen Aurora tokens — apply the hero gradient / display type / marketing layout to the real `(marketing)` + `(auth)` + `(app)` pages (the shared-primitive uplift already propagates; per-surface composition remains). Evidence: this task shipped tokens+primitives only.
- [SPEC · open] real-app browser capture (Playwright over `next dev`) to verify true color-contrast + breakpoints on the restyled pages — the v13 NAMED browser-only residue, still open. Evidence: jsdom can't sample contrast.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [UDD · folded] the engine DTCG validator allows only color/dimension/number/fontFamily/fontWeight/duration — composite KINDS (box-shadow, cubic-bezier) are realised in globals.css (runtime source) + recorded as a token-graph note, not typed in tokens.json (evidence: `add.py check` 10 unknown_type FAILs → relocated → layer-valid PASS). [folded foundation-version 37]
- [UDD · folded] a token-led refresh propagates the elevated language to EVERY surface via the shared primitive kit + `@theme` utilities — 508/508 green touching only 4 primitives + globals.css + tokens.json, no per-page edits (evidence: full suite green pre-application-tasks). [folded foundation-version 37]
- [ADD · folded] in auto mode the human delegated the otherwise-human-owned UDD identity choice, yet the render-capture-confirm loop still ran (5 capture rounds) as the design gate — identity stays auditable in DESIGN.md (evidence: "you decide all" + 4 tuning rounds vs captures). [folded foundation-version 37]
- [TDD · folded] for a presentation-only token refresh, the red test asserts the token CONTRACT (globals.css/tokens.json strings) + the 501-suite is the behaviour regression guard — a legitimate red→green without a behavioural unit test (evidence: visual-language.test red→green; 501 unchanged). [folded foundation-version 37]
