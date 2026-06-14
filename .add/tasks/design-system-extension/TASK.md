# TASK: Design System Extension

slug: design-system-extension · created: 2026-06-14 · stage: production
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

Touches (files · symbols · signatures): the FREEZE-FIRST design-system extension for v15 — the additive primitives the coverage surfaces (model-mgmt toggles, settings tabs, guardrail textareas/checkboxes) need. ADDS new files under `apps/dashboard/components/ui/`; touches the barrel + the design-system test project only. Verified the existing house style + the decisive constraint:
- House style (matched verbatim): `components/ui/button.tsx` = `cva()` variants + `React.forwardRef` + `cn` from `@/lib/cn` + semantic token classes + `focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background`; `components/ui/input.tsx` = forwardRef native `<input>` (the simplest pattern, no Radix). Barrel `components/ui/index.ts` re-exports every primitive.
- Tokens available (`app/globals.css` `@theme`, consumed as classes): `primary`/`primary-foreground`/`primary-hover` · `input` · `border` · `background` · `foreground` · `muted`/`muted-foreground` · `ring` · `accent`/`accent-foreground` · `success` · `destructive`. Switch ON=`bg-primary` OFF=`bg-input`, thumb=`bg-background`; Tabs active=`text-foreground`+`border-primary` inactive=`text-muted-foreground`; Checkbox checked=`bg-primary border-primary`; Textarea = Input's box style.
- DECISIVE CONSTRAINT (the v15 risky contract): the installed Radix set is `@radix-ui/react-{dialog,label,select,slot}` only — NOT switch/tabs/checkbox. AND the polyfills Radix needs (`hasPointerCapture`/`scrollIntoView`/`ResizeObserver`) live ONLY in `tests/design-system/primitives.test.tsx`'s `beforeAll`, NOT in `tests/setup.ts` or `tests-bff/setup.ts` (re-confirmed). So a Radix-based primitive would (a) be a NEW dependency requiring an allowlist change-request, and (b) CRASH any surface suite (model-mgmt/settings) that renders it without those polyfills — the exact v13 trap that drove `use-focus-trap` over Radix Dialog (foundation v14 UDD lesson). → HAND-ROLL all four with native elements + ARIA, zero new deps, no shared-setup edit.
- Test harness: vitest 3.2 + jsdom, design-system project at `tests/design-system/`; `primitives.test.tsx` is the render/behavior pattern, `components.test.tsx` the variant pattern, `a11y.test.tsx` the axe pattern, `tokens.test.ts` the token-layer pin. `tests/design-system/allowlist.json` (`test_deps_allowlisted` guard) asserts package.json deps ⊆ the list — hand-rolling keeps it UNTOUCHED.

Context (working folder): the v15 MILESTONE.md "design-system extension" task + its "freeze these first" risky contract; the v13 design-system foundation (foundation v14) this extends. The four state patterns + WCAG 2.2 AA floor + the `within(section)` RTL convention all carry from v13.

Honors (patterns / conventions): foundation v14 §Users (assert the SUBSTANTIVE a11y guarantee via labelled + keyboard-operable native controls, not a Radix attribute / not a polyfill-needing dep); CONVENTIONS.md (forwardRef + cn + cva house style; token classes not hardcoded values; axe-in-jsdom = impact serious|critical + color-contrast disabled; run the coverage gate).

Anchors the contract cites: NEW `components/ui/{switch,tabs,textarea,checkbox}.tsx` (hand-rolled, native+ARIA) · the `components/ui/index.ts` barrel · the semantic token classes above · the a11y guarantees (Switch=`role="switch"`+`aria-checked`+keyboard; Tabs=WAI-ARIA roving-tabindex `role="tablist/tab/tabpanel"`+arrow/Home/End+`aria-selected`+`aria-controls`; Checkbox=native `<input type="checkbox">`; Textarea=native `<textarea>`) · the `tests/design-system/` render+a11y suites · the UNCHANGED `allowlist.json` (zero new dependency).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Design-system extension — the additive shared UI primitives v15's coverage surfaces need (Switch, Tabs, Textarea, Checkbox), built in the v13 house style (forwardRef + cn + cva + semantic tokens), hand-rolled on native elements + ARIA so they render in EVERY test project with NO new dependency and NO shared-setup polyfill.
Framings weighed: Hand-roll on native elements + ARIA (chosen — zero new dep, no polyfill trap, renders in any suite, full a11y control; the v14/v13 use-focus-trap precedent) · Adopt Radix react-switch/tabs/checkbox (rejected — 3 new deps + allowlist change-request AND they need hasPointerCapture/scrollIntoView/ResizeObserver polyfills absent from tests/setup.ts + tests-bff/setup.ts → would crash the model-mgmt/settings surface suites) · Add the polyfills to the shared setups so Radix works everywhere (rejected for now — a broad-blast-radius infra edit to two shared setups for a benefit native elements already give; revisit only if a primitive genuinely needs Radix).

