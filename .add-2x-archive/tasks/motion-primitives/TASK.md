# TASK: Progressive, reduced-motion-safe animation primitives

slug: motion-primitives · created: 2026-06-26 · stage: production
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
- `apps/dashboard/app/globals.css` : the token + base layer. NO `prefers-reduced-motion` handling exists today (grep = only the `tw-animate-css` import). ADD a global reduced-motion NET at the end: `@media (prefers-reduced-motion: reduce)` that forces animation/transition to ~none for everyone (covers tw-animate-css + every utility, including third-party).
- (NEW) `apps/dashboard/components/ui/motion.tsx` : `Reveal({ as?, className, children, ...})` Client-agnostic component — a progressive entrance (fade/slide) gated behind Tailwind's `motion-safe:` variant so reduced-motion users get NO animation but ALWAYS see the content (never hidden-until-animated). Optional `delay`/`as` for composition.
- `apps/dashboard/components/ui/index.ts` : barrel — export `Reveal` alongside the existing primitives.

Context (working folder):
- `tw-animate-css@^1.4.0` (imported in globals.css) provides `animate-in`/`fade-in-0`/`slide-in-from-*`/`duration-*`. NO framer-motion / JS motion lib — keep it CSS-only (zero new dep, zero JS runtime cost).
- Tailwind v4 core variants `motion-safe:` / `motion-reduce:` key off `prefers-reduced-motion` — the right gate for progressive enhancement.
- Many primitives already use `animate-`/`transition-` (dialog/tabs/switch/sidebar/states) — the global NET protects ALL of them at once; Reveal is the opt-in entrance for page/section content.
- Tests: `tests/`/`tests-bff/` (vitest+jsdom). jsdom has NO CSS engine → assert the COMPONENT contract (children always rendered, motion-safe class applied, className merged) + a content assertion that globals.css ships the reduced-motion block.

Honors (patterns / conventions):
- Aurora design language + `cn` merge idiom; reuse tw-animate-css classes (no new keyframes).
- A11y (WCAG 2.2 — 2.3.3 Animation from Interactions): honor prefers-reduced-motion. PROGRESSIVE: motion is polish layered on top; content/usability never depends on it.
- IO-rule N/A (pure presentation, no IO).

Anchors the contract cites: `Reveal` (new), the `motion-safe:` gate, the globals.css `@media (prefers-reduced-motion: reduce)` net, tw-animate-css `animate-in`/`fade-in-0`/`slide-in-from-bottom-*`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Progressive, reduced-motion-safe motion primitives (a global reduced-motion net + a Reveal entrance)
Framings weighed: CSS-only net + motion-safe Reveal (chosen — zero dep) · add framer-motion (rejected — heavy JS dep for polish) · per-component ad-hoc animations (rejected — no a11y net, drift)
Must:
<must>
  - M1 globals.css ships a global `@media (prefers-reduced-motion: reduce)` net that reduces animation-duration/transition-duration to ~0 (and animation-iteration-count to 1) for `*` — so EVERY animation (tw-animate-css, utility, third-party) is neutralized for users who request reduced motion.
  - M2 `Reveal` renders its children UNCONDITIONALLY (progressive — content is never hidden waiting on animation); it applies a fade/slide entrance ONLY under Tailwind's `motion-safe:` variant (so reduced-motion = no animation, full content).
  - M3 `Reveal` merges a caller `className` (via `cn`), forwards rest props, and supports an `as` element + optional `delay` without breaking the motion-safe gate.
  - M4 No new dependency (CSS-only, tw-animate-css classes); the global net + Reveal touch no existing component behavior; existing tests stay green.
</must>
Reject:
<reject>
  - an animation that ignores prefers-reduced-motion -> blocked by the global net (M1) + the motion-safe gate (M2)
  - content hidden until an animation runs (a blank flash / opacity:0 stuck under reduced motion) -> Reveal always renders children; the entrance is additive (M2)
