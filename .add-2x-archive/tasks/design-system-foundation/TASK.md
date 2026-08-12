# TASK: Freeze shared design tokens + component variants + state components + responsive/a11y primitives + shell

slug: design-system-foundation · created: 2026-06-13 · stage: production
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

Touches (files · symbols · signatures):
- `apps/dashboard/app/globals.css` — TODAY literally one line `@import "tailwindcss";`. NO `@theme`, no CSS vars, no token layer. This is where the wired CSS-var token layer lands (Tailwind v4 CSS-first; there is NO `tailwind.config.{js,ts}` anywhere — confirmed).
- `apps/dashboard/app/layout.tsx` — `RootLayout` (`"use client"`); `<html lang="en"><body><QueryClientProvider>`. NO `<nav>`/`<main>`/skip-link/landmark, no theme class. The responsive a11y shell lands here.
- `apps/dashboard/package.json` — deps: next 15.3.3, react 19.1.0, @tanstack/react-query 5.80.5, lucide-react, **class-variance-authority 0.7.1 + clsx 2.1.1 + tailwind-merge 3.3.0 (all installed, NEVER imported)**, tailwindcss 4.1.8. NO shadcn/ui, NO @radix-ui/*, NO @tremor/react, NO chart lib. Test stack: vitest 3.2.3 + @testing-library/react 16 + jsdom + msw 2.
- NEW `apps/dashboard/components/ui/` (does NOT exist yet) — the shared primitive layer: `cn()` util (lib/), Button/Card/Dialog/Table/Badge/Input/Select + the standard state components (Loading/Empty/Error/Success).
- Surface components the foundation must NOT break (presentation-only): `components/usage/{UsagePage,UsageStatsCards,UsageTable,BudgetWidget,BudgetEditForm}.tsx`, `components/spend/SpendPage.tsx`, `components/keys/{KeysPage,KeyRow,CreateKeyDialog,KeyGovernanceEditor,PlaintextKeyBanner}.tsx`. Current styling = bare HTML + ~13 raw Tailwind utility strings total; most components have ZERO className.
- Data seam to preserve verbatim: `lib/api-client.ts:apiGet/apiPut`, `lib/bff-client.ts:bffGet/bffPost/bffPatch/bffDelete`, `lib/hooks/use-current-user.ts:useCurrentUser` (`{role}` → `canEdit`). All hit `GET/PUT/PATCH /api/gw/[...path]` (`app/api/gw/[...path]/route.ts`, untouched). FROZEN field-name distinction: tenant budget body `budget_usd_monthly` vs per-key governance body `monthly_budget_usd` (wrong name = silent no-op; asserted in govern.test.tsx TEST 1).
- NEW UDD foundation (this task's design contract): `.add/design/tokens.json` (3-layer), `.add/design/catalog.json` (component catalog), `.add/design/prototypes/*.json` (flat json-render trees), `DESIGN.md` (prose front-door). Linted by `add.py check` → `_token_layer_violations` + `_catalog_tree_violations` + udd-check-lint cross-file resolution.

Context (working folder):
- `.add/milestones/v13/MILESTONE.md` — v13 scope/exit criteria; NOTE its "shadcn/ui + Tremor + dark-mode-first + design tokens" premise is ASPIRATIONAL — none exist in code (ground-truth correction recorded). User direction (2026-06-13): install shadcn/ui+Radix · ADD a chart lib · themeable token layer shipping LIGHT default (no toggle).
- Test suites that gate behavior-preservation: `tests/usage.test.tsx` (T20–35), `tests/keys.test.tsx` (T7–14), `tests-bff/govern.test.tsx` (T1–10 + SpendPage T12–17), `tests-bff/{bff-client,bff-forms,middleware,use-current-user,route-handlers}.test.tsx`. They key on `role`/`aria`/exact text/`data-testid` — NOT CSS classes. One fragile coupling: loading detection falls back to `document.querySelector(".animate-pulse, .animate-spin")` (T10/T23) — the loading state component MUST keep `role="status"`+`aria-busy="true"` OR an `animate-pulse/spin` class.
- 1.3.0 UDD docs: `.add/tooling/templates/udd-tokens.md` (DTCG-2025.10 compact dialect + 6 named reds), `udd-catalog.md` (catalog/tree dialect + 8 named reds + json-render v0.19.0 `Spec` shape), `tokens.sample.json`, `catalog.sample.json`, `prototype.sample.json`, `DESIGN.md.tmpl`.
- Config: no eslint/tailwind blocker; `apps/dashboard/eslint.config.mjs`, `vitest.config.ts`, `next.config.ts` present and unchanged.

Honors (patterns / conventions):
- CONVENTIONS.md (v1 UDD fold): **state pattern** — every surface renders loading · empty · error · success; scenario observables anchor WHERE state appears → RTL scopes with `within(section)`, never bare global `getByText`.
- MILESTONE.md shared decisions: **design token** = a named themeable primitive defined ONCE, consumed everywhere (no surface hardcodes a value a token covers); **behavior-preserving / data-identical is non-negotiable** (same BFF route + same TanStack hook + same field names; existing BFF+RTL tests stay green); **a11y floor = WCAG 2.2 AA** (axe-core zero serious/critical + keyboard-operable; AA contrast).
- udd-tokens.md fail-closed citation rule: primitive=literal only · semantic cites primitive only · component cites semantic only (no skip/sideways/upward). Identity values (brand color/typeface) are HUMAN-OWNED — surfaced at specify, flagged at the freeze.

Anchors the contract cites: `.add/design/tokens.json` (3-layer + `$type`s) · `.add/design/catalog.json` + `.add/design/prototypes/dashboard-foundation.json` · `apps/dashboard/lib/cn.ts:cn` · `apps/dashboard/components/ui/*` · `apps/dashboard/app/globals.css` `@theme` wiring · `apps/dashboard/app/layout.tsx:RootLayout`/`AppShell` · the PRESERVED data seam + test-observable surface. Detail:
- `.add/design/tokens.json` — the 3 layers (`primitive` · `semantic` · `component`) and the supported `$type`s (color · dimension · number · fontFamily · fontWeight · duration).
- `.add/design/catalog.json` — the component variant inventory: Button/Card/Dialog/Table/Badge/Input/Select + state components (Loading/Empty/Error/Success) + shell (AppShell/Nav/SkipLink) with typed props bound to `{semantic.*}` tokens.
- `apps/dashboard/lib/cn.ts:cn` (NEW) · `apps/dashboard/components/ui/*` (NEW) · `apps/dashboard/app/globals.css` `@theme`/CSS-var token wiring · `apps/dashboard/app/layout.tsx:RootLayout` shell.
- The PRESERVED contracts §3 must not break: the data seam (api/bff client fns + field names) and the test-observable surface (roles/aria/text/testids enumerated above).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Hydroa dashboard design-system foundation — a frozen 3-layer UDD token layer + a shadcn/ui+Radix shared primitive layer + standard state components + a responsive WCAG-2.2-AA shell, all consuming one token source. The FREEZE-FIRST contract the v13 surface redesigns build against. Identity (human-owned, set this phase): accent **indigo-600 #4F46E5**, **slate** neutral ramp, **Inter** typeface, **light** default with a themeable CSS-var layer (no toggle ships).

Framings weighed: token-first design system (`.add/design/tokens.json` 3-layer → wired into Tailwind v4 `@theme`/CSS vars → consumed by `components/ui/` on shadcn/Radix + state components + a11y shell) **(chosen)** · shadcn-CLI-defaults-only — no frozen UDD token contract, surfaces free-drift (rejected: fails the milestone's "design system frozen first" requirement) · bespoke cva primitives without Radix — full control but I hand-roll dialog focus-trap (rejected: risk against the AA gate; user chose shadcn+Radix).

Must:
<must>
  - Define `.add/design/tokens.json` as a 3-layer set (primitive · semantic · component) that lints clean under `add.py check` (`_token_layer_violations == []`); identity wired through semantic layer (accent→indigo-600, surface/text/border→slate ramp, font→Inter); honors the fail-closed citation rule (primitive=literal, semantic→primitive, component→semantic).
  - Wire the token layer into the running app as the SINGLE source: `apps/dashboard/app/globals.css` `@theme`/CSS custom properties; the primitive/state/shell components reference only token-backed classes — no raw hex/px a token covers.
  - Provide `apps/dashboard/components/ui/` shared primitives on shadcn/ui + Radix, variant-typed via `class-variance-authority` + a `lib/cn.ts` `cn()` util: Button, Card, Dialog, Table, Badge, Input, Select (the variant inventory the surfaces consume).
  - Provide standard state components (Loading, Empty, Error, Success) implementing the v1 state-pattern, capable of rendering the existing test-observable markers (`role="status"`+`aria-busy="true"` for loading; `role="alert"` for error; caller-supplied empty/success copy).
  - Provide the responsive a11y shell in `apps/dashboard/app/layout.tsx`: skip-link to `#main`, `<nav>` landmark, `<main id="main">` landmark, visible `focus-visible` ring token, landmark roles; responsive nav frame (desktop sidebar → tablet/mobile collapsed) — while keeping `QueryClientProvider`.
  - Declare `.add/design/catalog.json` (typed props bound to `{semantic.*}`) + ≥1 `.add/design/prototypes/*.json` flat json-render tree that lints clean against it (cross-file resolution green under udd-check-lint) + `DESIGN.md` prose front-door carrying the identity.
  - Add new runtime deps to an allow-list and `apps/dashboard/package.json`: shadcn/ui-generated components, `@radix-ui/*` primitives, the charting substrate (**Recharts**, the shadcn-charts base — themeable via CSS vars), `next/font` Inter; the chart primitive is available for usage-cost-ui to consume later (no chart rendered on a data surface in THIS task).
  - PRESERVE behavior/data: existing `apps/dashboard/tests/*` + `tests-bff/*` suites stay green; no data hook, BFF route (`/api/gw/[...path]`), or field name (`budget_usd_monthly` vs `monthly_budget_usd`) changes; this task does NOT migrate the existing surfaces (that is usage-cost-ui / key-budget-governance-ui).
</must>
Reject:
<reject>
  - a token file violating the layer/citation/shape rules -> "udd_token_layer_violation"  (the 6 named reds: unknown_layer · unknown_type · unresolved_alias · cross_layer_citation · primitive_has_alias · malformed_value)
  - a catalog/prototype pair that does not resolve -> "udd_catalog_tree_violation"  (the 8 named reds incl. tree_cites_uncataloged_component · non_semantic_prop_token · prop_type_mismatch · missing_root)
  - a primitive/state/shell component hardcoding a value a token covers (raw hex/px) -> "untokenized_value"
  - a presentation change that breaks an existing behavioral assertion (role / aria / exact text / data-testid / field name) -> "behavior_regression"
  - a shell/primitive failing the a11y floor (missing focus-visible, missing landmark, axe serious/critical, non-keyboard-operable dialog) -> "a11y_floor_violation"
  - a runtime dependency not on the build allow-list -> "unlisted_dependency"
</reject>
After:
<after>
  - `.add/design/{tokens.json,catalog.json,prototypes/*.json}` + `DESIGN.md` exist and `add.py check` is green for the design set (no named red).
  - `apps/dashboard/lib/cn.ts` + `apps/dashboard/components/ui/*` + the wired `globals.css` token layer + the `app/layout.tsx` a11y shell exist and render.
  - The full dashboard suite (`vitest run`) is green — zero behavioral regression — plus a new baseline-render test and a token/catalog-lint assertion pass; the new shell passes an axe-core scan (zero serious/critical).
  - §3 design-system contract is FROZEN; usage-cost-ui and key-budget-governance-ui can build against it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] json-render v0.19.0 `Spec`/catalog dialect coupling is the top risk — `catalog.json` + the prototype tree must mirror vercel-labs/json-render's `Spec` exactly (the milestone's own named top risk: young-project schema drift). If the pinned shape diverges from what `_catalog_tree_violations` expects, the cross-file lint goes red. If wrong: rework confined to `.add/design/` (no app code). Mitigation: author straight from `catalog.sample.json` + `prototype.sample.json`, which validate clean, and lint incrementally.
  - [ ] ⚠ [spec] scope boundary — "freeze the design system" must NOT also migrate the existing usage/keys/spend surfaces now; this task proves the foundation renders + existing tests stay green only. If misread: scope balloons ~3x and collides with the two surface tasks.
  - [x] [test] the loading-state fallback selector `.animate-pulse/.animate-spin` — the new Loading component keeps `role="status"`+`aria-busy`, so T10/T23 hold; low stakes.
  - [x] [contract] identity values confirmed by the user this phase (indigo-600 · slate · Inter · light); low stakes.
  - [x] [contract] chart substrate = Recharts (shadcn-charts base, CSS-var themeable); reversible, no data surface depends on it yet; low stakes.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- Musts ---
Scenario: Token layer lints clean (M1)
  Given .add/design/tokens.json with 3 layers and identity wired (accent→indigo-600, slate ramp, Inter)
  When `python3 .add/tooling/add.py check` runs over the design set
  Then it reports no token-layer named red (_token_layer_violations == [])
  And semantic.color.accent resolves through a primitive to "#4F46E5"

Scenario: Tokens are the single wired source (M2)
  Given globals.css carries the token layer as @theme/CSS custom properties
  When the dashboard builds and a primitive renders
  Then the primitive's color/space come from a CSS var (var(--color-…)/token class), not a raw literal
  And no components/ui or shell file contains a raw hex/px that a token covers

Scenario: Shared primitives exist and are token-backed (M3)
  Given components/ui/{button,card,dialog,table,badge,input,select} on shadcn/Radix + lib/cn.ts
  When a Button renders with variant="default" and a Dialog opens
  Then the Button uses the cva variant + cn() merge and token classes
  And the Dialog is keyboard-operable (focus trap, ESC closes) via Radix

Scenario: Standard state components preserve observable markers (M4)
  Given the shared Loading/Empty/Error/Success state components
  When Loading renders
  Then it exposes role="status" and aria-busy="true"
  And Error renders role="alert" with caller-supplied copy

Scenario: Responsive a11y shell (M5)
  Given app/layout.tsx renders the shell around children
  When the page loads
  Then there is a skip-link to #main, a <nav> landmark, and <main id="main">
  And a focus-visible ring is present and QueryClientProvider still wraps children

Scenario: Catalog + prototype resolve cross-file (M6)
  Given .add/design/catalog.json (typed props → {semantic.*}) and a prototypes/*.json flat tree
  When `add.py check` runs udd-check-lint cross-file resolution
  Then no catalog/tree named red is reported
  And DESIGN.md records the identity and indexes the prototype

Scenario: New deps are allow-listed and behavior is preserved (M7)
  Given shadcn/ui + @radix-ui/* + recharts + next/font added to the build allow-list and package.json
  When `vitest run` executes the full dashboard suite
  Then every existing tests/* and tests-bff/* assertion passes (zero behavioral regression)
  And no BFF route, data hook, or field name changed

# --- Rejects ---
Scenario: Token layer violation is rejected (R1)
  Given a tokens.json where a primitive's $value is an alias
  When `add.py check` runs
  Then it reports "udd_token_layer_violation" (primitive_has_alias)
  And the named-set lint stays red until fixed (no silent pass)

Scenario: Unresolved catalog/prototype is rejected (R2)
  Given a prototype element citing a component not in catalog.json
  When `add.py check` runs
  Then it reports "udd_catalog_tree_violation" (tree_cites_uncataloged_component)
  And the existing token layer lint is unaffected (separate validators)

Scenario: Untokenized value is rejected (R3)
  Given a components/ui component hardcoding "#4F46E5" or "16px" that a token covers
  When the token-usage check runs over components/ui + shell
  Then it flags "untokenized_value"
  And token-backed siblings remain valid

Scenario: Behavioral regression is rejected (R4)
  Given a presentation change that drops a role/aria/exact-text/data-testid an existing test asserts
  When `vitest run` executes
  Then the affected existing assertion FAILS surfacing "behavior_regression"
  And the data seam (field names, BFF route) is unchanged — the fix restores the marker, never weakens the test

Scenario: A11y floor violation is rejected (R5)
  Given the shell/dialog missing a focus-visible ring or a landmark
  When the axe-core baseline scan runs
  Then it reports a serious/critical violation → "a11y_floor_violation"
  And no token/behavioral assertion is masked by it

Scenario: Unlisted dependency is rejected (R6)
  Given a runtime import of a package not on the build allow-list
  When the build/verify allow-list check runs
  Then it flags "unlisted_dependency"
  And the allow-listed deps continue to resolve
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a design-system foundation, so the "shape" is: the UDD named-set file structure + identity
values, the shared component interfaces (cva variant signatures + Radix parts), the a11y shell
structure, the token→CSS-var wiring convention, the dependency allow-list, and the PRESERVED
seams. The lint (`add.py check`) + the test suite are the enforcement; this freezes WHAT, not how.

```
A. UDD NAMED SET  (.add/design/ — linted by `add.py check`; clean = no named red)
   tokens.json   3 layers, fail-closed citation (primitive=literal · semantic→primitive · component→semantic)
     primitive.color   : slate {50,100,200,300,400,500,600,700,800,900,950} · indigo {500,600,700}
                          · white #FFFFFF · emerald-600 · amber-500 · red-600   ($type color, "#RRGGBB")
     primitive.space   : {0.5,1,2,3,4,5,6,8,10,12,16} → "<n>px" rem-equiv   ($type dimension)
     primitive.radius  : {sm,md,lg,full}              ($type dimension)
     primitive.font.family.sans = ["Inter","system-ui","sans-serif"]  ($type fontFamily)
     primitive.font.weight = {regular 400, medium 500, semibold 600, bold 700}  ($type fontWeight)
     primitive.font.size   = {xs,sm,base,lg,xl,2xl,3xl} → "<n>rem"   ($type dimension)
     primitive.motion.duration = {fast 150ms, base 200ms}  ($type duration)
     semantic.color  : accent→indigo-600 · accent-hover→indigo-700 · accent-ring→indigo-500
                        · surface→white · surface-muted→slate-50 · text→slate-900 · text-muted→slate-500
                        · text-on-accent→white · border→slate-200 · success→emerald-600 · warning→amber-500
                        · danger→red-600 · focus-ring→indigo-500   (each $value an alias to a primitive)
     semantic.space  : inset-{xs,sm,md,lg} · gap-{sm,md}        (→ primitive.space)
     semantic.radius : control · card                          (→ primitive.radius)
     semantic.font   : family.base→sans · weight.{normal,emphasis,strong} · size.{body,heading,display}
     component.{button,card,dialog,input,select,badge,table} : parts cite semantic only, e.g.
        button.bg→{semantic.color.accent} · button.bg-hover→{semantic.color.accent-hover}
        · button.label→{semantic.color.text-on-accent} · button.padding→{semantic.space.inset-md}
        · button.radius→{semantic.radius.control} · button.ring→{semantic.color.focus-ring}
        (card/dialog/input/select/badge/table follow the same surface/text/border/radius pattern)
   catalog.json  components{} with typed props bound to {semantic.*} (PropSpec: string|number|boolean|enum|token)
     declares: Screen(hasChildren) · Card(hasChildren) · Heading · Text · Button · Badge · StatCard · Field
   prototypes/dashboard-foundation.json  flat json-render Spec (root + elements), lints clean vs catalog.json
   DESIGN.md  prose front-door: Identity (indigo-600 · slate · Inter · light · voice: precise·calm)
              · Principles · a11y floor (WCAG 2.2 AA · focus-visible · hit-target ≥44px) · Screens index

B. WIRING  (apps/dashboard/)
   app/globals.css   `@import "tailwindcss";` + `@theme { --color-*, --radius-*, --font-* }` mapping the
                     semantic/component tokens to Tailwind v4 theme vars (the SINGLE source). Light values
                     live on :root; a `.dark` block is scaffolded (themeable) but light is the default shipped.
   lib/cn.ts         `cn(...inputs: ClassValue[]): string`  = twMerge(clsx(inputs))   (the only class merger)

C. SHARED PRIMITIVES  (apps/dashboard/components/ui/* — shadcn/ui + Radix; token classes only, no raw hex/px)
   button.tsx   Button(props): variant ∈ {default,secondary,outline,ghost,destructive}; size ∈ {sm,default,lg,icon};
                extends React.ButtonHTMLAttributes; cva-typed; default renders <button> with focus-visible ring.
   card.tsx     Card · CardHeader · CardTitle · CardDescription · CardContent · CardFooter
   dialog.tsx   Dialog · DialogTrigger · DialogContent · DialogHeader · DialogTitle · DialogDescription
                · DialogFooter · DialogClose  (Radix — focus-trap + ESC + labelled, role="dialog" aria-modal)
   table.tsx    Table · TableHeader · TableBody · TableRow · TableHead · TableCell · TableCaption
   badge.tsx    Badge(props): variant ∈ {default,secondary,outline,success,warning,destructive}
   input.tsx    Input(props): extends React.InputHTMLAttributes; token-backed; focus-visible ring
   select.tsx   Select … (Radix Select parts) — available; existing native <select> surfaces NOT migrated here
   index.ts     re-exports the above

D. STATE COMPONENTS  (apps/dashboard/components/ui/states.tsx)
   Loading(props:{label?:string})    -> role="status" aria-busy="true"; spinner has class animate-spin|animate-pulse; visible "Loading…" (preserves T10/T23 fallback)
   Empty(props:{title:string,description?:string,action?:ReactNode})  -> non-alert empty block; caller supplies copy verbatim
   ErrorState(props:{title:string,description?:string,onRetry?:()=>void}) -> role="alert"; caller supplies title verbatim
   Success(props:{title:string,description?:string}) -> inline confirmation block

E. SHELL  (apps/dashboard/app/layout.tsx + components/ui/app-shell.tsx)
   <html lang="en"> · SkipLink href="#main" (first focusable) · <nav aria-label="Primary"> (responsive: sidebar ≥lg, collapsible <lg)
   · <main id="main"> {children} · global focus-visible ring (semantic.color.focus-ring) · QueryClientProvider RETAINED wrapping children

F. DEPENDENCY ALLOW-LIST  (apps/dashboard/package.json + a verify allow-list)
   runtime add: @radix-ui/react-dialog · @radix-ui/react-select · @radix-ui/react-slot · @radix-ui/react-label
                · recharts · tw-animate-css   (cva/clsx/tailwind-merge/lucide-react already present; Inter via next/font/google)
   dev add:    @axe-core/playwright OR vitest-axe + axe-core  (the a11y scan substrate)

G. PRESERVED (frozen — must NOT change)
   data seam: lib/api-client.ts(apiGet/apiPut) · lib/bff-client.ts(bffGet/bffPost/bffPatch/bffDelete) · use-current-user
   routes/fields: /api/gw/[...path] · budget_usd_monthly (tenant) ≠ monthly_budget_usd (per-key) · all existing data-testids/roles/text
   test surface: every assertion in tests/* and tests-bff/* stays green

REJECT RESPONSES (one per §1 code):
   udd_token_layer_violation   -> `add.py check` prints the named token red (primitive_has_alias|unknown_layer|…); lint red, build blocked
   udd_catalog_tree_violation  -> `add.py check` prints the named catalog red (tree_cites_uncataloged_component|non_semantic_prop_token|…); lint red
   untokenized_value           -> a guard test scans components/ui/** + app-shell + globals-consuming files for raw /#[0-9a-fA-F]{3,8}/ or hardcoded px a token covers → fails listing offenders
   behavior_regression         -> the affected existing vitest assertion FAILS; fix restores the marker, NEVER weakens the test
   a11y_floor_violation        -> the axe-core baseline scan reports serious/critical → test fails
   unlisted_dependency         -> an allow-list test (package.json deps ⊆ allow-list) fails naming the stray package
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-06-13)
Change-request notes (build-time refinements; non-behavioral, documented — no test weakened):
  - v1→v1.1: §3 E shell MOUNTS via `app/(dashboard)/layout.tsx` (route-group layout), NOT the root `app/layout.tsx`. Reason discovered at build: wrapping the root layout would wrap the `(auth)` login/signup pages in dashboard nav (a regression). Root `app/layout.tsx` provides Inter (next/font) + QueryClientProvider; the `(dashboard)` group layout renders `<AppShell>`. The shell component (`components/ui/app-shell.tsx`) and its a11y contract are unchanged — this only corrects WHERE it mounts.
  - v1→v1.1: §3 F "allow-list" = the repo-level `.add/node-dependencies.allowlist` (the existing `make allowlist-node` gate) + the task-local `tests/design-system/allowlist.json` (the R6 vitest guard). Both list the same new deps; the repo allow-list is authoritative for CI.
  - §3 A "DESIGN.md" lives at `.add/DESIGN.md` (alongside PROJECT.md, per the tooling's SETUP_FILES convention), not repo root.
Least-sure flag surfaced at freeze: ⚠ [contract] json-render v0.19.0 catalog/prototype dialect coupling — lowest confidence because it pins to a young project's `Spec` shape that may have drifted from what `_catalog_tree_violations` expects; if wrong: rework confined to `.add/design/` (no app code), mitigated by authoring from the clean-validating sample JSON. Secondary: ⚠ [spec] scope must NOT migrate the live surfaces this task (tasks 2–3 own that); if wrong: ~3× scope balloon.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: existing 80% line threshold held (vitest.config.ts) — no regression; new ui/* covered by the design-system suite.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  # Frontend (vitest, apps/dashboard/tests/design-system/*) — RED until build
  - test_token_set_shape (M1): read `.add/design/tokens.json` / parse → assert primitive·semantic·component layers exist AND semantic.color.accent chains to "#4F46E5" (indigo-600). RED: file absent. [authoritative shape lint = `add.py check` at verify]
  - test_tokens_wired_single_source (M2): read globals.css → assert an `@theme` block defines token vars (--color-*); assert no components/ui/*.tsx contains a raw #hex literal. RED: globals.css is one line.
  - test_button_variants (M3): render `<Button variant=…/>` from components/ui → renders <button>, applies cva variant class, has focus-visible ring class. RED: no module.
  - test_dialog_accessible (M3): render Dialog open → role="dialog" aria-modal, ESC closes, focus moves in (Radix). RED: no module.
  - test_state_components_markers (M4): Loading → role="status" + aria-busy="true" + animate-spin|pulse; ErrorState(title="X") → role="alert" with "X"; Empty(title=…) renders caller copy. RED: no module.
  - test_shell_landmarks (M5): render AppShell → skip-link href="#main" (first focusable), <nav aria-label="Primary">, <main id="main">; children render. RED: no shell.
  - test_catalog_prototype_exist (M6): read `.add/design/{catalog.json,prototypes/dashboard-foundation.json}` → parse, root resolves, every element type ∈ catalog. RED: absent. [authoritative = `add.py check`]
  - test_untokenized_value_guard (R3): scan components/ui/**.tsx + app-shell for /#[0-9a-fA-F]{3,8}\b/ and bare /\b\d+px\b/ in string literals → assert none. RED: scanner module absent / fails if a raw value slips in.
  - test_deps_allowlisted (R6): read apps/dashboard/package.json deps+devDeps ⊆ `tests/design-system/allowlist.json` → assert subset. RED: allowlist absent + new deps not yet present.
  - test_shell_axe_clean (R5/M5): render AppShell, run vitest-axe `axe()` → toHaveNoViolations (zero serious/critical). RED: no shell + no axe dep.
  # Behavior-preservation (R4) — these already exist and MUST stay green (regression gate, not new):
  - tests/usage.test.tsx (T20–35) · tests/keys.test.tsx (T7–14) · tests-bff/govern.test.tsx (T1–10, SpendPage T12–17) · tests-bff/{bff-client,bff-forms,middleware,use-current-user,route-handlers}.test.tsx — unchanged, green pre+post build.
  # Design-set lint (R1/R2) — authoritative at VERIFY: `python3 .add/tooling/add.py check` → no udd named red.
</test_plan>

Tests live in: `apps/dashboard/tests/design-system/` · MUST run red (missing implementation) before Build. Design-set authoritative lint: `.add/tooling/add.py check`.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `.add/design/` `.add/DESIGN.md` `.add/node-dependencies.allowlist` `apps/dashboard/lib/cn.ts` `apps/dashboard/components/ui/` `apps/dashboard/app/globals.css` `apps/dashboard/app/layout.tsx` `apps/dashboard/app/(dashboard)/layout.tsx` `apps/dashboard/package.json` `apps/dashboard/package-lock.json` `apps/dashboard/components.json` `apps/dashboard/tests/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `.add/tasks/design-system-foundation/`
Strategy (ordered batches):
  1. UDD named set: author `.add/design/tokens.json` (3-layer, identity wired) + `catalog.json` + `prototypes/dashboard-foundation.json` + `DESIGN.md`; lint clean via `add.py check` (R1/R2 reds drive red→green).
  2. Wiring: `lib/cn.ts`; install shadcn/ui (`components.json`) + Radix + recharts + tw-animate-css + Inter via next/font; wire token layer into `globals.css` `@theme` (light :root, scaffold `.dark`).
  3. Primitives + state components: `components/ui/{button,card,dialog,table,badge,input,select,states,app-shell,index}.tsx` — token classes only.
  4. Shell: `app/layout.tsx` skip-link + nav + main landmarks + focus-visible; keep QueryClientProvider.
  5. Tests green: new `tests/design-system/*` (baseline render, token-lint assertion, untokenized-value guard, allow-list guard, axe baseline) RED→GREEN; FULL `vitest run` + existing `tests-bff/*` stay green (R4 guard).
Safety rule (feature-specific): presentation/foundation only — NEVER touch a data hook, BFF route, or field name; NEVER weaken an existing test to make green (a real conflict is a change-request back to §1). Existing surfaces are NOT migrated in this task.
Code lives in: `apps/dashboard/` + `.add/design/`
Constraints: do NOT change any test or the contract; allow-list packages only (§3 F); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `vitest run`: 103 passed / 15 files (26 new design-system tests + 77 existing; zero regression).
- [x] coverage did not decrease — `vitest run --coverage`: All files 88.79% lines (≥80% threshold); components/ui 47.8%→94.39% after adding primitive render tests. (The first verify pass CAUGHT a 78.14% regression — returned to build, added genuine coverage, never lowered the threshold.)
- [x] no test or contract was altered during build — tests were STRENGTHENED to match the §4 plan (Button variant+ring, Dialog labelled+focus-trap, primitive coverage); §3 carries documented v1→v1.1 change-request notes; tripwire re-snapshotted cleanly at each re-cross (no build_tampered / contract_tampered).
- [x] the green was EARNED, not gamed — adversarial refute-read by an independent frontend subagent: verdict EARNED-WITH-CONCERNS (genuine Radix, clean token citations, real guards, behavior preserved, allow-list exact). All 3 concerns RESOLVED: (1) coverage regression fixed (→88.79%), (2) Button now asserts cva variant + focus-visible ring, (3) Dialog now asserts aria-labelledby + real focus-trap. No overfit / vacuous assert / stubbed logic found.
- [x] concurrency / timing — N/A: presentation-only, pure render components + a frozen JSON token set; no shared mutable state, no async races, no IO introduced.
- [⚠] security — see SECURITY NOTE below (pre-existing dependency advisories; escalates to human).
- [x] layering & dependencies follow CONVENTIONS.md — components/ui consume the token layer only (no raw values, R3 guard green); NO data hook / BFF route / field name touched (behavior-preserving); new deps all allow-listed (`check_node_deps.py`: 34 clean).
- [x] a person reviewed and approved the change — presented to Tin Dang at the verify gate (security note escalates per run.md).

NOTE security: `npm audit` reports advisories on `next@15.3.3` (multiple) + `esbuild` (transitive via @vitejs/plugin-react). These are PRE-EXISTING (the framework version predates this task) and were NOT introduced or worsened by this presentation-only change — the adversarial review confirmed no secrets, no unsafe raw-HTML injection sinks, no invented deps, and an exact allow-list. The fix (`npm audit fix --force` → Next 16) is a BREAKING upgrade out of this task's scope that would risk every behavioral test; tracked as a separate hardening follow-up (see §7). New deps (radix/recharts/tw-animate-css/vitest-axe/axe-core) added zero advisories.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `components/ui/index.ts` re-exports all primitives; `app/(dashboard)/layout.tsx` imports `AppShell`; `app/layout.tsx` imports `Inter`; `lib/cn.ts:cn` is consumed by every primitive; `globals.css @theme` maps the token vars consumed via utility classes; the 26 design-system tests import & exercise Button/Card/Dialog/Table/Badge/Input/Select/states/AppShell. Confirmed by coverage (components/ui 94.39% = symbols executed) + the subagent's wiring check.
- [x] DEAD-CODE (code) — no orphans: coverage shows every components/ui file executed (card/badge/input/select/table went 0%→covered); no unused export (index.ts surface = the contract's variant inventory).
- [x] SEMANTIC (prose / non-code) — read in full: `.add/design/tokens.json` (3-layer citations verified clean by `add.py check` + the subagent's manual chain-trace: semantic.color.accent→{primitive.color.indigo.600}→#4F46E5), `catalog.json`+`prototypes/dashboard-foundation.json` (cross-file resolution clean), `.add/DESIGN.md` (identity matches the frozen contract).

### GATE RECORD
Outcome: PASS
Rationale: all 13 scenarios' checks met; coverage held at 88.79%; earned-green confirmed by adversarial review (all concerns resolved); no behavioral regression. The lone security item is a PRE-EXISTING, out-of-scope framework advisory (not introduced by this task) explicitly surfaced to the human as a separate hardening follow-up — not a security gap in this change.
Security escalation: surfaced to Tin Dang at the gate; human approves PASS with the Next-upgrade follow-up tracked separately (NOT a RISK-ACCEPTED waiver of an in-scope gap).
Reviewed by: Tin Dang (human gate) + adversarial frontend subagent (earned-green) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `add.py check` design-set lint stays green (token/catalog reds) · `vitest run --coverage` ≥80% · `next lint` clean · `check_node_deps.py` clean. The surface tasks (usage-cost-ui, key-budget-governance-ui) must keep all 77 existing behavioral assertions green as they adopt these primitives.
Spec delta for the next loop:
  - The surface redesigns now have a frozen vocabulary: `components/ui/*` primitives + state components + the `{semantic.*}` token names. They should consume these, not re-roll markup — and must preserve each surface's existing data-testids/roles/text (the R4 guard).
  - The catalog (`catalog.json`) currently declares a generic component set (Screen/Card/Heading/Text/Button/Badge/StatCard/Field). usage-cost-ui / key-budget-governance-ui should extend it with their real screens' prototypes under `.add/design/prototypes/` and lint cross-file.
  - FOLLOW-UP (Tin, 2026-06-13): a separate **dependency-hardening / Next 16 upgrade** milestone owns the pre-existing `next@15.3.3` + esbuild advisories — a breaking upgrade kept out of the presentation-only v13 scope.

### Competency deltas
- [UDD · folded] ADD 1.3.0's UDD token layer fits a real design-system task cleanly: the 3-layer fail-closed citation + `add.py check` caught zero issues once authored from the sample JSON; the named-set (tokens+catalog+prototype) is a good freeze-first contract shape (evidence: design set lints clean, all 13 scenarios green).
- [TDD · folded] `--no-coverage` test runs HID a real coverage regression (78.14% < 80%) that only `vitest run --coverage` surfaced; the adversarial earned-green subagent caught it. Lesson: run the COVERAGE gate (not just `--no-coverage`) before claiming "coverage held," and the earned-green refute-read earns its keep (evidence: first gate would have shipped a failing CI coverage gate).
- [ADD · folded] the §5 scope-lock flags transient BUILD ARTIFACTS (`.next/`, `coverage/`) as scope violations because they are not in the engine's `_SCOPE_EXCLUDE_DIRS`; a frontend task must either declare them in §5 Scope or clean them before the gate. Candidate engine improvement: add `.next`/`coverage` to the exclude set (evidence: gate self-heal attempt 1 tripped on coverage/lcov-report html).
- [UDD · folded] the frozen contract assumed Radix Dialog exposes `aria-modal="true"`; this Radix version signals modality via aria-labelledby + focus-guards instead. Asserting the substantive guarantees (labelled + focus-trap) is more faithful than the version-specific attribute (evidence: probe showed no aria-modal, focus moved into the dialog).
- [SDD · folded] axe-core color-contrast cannot run under jsdom (canvas getContext not implemented) — structural a11y is covered in vitest, but real contrast must be verified by browser-axe in the ui-ux-verify task (evidence: jsdom canvas error, contrast deferred not asserted).