Must:
<must>
  - Switch — a controlled toggle rendered as a native `<button role="switch">` with `aria-checked` reflecting the `checked` prop; fires `onCheckedChange(next: boolean)` on click AND on Space/Enter; focusable with the house focus-visible ring; `disabled` blocks toggle + dims; ON=`bg-primary` / OFF=`bg-input` track, `bg-background` thumb (tokens only); labellable by the caller (aria-label/aria-labelledby/htmlFor via id).
  - Tabs — the WAI-ARIA tabs pattern: `Tabs` (controlled `value`/`onValueChange` OR uncontrolled `defaultValue`) · `TabsList` `role="tablist"` · `TabsTrigger` `role="tab"` with `aria-selected` + `aria-controls` + roving tabindex (only the active trigger is tabbable) · `TabsContent` `role="tabpanel"` with `aria-labelledby` its trigger. ArrowLeft/ArrowRight move+activate between triggers (wrapping), Home/End jump to first/last, click selects; only the active panel is rendered/visible. Automatic activation (focus follows selection).
  - Textarea — a forwardRef native `<textarea>` carrying the Input box style (border-input, focus-visible ring, disabled styles), accepts typing.
  - Checkbox — a forwardRef native `<input type="checkbox">`, styled (checked accent = `bg-primary`/`border-primary`), toggles on click + Space, labellable, accepts `checked`/`onChange`.
  - All four are exported from the `components/ui/index.ts` barrel and follow the house style (forwardRef where it is a single DOM node, `cn` merge, `displayName`).
  - Each primitive passes an axe scan (ZERO serious/critical, color-contrast excluded in jsdom) and is keyboard-operable; no hardcoded hex/px a token covers; ZERO new npm dependency (`tests/design-system/allowlist.json` + package.json UNCHANGED); the full behavioral floor stays green; coverage ≥ 80%.
</must>
Reject:
<reject>
  - A primitive built on a dependency that needs a polyfill absent from the shared test setups (would crash surface suites) -> "polyfill_dependency"
  - Any new npm dependency or an edit to `allowlist.json`/package.json deps -> "unlisted_dependency"
  - A primitive with a serious/critical axe violation, or not operable by keyboard (Switch Space/Enter; Tabs Arrow/Home/End; Checkbox Space) -> "a11y_inoperable"
  - A hardcoded raw color/space value that a `@theme` token covers -> "untokened_value"
  - A primitive not re-exported from the `components/ui` barrel (surfaces import from `@/components/ui`) -> "unbarrelled_primitive"
</reject>
After:
<after>
  - `components/ui/{switch,tabs,textarea,checkbox}.tsx` exist, are barrel-exported, render + keyboard-operable + axe-clean + token-driven, with ZERO new dependency; the design-system suite proves each; the full suite (122 prior + new) is green, coverage ≥ 80%, lint clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ hand-rolled Tabs roving-tabindex honors the WAI-ARIA pattern as faithfully as Radix would — LOWEST confidence because manual roving focus + Arrow/Home/End + aria-selected/aria-controls + panel labelling is the one non-trivial primitive where subtle a11y gaps hide (focus not following selection; panel missing aria-labelledby; tabindex not roving). If wrong: it is a markup/keyboard-handler fix in `tabs.tsx`, caught by the EXPLICIT keyboard + axe tests, no API change. Mitigation: test ArrowLeft/Right (with wrap) + Home/End + aria-selected + tabpanel labelling directly.
  - [ ] native checkbox/textarea + a button-based switch are fully a11y in jsdom WITHOUT polyfills — CONFIRMED (native elements need no pointer/observer APIs; the v13 Select polyfills were Radix-internal, not native-element requirements).
  - [ ] the existing `@theme` tokens cover the new primitives' palette (no new token needed) — CONFIRMED from the token list (primary/input/border/background/ring/muted all present).
  - [ ] Tabs activation = AUTOMATIC (focus follows arrow selection) not manual — chosen because settings/coverage panels are cheap to render; manual activation only pays off for expensive panels, which these are not.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Switch toggles by pointer and keyboard
  Given a controlled Switch with checked=false and an onCheckedChange spy
  When the user clicks it, then focuses it and presses Space
  Then it has role="switch", aria-checked reflects state, and onCheckedChange fired with the next boolean each time
  And a disabled Switch does NOT fire onCheckedChange on click