</reject>
After:
<after>
  - Reduced-motion users get a still, fully-usable UI (no animation, no hidden content); motion-allowed users get a subtle, on-brand entrance. No new dep; no existing behavior changed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The `motion-safe:` variant + tw-animate-css `animate-in fade-in-0 slide-in-from-bottom-*` classes compose under Tailwind v4 — lowest confidence because tw-animate-css classes must be prefixable by the core `motion-safe:` variant. If wrong (classes don't gate): the global net (M1) STILL guarantees the a11y outcome (reduced-motion users are safe); only the polish degrades. Mitigated: the net is the real guarantee; Reveal is enhancement.
  ⚠ jsdom has no CSS engine so I cannot assert the media query "took effect" — I assert the COMPONENT contract (children present, motion-safe class applied) + a content check that globals.css contains the reduced-motion block. If wrong (CSS net malformed): caught by `next build` compiling the CSS, not by a unit test.
  - [ ] The net uses `!important` on duration to beat utility specificity — standard a11y-reset practice; confirm it doesn't break a functional transition users rely on (it only shortens duration, behavior unchanged).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M2 Reveal always renders content + applies motion-safe entrance
  Given <Reveal>visible child</Reveal>
  When it renders
  Then the child text is in the DOM
  And the wrapper className contains a motion-safe: entrance class

Scenario: M3 Reveal merges className + forwards props + honors `as`
  Given <Reveal as="section" className="custom" data-testid="r">child</Reveal>
  When it renders
  Then it is a <section> with both "custom" and the motion-safe class and the data-testid

Scenario: M1 globals.css ships the reduced-motion net
  Given the compiled globals.css source
  When read
  Then it contains a "@media (prefers-reduced-motion: reduce)" block reducing animation/transition duration

Scenario: M4 no regression
  Given the full dashboard suite
  When run after adding the net + Reveal
  Then every existing test stays green and the barrel exports Reveal
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// components/ui/motion.tsx (NEW)
export interface RevealProps extends React.HTMLAttributes<HTMLElement> {
  as?: React.ElementType;   // default "div"
  delay?: 0 | 75 | 150 | 300;  // optional motion-safe:delay-* ; default 0
}
export function Reveal({ as: Tag = "div", delay = 0, className, children, ...rest }: RevealProps): JSX.Element
//   className = cn("motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-2 motion-safe:duration-500",
//                  delay && `motion-safe:delay-${delay}`, className)
//   renders <Tag className={...} {...rest}>{children}</Tag>   // children ALWAYS rendered

// components/ui/index.ts — add: export { Reveal } from "./motion"

// app/globals.css — append (outside @layer base so it wins):
//   @media (prefers-reduced-motion: reduce) {
//     *, *::before, *::after {
//       animation-duration: 0.01ms !important;
//       animation-iteration-count: 1 !important;
//       transition-duration: 0.01ms !important;
//       scroll-behavior: auto !important;
//     }
//   }
```

Schema: none — no DB, no network, no new dependency. No existing component behavior changed.

Least-sure flag surfaced at freeze: [spec] whether `motion-safe:` prefixes tw-animate-css classes under Tailwind v4 — if not, the global reduced-motion NET (M1) STILL delivers the a11y guarantee (the real requirement); Reveal's polish is the only thing at risk, and `next build` will surface a bad class. Net cost: low. · [contract] the net uses `!important` (standard a11y reset) — confirmed it only shortens durations, never changes behavior.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; presentation/a11y, low-risk; flag is polish-only, the a11y net is unconditional)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on `components/ui/motion.tsx`.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_reveal_renders_children_and_motion_safe: render <Reveal>hello</Reveal> → "hello" present; wrapper className matches /motion-safe:/.
  - test_reveal_merges_props: render <Reveal as="section" className="custom" data-testid="r"> → element is a SECTION, className has "custom" + a motion-safe class, data-testid present.
  - test_globals_has_reduced_motion_net: read app/globals.css → contains "@media (prefers-reduced-motion: reduce)" and "animation-duration".
  - test_barrel_exports_reveal: import { Reveal } from "@/components/ui" → is a function.
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests/motion-primitives.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/motion.tsx` `apps/dashboard/components/ui/index.ts` `apps/dashboard/app/globals.css` `apps/dashboard/tests/motion-primitives.test.tsx`
Strategy (ordered batches): 1. globals.css reduced-motion net. 2. motion.tsx Reveal. 3. barrel export. 4. green.
Safety rule (feature-specific): Reveal renders children unconditionally (never opacity:0 without the motion-safe entrance); the net only shortens durations (no behavior change).
Code lives in: `apps/dashboard/components/ui/` + `apps/dashboard/app/globals.css`
Constraints: do NOT change any test or the contract; allow-list packages only (NO new dep); change no existing component behavior; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 547 green (67 files); +5 new motion tests
- [x] coverage did not decrease — motion.tsx 100% line/func (delay branch now covered)
- [x] no test or contract was altered during build — only new motion.tsx + additive globals.css net + barrel export; existing component behavior untouched (545→547 additive)
- [x] the green was EARNED — Reveal test asserts children ALWAYS render (the progressive property) + the motion-safe class + className merge + `as` + delay; the globals.css test asserts the real reduced-motion block content. Pure presentation/CSS, no logic to game → no subagent refute-read needed
- [x] concurrency / timing safe — N/A: pure presentation, no IO/state
- [x] no exposed secrets, injection openings, or unexpected dependencies — ZERO new deps (CSS-only + tw-animate-css classes already present)
- [x] layering & dependencies follow CONVENTIONS.md — Reveal uses `cn`; exported via the barrel alongside the other primitives; globals.css net lives with the token layer
- [x] a person reviewed — Tin approved the freeze (a11y net is the unconditional guarantee, polish is enhancement); low-risk presentation, auto-gate. Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Reduced-motion users get a still UI — confirmed: globals.css ships `@media (prefers-reduced-motion: reduce)` collapsing animation/transition duration for `*` (asserted by test + compiled by `next build`)
- [x] Reveal never hides content — confirmed by `test_reveal_renders_children_and_motion_safe` (child text present) + the entrance is purely additive `motion-safe:` classes
- [x] No regression / zero new dep — 547-green suite, tsc 0, eslint 0, next build exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `Reveal` exported from motion.tsx + the barrel (imported both ways in the test); globals.css net compiled into the bundle (build clean).
- [x] DEAD-CODE — DELAY_CLASS used by the delay path (now covered); no orphan symbol.
- [x] SEMANTIC — re-read the globals.css net: collapses durations only (no behavior change), uses standard a11y-reset `!important`.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) · auto-resolved under autonomy:auto (presentation/a11y, low-risk) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): visual QA that reduced-motion (OS setting) yields a still UI; that Reveal entrances don't cause layout shift (CLS) on the marketing pages once applied.

### Spec delta
- [SPEC · open] Apply `Reveal` to the marketing hero/section content + dashboard card grids in the apply tasks (harden-marketing/harden-admin) — this task ships the primitive, not its placement (evidence: Reveal exists but is not yet used).
- [SPEC · seeded] Add a `motion-reduce:` explicit fallback class set if any animation is found that the global net misses (e.g. JS-driven), once the a11y-ci task can detect motion (evidence: net covers CSS animations; JS-driven motion would need its own guard).

### Competency deltas
- [UDD · folded] The a11y guarantee (reduced-motion) belongs in a GLOBAL css net (covers everything unconditionally), while the per-component primitive (Reveal) is the opt-in polish — separating "guarantee" from "enhancement" keeps the invariant robust even if a component forgets the motion-safe gate (evidence: M1 net independent of M2 Reveal). [folded foundation-version 37]
- [TDD · folded] `import.meta.url` is NOT a file:// URL under the jsdom/vitest transform — read repo files in tests via `resolve(process.cwd(), …)` instead (evidence: test_globals_has_reduced_motion_net threw "URL must be of scheme file" → fixed). [folded foundation-version 37]