Scenario: Switch is accessibly labelled and axe-clean
  Given a Switch with an associated label
  When axe scans the container
  Then there are zero serious/critical violations and the switch is reachable by its accessible name

Scenario: Tabs select by click and show the matching panel
  Given Tabs with three TabsTrigger + TabsContent pairs, defaultValue the first
  When the user clicks the second trigger
  Then the second trigger has aria-selected=true, its tabpanel (aria-labelledby that trigger) is shown, and the others' panels are not

Scenario: Tabs keyboard navigation (arrows + Home/End, roving tabindex)
  Given Tabs with three triggers and the first active
  When the user focuses the active trigger and presses ArrowRight, then End, then ArrowRight (wrap), then Home
  Then focus+selection move next / to last / wrap to first / to first respectively, and only the active trigger is tabbable (roving tabindex) -> else "keyboard_inoperable"
  And the tablist has role=tablist and each trigger role=tab with aria-controls

Scenario: Tabs are axe-clean
  Given an open Tabs with a labelled tablist
  When axe scans the container
  Then there are zero serious/critical violations

Scenario: Textarea is a native textarea that accepts typing
  Given a Textarea with an accessible label
  When the user types into it
  Then it is a <textarea>, holds the typed value, and is axe-clean

Scenario: Checkbox toggles by pointer and keyboard
  Given a labelled Checkbox with an onChange spy
  When the user clicks it, then focuses and presses Space
  Then it is a native input[type=checkbox], checked flips each time, onChange fired, and it is axe-clean

Scenario: Primitives are barrel-exported and add no dependency
  Given the components/ui barrel and package.json
  When the new primitives are imported from "@/components/ui"
  Then Switch, Tabs/TabsList/TabsTrigger/TabsContent, Textarea, Checkbox all resolve -> else "unbarrelled_primitive"
  And tests/design-system/allowlist.json and package.json dependencies are UNCHANGED -> else "unlisted_dependency"

Scenario: Behavioral floor stays green
  Given the full vitest suite plus the new primitive tests
  When it runs with coverage
  Then all tests pass (122 prior + new) and coverage >= 80%
  And no existing primitive, surface, or shared setup was modified
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# COMPONENT-API contract (no HTTP). Freezes the public PROP SHAPES + a11y guarantees of
# four hand-rolled primitives. All in apps/dashboard/components/ui/, native elements + ARIA,
# ZERO new dependency, exported from the components/ui barrel.

switch.tsx ───────────────────────────────────────────────────────────────────
  export interface SwitchProps
    extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onChange" | "type"> {
    checked: boolean
    onCheckedChange?: (next: boolean) => void
    disabled?: boolean
  }
  export const Switch: forwardRef<HTMLButtonElement, SwitchProps>
  GUARANTEE: <button type="button" role="switch" aria-checked={checked}>; click + Space/Enter
    -> onCheckedChange(!checked) (NOT when disabled); focus-visible ring; ON bg-primary / OFF
    bg-input track, bg-background thumb; caller labels via aria-label/aria-labelledby/id.

tabs.tsx ─────────────────────────────────────────────────────────────────────
  export interface TabsProps { value?: string; defaultValue?: string;
    onValueChange?: (v: string) => void; children: React.ReactNode; className?: string }
  export const Tabs        // controlled (value) OR uncontrolled (defaultValue); Context-provided
  export const TabsList    // role="tablist"; arrow/Home/End handled here
  export interface TabsTriggerProps { value: string; children; className?: string; disabled?: boolean }
  export const TabsTrigger // role="tab" aria-selected aria-controls=<panelId> id=<tabId>
                           // roving tabindex: active=0, others=-1
  export interface TabsContentProps { value: string; children; className?: string }
  export const TabsContent // role="tabpanel" aria-labelledby=<tabId> id=<panelId>; rendered only when active
  GUARANTEE: ArrowLeft/Right move+activate (wrap), Home/End first/last, click selects (automatic
    activation); panel id/tab id deterministic per value so aria-controls/labelledby pair up.

textarea.tsx ─────────────────────────────────────────────────────────────────
  export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>
  export const Textarea: forwardRef<HTMLTextAreaElement, TextareaProps>
  GUARANTEE: native <textarea> with the Input box style (border-input, focus-visible ring, disabled).

checkbox.tsx ─────────────────────────────────────────────────────────────────
  export type CheckboxProps = React.InputHTMLAttributes<HTMLInputElement>  // type forced to "checkbox"
  export const Checkbox: forwardRef<HTMLInputElement, CheckboxProps>
  GUARANTEE: native <input type="checkbox"> styled (checked accent bg-primary/border-primary),
    toggles on click + Space; caller labels via id/aria-label.

barrel: components/ui/index.ts re-exports Switch · Tabs/TabsList/TabsTrigger/TabsContent · Textarea · Checkbox.

ACCEPTANCE BAR (the gate): each primitive renders + is keyboard-operable per its GUARANTEE +
  axe(container) ZERO serious|critical (color-contrast disabled, jsdom) + token classes only +
  package.json/allowlist.json UNCHANGED + full suite green + coverage >= 80%.
Reject codes: polyfill_dependency · unlisted_dependency · a11y_inoperable · untokened_value · unbarrelled_primitive
Schema: NONE — additive UI primitives; no DB/route/data/contract change; no existing primitive or shared setup edited.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, v15 freeze-first design-system extension)

**Least-sure flag surfaced at freeze:** `[contract]` — the Tabs prop/behavior shape (roving-tabindex WAI-ARIA
hand-roll vs Radix Tabs). *Why it's the riskiest call:* the four-component API is otherwise mechanical (Switch/
Checkbox/Textarea mirror existing native primitives), but Tabs hand-rolls roving focus + Arrow/Home/End + the
aria-controls/labelledby id pairing — the one place a subtle a11y gap (focus not following selection, panel
unlabelled, tabindex not roving) could hide. *Cost if wrong:* a markup/keyboard-handler fix inside `tabs.tsx`,
caught by the explicit keyboard + axe tests (§4) — the FROZEN prop shape (value/defaultValue/onValueChange +
the four sub-components) does not change. Honest-disclosure mitigation: §4 tests Arrow/Home/End + wrap +
aria-selected + tabpanel labelling directly, so a gap is a found-and-fixed build bug, not a contract change.
Second-most unsure `[spec]`: the choice to hand-roll over Radix — if a future surface needs a Radix-only
behavior, that is a NEW change-request (add the dep + the shared-setup polyfills), not a silent reversal here.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% line (held; new primitives are render+behavior tested). TRUE-RED reason: the four `components/ui/{switch,tabs,textarea,checkbox}.tsx` modules do not exist → every import is MODULE_NOT_FOUND until Build. The suite lives in the design-system project (it carries the Radix-Select polyfills already, but the hand-rolled primitives need NONE — proving they work polyfill-free is part of the point; the surface suites in v15's later tasks will render them in legacy/bff without polyfills).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  In `apps/dashboard/tests/design-system/extension.test.tsx` (design-system project):
  - test_switch_toggles_pointer_keyboard: render controlled Switch checked=false + onCheckedChange spy / click, then focus+Space / assert role=switch, aria-checked flips, spy called with next bool each time; disabled Switch click → spy NOT called
  - test_switch_labelled_axe_clean: render Switch with a <label htmlFor> / assert reachable by accessible name + axeSeriousCritical == []
  - test_tabs_select_by_click: render 3 Tabs trigger/content pairs defaultValue first / click 2nd trigger / assert 2nd aria-selected=true + its tabpanel shown (aria-labelledby its trigger) + others' panels absent
  - test_tabs_keyboard_roving: focus active trigger / ArrowRight, End, ArrowRight(wrap), Home / assert focus+selection move next/last/first/first; only active trigger tabindex=0 (others -1); tablist role + each trigger aria-controls
  - test_tabs_axe_clean: axeSeriousCritical(container) == []
  - test_textarea_native_typeable_axe: render labelled Textarea / type / assert tagName TEXTAREA + value + axe clean
  - test_checkbox_toggles_pointer_keyboard_axe: render labelled Checkbox + onChange spy / click, focus+Space / assert input[type=checkbox], checked flips, spy fired, axe clean
  - test_primitives_barrel_exported: import { Switch, Tabs, TabsList, TabsTrigger, TabsContent, Textarea, Checkbox } from "@/components/ui" / assert all defined
  - test_no_new_dependency: read package.json deps+devDeps / assert ⊆ tests/design-system/allowlist.json (the existing test_deps_allowlisted guard already enforces this milestone-wide; this pins it for the new primitives)
</test_plan>

Tests live in: `apps/dashboard/tests/design-system/extension.test.tsx` · MUST run red (modules absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/` `apps/dashboard/tests/design-system/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/design-system-extension/`
<!-- New primitives live in components/ui/ (switch/tabs/textarea/checkbox.tsx) + the barrel index.ts;
     the RED suite + this task's TASK.md. .next/coverage/tsbuildinfo are the gitignored tsc/build
     artifacts the scope-lock flags (engine _SCOPE_EXCLUDE_DIRS = .git/.add/__pycache__/node_modules).
     NO existing primitive, NO surface, NO shared setup (tests/setup.ts, tests-bff/setup.ts), NO
     package.json, NO allowlist.json — touching any of those is scope_creep / unlisted_dependency. -->
Strategy (ordered batches): 1. textarea.tsx + checkbox.tsx (trivial native). 2. switch.tsx (button role=switch + keyboard). 3. tabs.tsx (Context + roving-tabindex TabsList/Trigger/Content). 4. barrel exports. 5. run the design-system suite green, then full-suite + coverage + lint gate.
Safety rule (feature-specific): native elements + ARIA only — NO new dependency, NO shared-setup polyfill; token classes only (no raw hex/px).
Code lives in: `apps/dashboard/components/ui/`
Constraints: do NOT change any test or the contract; allow-list packages only (none added); ask if unclear.
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 132/132 green (122 prior + 10 new in `tests/design-system/extension.test.tsx`); full vitest run across both projects clean.
- [x] coverage did not decrease — 91.06% line (≥ 80% gate held; the four new primitives are 100% line-covered; coverage gate exit=0).
- [x] no test or contract was altered during build — the only test file touched is the NEW `extension.test.tsx`; the re-cross ritual was used twice (userEvent brace fix + DEFECT-2 fix) so the tripwire re-snapshotted legitimately; §3 contract UNCHANGED post-freeze.
- [x] the green was EARNED, not gamed — adversarial refute-read (subagent, model sonnet) VERDICT EARNED-WITH-GAPS: found DEFECT 2 (real component bug — ArrowUp/Down cycled the horizontal tablist, against WAI-ARIA APG) + 3 test gaps (vacuous aria-controls check, ArrowLeft untested, roving-tabindex update unasserted). ALL closed: Up/Down removed from the keydown handler + `test_tabs_arrowup_down_do_not_navigate` added; `test_tabs_keyboard_roving` strengthened (ArrowLeft + wrap, roving-tabindex-update assertions, non-vacuous active-tab aria-controls→panel→aria-labelledby cross-link). DEFECT 1 (inactive TabsContent unmounts → its trigger's aria-controls points at a non-rendered panel) KEPT as a deliberate Radix-Tabs-aligned choice (matches the most-trusted lib, passes axe, honors the frozen "only active panel rendered") — recorded here, not silently passed.
- [x] concurrency / timing of the risky operation is safe — N/A for pure client primitives; the only stateful flows are React controlled/uncontrolled state (Tabs Context) + roving focus, both synchronous and asserted by the keyboard tests.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; ZERO new npm dependency (package.json + `tests/design-system/allowlist.json` UNCHANGED; node-deps allow-list 34 clean); no raw-HTML injection sink (all rendering is React-escaped text/children); the shared setups (`tests/setup.ts`, `tests-bff/setup.ts`) are UNTOUCHED.
- [x] layering & dependencies follow CONVENTIONS.md — forwardRef + `cn` + token classes house style matched; hand-rolled native+ARIA (no Radix, no polyfill trap) per foundation v14 UDD lesson; axe-in-jsdom = impact serious|critical + color-contrast disabled.
- [x] a person reviewed and approved the change — Tin (delegated auto mode, v15 freeze-first) + adversarial subagent refute-read; all actionable findings closed before gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: all four primitives + the Tabs sub-components are re-exported from `components/ui/index.ts` and imported/exercised in `extension.test.tsx` (`test_primitives_barrel_exported` pins the barrel). They are the building blocks v15 tasks 2–6 consume.
- [x] DEAD-CODE (code) — no orphaned symbol: `tabId`/`panelId` helpers + the `TabsContext`/`useTabs` pair are all consumed within `tabs.tsx`; no exported symbol is unreferenced by the suite.
- [x] SEMANTIC (prose / non-code) — read in full: §1–§4 of this TASK.md + the frozen contract + the adversarial review report. Confirmed the impl honors the GUARANTEE lines (Switch role/aria-checked/keyboard; Tabs roving-tabindex + Arrow/Home/End + aria-controls/labelledby pairing; native Textarea/Checkbox) and the frozen prop shapes did NOT change.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto) + adversarial refute-read subagent · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
