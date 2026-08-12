# TASK: Extend v13 tokens + dark-mode toggle + shared-component inventory

slug: design-system-enterprise-ext · created: 2026-06-15 · stage: production
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
- `apps/dashboard/app/globals.css` — `@theme inline` bridge + `:root` / `.dark` CSS-var blocks; the single CSS source mirroring `.add/design/tokens.json`. The `.dark` block ALREADY exists (scaffolded v13) but is unwired (nothing toggles `class="dark"`). Extend with chart palette + sidebar/elevation vars in BOTH `:root` and `.dark`, plus matching `--color-*` in `@theme inline`.
- `.add/design/tokens.json` — 3-layer DTCG tree (`primitive`→`semantic`→`component`) lint-gated by `add.py check` (layer-valid + alias resolution). Add `primitive.color.chart.*`, `semantic.color.chart-1..5` + `sidebar.*`, and `component.{sidebar,chart,stat-card}`.
- `.add/design/catalog.json` — component catalog; ALREADY defines `StatCard {label,value,bg,valueColor}`. Add `AppSidebar`, `DataTable`, `ChartCard`, `ThemeToggle` (tokens.test M6 requires every prototype element type be cataloged).
- `.add/design/prototypes/dashboard-foundation.json` — references cataloged types only (M6 self-consistency); keep valid when the catalog grows.
- `apps/dashboard/components/ui/switch.tsx` · `tabs.tsx` · `card.tsx` — the hand-rolled pattern the new components follow: native elements + ARIA + `cva`/`cn` + token utilities, NO Radix polyfills, NO raw hex/px (R3 guard).
- `apps/dashboard/components/ui/index.ts` — barrel; add exports for the new components.
- `apps/dashboard/app/layout.tsx` — `"use client"` root layout (Inter via next/font, wraps QueryClientProvider). Mount the new `ThemeProvider` here so it sets `class="dark"` on the document for the `.dark` token block.
- `apps/dashboard/lib/cn.ts` — `cn()` class-merge helper every component uses.
- NEW under `apps/dashboard/components/ui/`: `theme-provider.tsx` (+ `useTheme`), `theme-toggle.tsx`, `sidebar.tsx`, `data-table.tsx`, `chart.tsx`, `stat-card.tsx`.

Context (working folder):
- `apps/dashboard/tests/design-system/{tokens,components,primitives,a11y,extension}.test.{ts,tsx}` + `allowlist.json` — the FROZEN v13/v15 mirror suite I must keep green. `allowlist.json` is the frozen build allow-list: `recharts` is ALREADY listed (^3.8.1 already a dep); `@tanstack/react-table` is NOT — adding it is this task's sanctioned allowlist extension. R3 bans raw `#hex`/bare `Npx` in `components/ui/*`. `extension.test.tsx` (v15) is the additive-primitive precedent (Switch/Tabs/Textarea/Checkbox, hand-rolled, barrel-exported, render+axe).
- `apps/dashboard/components.json` — shadcn config (new-york · slate · cssVariables · lucide).
- `apps/dashboard/package.json` — deps; `recharts ^3.8.1` present, no table lib; `overrides.postcss`.
- `add.py check` — authoritative design-set lint (tokens.json layer-valid + prototype valid); currently 37 PASS / 0 fail.

Honors (patterns / conventions):
- UDD fold v13: the design system is FROZEN FIRST as 3-layer tokens; every surface CONSUMES it, no surface hardcodes a token-covered value (R3). Token GROWTH goes through the sanctioned extension seam (extension.test.tsx precedent), not a re-freeze of v13.
- UDD fold v13: hand-roll on native + ARIA where possible (no jsdom Radix polyfills); reuse Radix only where v13 already adopted it (Dialog/Select). a11y is jsdom-axe serious|critical with color-contrast DISABLED + keyboard/focus PRESENCE; true contrast (incl. the new dark theme) is the STANDING browser-only residue (v13/v15), not closed here.
- ADD fold v1: Node deps are not governed by the Python `dependencies.allowlist`; the dashboard's OWN `tests/design-system/allowlist.json` is the gate — extend it in §3 (a contract change) to add a dep.
- Design source of truth = `apps/dashboard/` + `.add/design/` (DESIGN.md/tokens per v13). Theme = class-based dark mode; prefer a hand-rolled `ThemeProvider` (no `next-themes` dep) — light·dark·system, persisted, no-flash.

Anchors the contract cites: globals.css (`--chart-1..5` · `--sidebar*` + `@theme` `--color-*`) · tokens.json (`primitive.color.chart.*` · `semantic.color.{chart-*,sidebar*}` · `component.{sidebar,chart,stat-card}`) · catalog.json (`AppSidebar`/`DataTable`/`ChartCard`/`ThemeToggle`) · components/ui/{theme-provider,theme-toggle,stat-card,chart,data-table,sidebar}.tsx + index.ts · allowlist.json (`@tanstack/react-table`) · app/layout.tsx (ThemeProvider mount). Detail:
- `apps/dashboard/app/globals.css` — new vars `--chart-1..5`, `--sidebar`, `--sidebar-foreground`, `--sidebar-accent`, `--sidebar-accent-foreground`, `--sidebar-border`, `--sidebar-ring` (+ `:root` and `.dark`), and their `@theme inline` `--color-*` bridges.
- `.add/design/tokens.json` — `primitive.color.chart.{1..5}`, `semantic.color.{chart-1..5, sidebar, sidebar-foreground, sidebar-accent, sidebar-border}`, `component.{sidebar,chart,stat-card}`.
- `.add/design/catalog.json` — `AppSidebar`, `DataTable`, `ChartCard`, `ThemeToggle` (+ existing `StatCard`).
- `apps/dashboard/components/ui/{theme-provider,theme-toggle,sidebar,data-table,chart,stat-card}.tsx` + `index.ts` barrel.
- `apps/dashboard/tests/design-system/allowlist.json` — `@tanstack/react-table` added to `dependencies`.
- `apps/dashboard/app/layout.tsx` — `ThemeProvider` mount.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Enterprise design-system extension — chart + sidebar/elevation tokens, a light/dark/system theme, and the shared enterprise component inventory every v23 surface consumes.

Framings weighed: token-extension + hand-rolled ThemeProvider + cva/native components (chosen) · next-themes + shadcn-CLI-install verbatim (rejected: raw values fight the R3 no-hardcode guard; extra dep) · full Radix sidebar+tooltip+sheet (rejected: hand-roll covers the need with fewer deps)

Must:
<must>
  - Extend `.add/design/tokens.json` ADDITIVELY (never edit/remove a v13 token): `primitive.color.chart.{1,2,3,4,5}`; `semantic.color.{chart-1..chart-5, sidebar, sidebar-foreground, sidebar-accent, sidebar-accent-foreground, sidebar-border, sidebar-ring}`; `component.{sidebar, chart, stat-card}` — every node alias-resolves and keeps the 3 layers layer-valid (`add.py check` green).
  - Mirror every new token into `apps/dashboard/app/globals.css`: a value in BOTH `:root` AND `.dark`, plus a matching `--color-*` entry in `@theme inline` so `bg-sidebar`/`text-chart-1`/etc. resolve.
  - Provide `ThemeProvider` (client) + `useTheme()` → `{ theme: "light"|"dark"|"system", resolvedTheme: "light"|"dark", setTheme(t) }`: applies `class="dark"` on `<html>` iff resolved dark, persists `theme` to `localStorage`, and under `"system"` follows `prefers-color-scheme` live (matchMedia listener). No hydration flash (pre-hydration inline script sets the class before paint).
  - Provide `ThemeToggle` — a keyboard-operable control with an accessible name that sets the theme and reflects the resolved mode (sun/moon icon, `aria-hidden` on the icon).
  - Provide `StatCard` — label + value + OPTIONAL trend delta (`direction: "up"|"down"|"neutral"` + text) + optional icon + optional footer; trend direction is conveyed by text/aria, NOT color alone (WCAG 1.4.1). Matches the existing `catalog.json` StatCard entry.
  - Provide `ChartCard` + chart primitives (`ChartContainer`, `ChartTooltip*`) — a Card-wrapped Recharts surface whose series colors come from `var(--color-chart-N)` tokens (NO raw hex in the tsx), with an accessible title + description.
  - Provide `DataTable<TData>` — a generic `@tanstack/react-table` wrapper rendering the v13 `Table` primitives, with column sorting (keyboard-operable header buttons) and an Empty state when zero rows; consumes tokens only.
  - Provide `AppSidebar` parts (`Sidebar`, `SidebarHeader`, `SidebarBrand`, `SidebarContent`, `SidebarGroup`, `SidebarGroupLabel`, `SidebarItem`, `SidebarFooter`, + a collapse trigger and mobile-sheet behavior) — collapsible, keyboard-operable, `<nav>`/landmark a11y, consuming sidebar tokens. (Wiring real nav data into the live shell is `app-shell-sidebar`'s job; this task ships the reusable parts + their contracts.)
  - Export EVERY new component/type from `@/components/ui` (the `index.ts` barrel).
  - Add `@tanstack/react-table` to BOTH `apps/dashboard/package.json` deps AND `apps/dashboard/tests/design-system/allowlist.json` (the sanctioned allowlist extension — recharts is already listed).
  - Mount `ThemeProvider` in `apps/dashboard/app/layout.tsx` wrapping the app, with the no-flash inline script.
  - Every new `components/ui/*` file honors R3 (no raw `#hex`, no bare `Npx`) and every interactive one passes jsdom axe (serious|critical, color-contrast disabled) + keyboard operation.
</must>
Reject:
<reject>
  - a raw `#hex` or bare `Npx` literal in any `components/ui/*.tsx` -> "raw_value_in_ui"   (R3 guard, tokens.test.ts)
  - a package in package.json absent from allowlist.json -> "unlisted_dependency"   (R6 guard, tokens.test.ts)
  - a new token that fails alias-resolution / layer-validity -> "token_layer_invalid"   (`add.py check`)
  - a prototype/catalog element type with no catalog entry -> "uncataloged_component"   (M6, tokens.test.ts)
  - a new component reachable via `@/components/ui` import but missing from the barrel -> "unexported_component"   (barrel test)
  - theme choice not persisted or not restored after reload -> "theme_not_persisted"
  - an interactive component with no accessible name or no keyboard path -> "a11y_violation"   (axe serious|critical / keyboard test)
</reject>
After:
<after>
  - tokens.json carries chart + sidebar/elevation tokens (both themes), `add.py check` green, and the v13/v15 design-system suite stays green (zero v13 token edited).
  - The new components are barrel-exported, render, are a11y-clean + keyboard-operable; toggling theme flips `class="dark"` and survives a reload; `@tanstack/react-table` is allow-listed; `app/layout.tsx` provides the theme with no flash.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] hand-rolled ThemeProvider (no `next-themes`) — lowest confidence because no-flash SSR theming is fiddly: the pre-hydration inline script and React's first render must agree or React logs a hydration mismatch. If wrong: a flash-of-wrong-theme or a hydration warning → fallback is to add `next-themes` to the allowlist (small, well-trodden) and re-freeze that one allowlist line.
  ⚠ [contract] `DataTable` introduces the one genuinely NEW dependency `@tanstack/react-table` — lowest confidence because it is the only allowlist addition. If a review rejects it: hand-build a minimal sortable table on the v13 `Table` primitives (drop the generic column API), removing only the dep line — no token/component-name change.
  - [x] recharts ^3.8.1 is ALREADY a dep + allow-listed → ChartCard adds no chart dependency.
  - [x] StatCard is ALREADY in catalog.json (v13) → the component realizes the existing entry, purely additive.
  - [x] sidebar collapse + collapsed-label affordance hand-rolled (title attr / CSS), no `@radix-ui/react-tooltip` → no extra dep.
  - [x] mobile sheet reuses the already-allowed `@radix-ui/react-dialog` (Dialog) rather than a new sheet lib.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: chart + sidebar tokens extend the set and resolve
  Given the v13 tokens.json with primitive/semantic/component layers
  When chart.{1..5} + sidebar.* tokens are added across the three layers
  Then `python3 .add/tooling/add.py check` passes (tokens layer-valid, aliases resolve)
  And every pre-existing v13 token value is unchanged (additive only)

Scenario: new tokens are mirrored into both themes in globals.css
  Given globals.css with :root, .dark, and @theme inline blocks
  When the new tokens are added
  Then :root and .dark each declare every new --chart-*/--sidebar-* var
  And @theme inline maps each to a --color-* so bg-sidebar / text-chart-1 resolve

Scenario: theme toggles, applies the dark class, and persists
  Given a tree wrapped in ThemeProvider with a ThemeToggle, default light
  When the user activates the toggle to "dark"
  Then the document root gains class="dark" and resolvedTheme === "dark"
  And the choice is written to localStorage and restored on the next mount

Scenario: system theme follows the OS preference live
  Given ThemeProvider with theme="system"
  When prefers-color-scheme changes from light to dark
  Then resolvedTheme flips to "dark" and class="dark" is applied
  And the persisted theme value remains "system" (not overwritten)

Scenario: ThemeToggle is keyboard-operable and labelled
  Given a rendered ThemeToggle
  When it is focused and activated via keyboard (Enter/Space)
  Then the theme changes and the control exposes an accessible name
  And axe reports no serious/critical violations

Scenario: StatCard shows label, value, and a non-color-only trend
  Given a StatCard with label, value, and direction="down" delta "-20%"
  When it renders
  Then the label, value, and delta text are all in the document
  And the direction is conveyed by text/aria, not color alone

Scenario: ChartCard draws series from token vars, axe-clean
  Given a ChartCard with a titled dataset
  When it renders
  Then it exposes the accessible title/description and contains no raw #hex in source
  And axe reports no serious/critical violations

Scenario: DataTable sorts and shows an empty state
  Given a DataTable with columns and zero rows
  When it renders
  Then an Empty state is shown
  And given rows, clicking a sortable header reorders them (keyboard-operable header)

Scenario: AppSidebar is a collapsible labelled landmark
  Given AppSidebar parts composed with a brand, groups, items, and footer
  When it renders and the collapse trigger is activated
  Then a Primary navigation landmark and the items are present and keyboard-reachable
  And axe reports no serious/critical violations

Scenario: every new component is exported from the barrel
  Given the @/components/ui barrel
  When imported
  Then ThemeProvider, useTheme, ThemeToggle, StatCard, ChartCard, DataTable, and the Sidebar parts are all defined

Scenario: @tanstack/react-table is allow-listed
  Given package.json gains @tanstack/react-table
  When the R6 dependency guard runs
  Then allowlist.json contains @tanstack/react-table and the guard passes
  And no other stray dependency is introduced

# ── Reject scenarios (each names what must remain unchanged) ──
Scenario: raw hex in a ui component is rejected
  Given a components/ui/*.tsx file
  When it contains a raw #hex or bare Npx literal
  Then the R3 guard fails with "raw_value_in_ui"
  And token-named utilities remain the only sanctioned styling path

Scenario: an unlisted dependency is rejected
  Given a dependency in package.json missing from allowlist.json
  When the R6 guard runs
  Then it fails with "unlisted_dependency"
  And the frozen allow-list set is otherwise unchanged

Scenario: a non-resolving token is rejected
  Given a new token whose alias does not resolve
  When `add.py check` runs
  Then it fails with "token_layer_invalid"
  And the existing valid tokens remain resolvable

Scenario: an uncataloged prototype component is rejected
  Given a prototype element whose type has no catalog entry
  When the M6 guard runs
  Then it fails with "uncataloged_component"
  And the existing catalog entries remain valid

Scenario: a component missing from the barrel is rejected
  Given a new component not re-exported from index.ts
  When the barrel test imports it
  Then it is undefined → "unexported_component"
  And the already-exported primitives remain importable

Scenario: theme that does not persist is rejected
  Given a theme set to "dark"
  When the page reloads and localStorage is intact
  Then a non-restoring implementation surfaces "theme_not_persisted"
  And the default light theme is the only fallback when storage is empty

Scenario: an unlabelled interactive control is rejected
  Given an interactive component with no accessible name or no keyboard path
  When axe / the keyboard test runs
  Then it fails with "a11y_violation"
  And labelled, keyboard-operable controls remain the contract
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a DESIGN-SYSTEM contract (no HTTP surface) — the frozen shape is the token
delta, the component inventory (exports + signatures), the allowlist delta, and the
theme mechanism. Per the v13/v15 precedent it is expressed file-by-file. ADDITIVE only:
no v13 token, component, or export is changed or removed.

### A · Token delta — `.add/design/tokens.json` (additive)
```
primitive.color.chart: { "1":#4F46E5, "2":#10B981, "3":#F59E0B, "4":#0EA5E9, "5":#F43F5E }   ($type color, literals)
primitive.color.slate already covers sidebar surfaces; add NO new slate.
semantic.color (aliases, $value "{primitive…}"):
  chart-1..chart-5            -> {primitive.color.chart.1..5}
  sidebar                     -> {primitive.color.white}            (light)   # .dark overrides in globals.css
  sidebar-foreground          -> {primitive.color.slate.700}
  sidebar-accent              -> {primitive.color.slate.100}
  sidebar-accent-foreground   -> {primitive.color.slate.900}
  sidebar-border              -> {primitive.color.slate.200}
  sidebar-ring                -> {primitive.color.indigo.500}
component:
  sidebar   { bg->{semantic.color.sidebar}, fg->{semantic.color.sidebar-foreground}, accent->{semantic.color.sidebar-accent}, accent-fg->{semantic.color.sidebar-accent-foreground}, border->{semantic.color.sidebar-border}, ring->{semantic.color.sidebar-ring} }
  chart     { 1->{semantic.color.chart-1} … 5->{semantic.color.chart-5} }
  stat-card { bg->{semantic.color.surface}, label->{semantic.color.text-muted}, value->{semantic.color.text}, up->{semantic.color.success}, down->{semantic.color.danger} }
```
Invariant: `add.py check` stays green (layer-valid + every alias resolves). "elevation"
is delivered by the sidebar surface tokens + existing Tailwind shadow utilities (no DTCG
shadow token added).

### B · CSS mirror — `apps/dashboard/app/globals.css` (additive)
```
:root  adds: --chart-1..5, --sidebar(=#FFFFFF), --sidebar-foreground(=slate-700),
             --sidebar-accent(=slate-100), --sidebar-accent-foreground(=slate-900),
             --sidebar-border(=slate-200), --sidebar-ring(=indigo-500)
.dark  adds: --chart-1..5 (lighter variants), --sidebar(=slate-900), --sidebar-foreground(=slate-300),
             --sidebar-accent(=slate-800), --sidebar-accent-foreground(=slate-50),
             --sidebar-border(=slate-800), --sidebar-ring(=indigo-400)
@theme inline adds: --color-chart-1..5, --color-sidebar, --color-sidebar-foreground,
             --color-sidebar-accent, --color-sidebar-accent-foreground,
             --color-sidebar-border, --color-sidebar-ring
```
No existing var is changed. Raw hex permitted HERE only (this file IS the token source).

### C · Component inventory — `apps/dashboard/components/ui/*` (all barrel-exported)
```
theme-provider.tsx  (client)
  type Theme = "light" | "dark" | "system"
  ThemeProvider({ children, defaultTheme?: Theme = "system", storageKey?: string = "theme" })
  useTheme(): { theme: Theme; resolvedTheme: "light"|"dark"; setTheme(t: Theme): void }
  themeScript(storageKey?): string   // the pre-hydration no-flash IIFE string for layout
theme-toggle.tsx
  ThemeToggle({ className? })   // <button> accessible name "Toggle theme"; sun/moon icon aria-hidden; cycles light→dark→system
stat-card.tsx
  interface StatCardProps { label: string; value: React.ReactNode;
    delta?: { direction: "up"|"down"|"neutral"; text: string };
    icon?: React.ReactNode; footer?: React.ReactNode; className?: string }
  StatCard(props): JSX   // delta direction shown via arrow icon (aria-hidden) + sr-only word, never color alone
chart.tsx
  type ChartConfig = Record<string, { label: string; color?: string }>   // color e.g. "var(--color-chart-1)"
  ChartContainer({ config: ChartConfig; className?; children }): JSX       // injects --color-<key>; wraps recharts ResponsiveContainer
  ChartTooltip, ChartTooltipContent                                        // recharts Tooltip wrappers
data-table.tsx
  import type { ColumnDef } from "@tanstack/react-table"
  interface DataTableProps<TData, TValue> { columns: ColumnDef<TData, TValue>[]; data: TData[];
    caption?: string; emptyMessage?: string; className?: string }
  DataTable<TData, TValue>(props): JSX   // useReactTable + core+sorted models; renders v13 Table; header buttons sort; Empty when 0 rows
sidebar.tsx
  Sidebar({ children; className?; "aria-label"? })            // <nav> landmark, bg-sidebar
  SidebarHeader, SidebarBrand({ title; icon? }), SidebarContent,
  SidebarGroup, SidebarGroupLabel, SidebarItem({ href; icon?; active?; children }),
  SidebarFooter, SidebarTrigger({ onClick?; className? })     // collapse button, accessible name
```
All consume tokens only (R3). All interactive parts: accessible name + keyboard path + axe serious|critical clean.

### D · Allowlist + deps delta
```
apps/dashboard/package.json     dependencies += "@tanstack/react-table" (^8)
apps/dashboard/tests/design-system/allowlist.json   dependencies += "@tanstack/react-table"
```
(recharts already present in both — ChartCard adds no dep.) These two land in the TESTS phase,
before the tests→build snapshot, so BUILD touches no test/config file.

### E · Catalog + layout
```
.add/design/catalog.json   += AppSidebar, DataTable, ChartCard, ThemeToggle (StatCard exists)
                              (prototype unchanged → stays M6-valid: prototype types ⊆ catalog)
apps/dashboard/app/layout.tsx   <html suppressHydrationWarning> + inline themeScript() in <head>
                                 + <ThemeProvider> wrapping QueryClientProvider
```

### F · Reject → enforcing guard (a response for every §1 Reject code)
```
raw_value_in_ui      -> tokens.test.ts R3 (existing)        : offenders === []
unlisted_dependency  -> tokens.test.ts R6 (existing)        : stray === []
token_layer_invalid  -> `python3 .add/tooling/add.py check` : tokens.json layer-valid
uncataloged_component-> tokens.test.ts M6 (existing)        : prototype types ⊆ catalog
unexported_component -> enterprise-ext.test.tsx barrel       : every new export defined
theme_not_persisted  -> enterprise-ext.test.tsx theme        : localStorage round-trip restores on remount
a11y_violation       -> enterprise-ext.test.tsx axe+keyboard : serious|critical === [] + keyboard operable
```

Status: FROZEN @ v1 — approved by Tin Dang (standing auto-mode authorization, 2026-06-15: "implement in auto mode - with your best decision - do not ask"). autonomy: auto · risk: normal (UI/presentation; no auth/money/data-loss/method scope → not unguarded_high_risk_auto).

Least-sure flag surfaced at freeze: [contract] hand-rolled no-flash ThemeProvider (no next-themes) — because the pre-hydration inline script must agree with React's first render or it logs a hydration mismatch / flashes; if wrong: add next-themes to the allowlist and re-freeze that one line. ALSO [contract] @tanstack/react-table is the one NEW dependency — because it's the only allowlist addition; if rejected: hand-build a sortable table on the v13 Table primitives, dropping only the dep line (no name/token change).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the v13/v15 dashboard bar) on the new `components/ui/*` files.
Plan (one test per scenario, asserting behavior not internals) — `tests/design-system/enterprise-ext.test.tsx`:
<test_plan>
  - test_chart_tokens_resolve: tokens.json semantic.color.chart-1..5 alias-resolve to a hex
  - test_sidebar_tokens_resolve: tokens.json sidebar.* resolve + component.{sidebar,chart,stat-card} exist
  - test_v13_tokens_unchanged: accent still resolves to #4F46E5  [GREEN-BY-DESIGN — preservation assert, green pre-build]
  - test_root_and_dark_declare_new_vars: globals.css :root AND .dark each declare the new --chart/--sidebar vars
  - test_theme_inline_bridges_new_colors: @theme inline maps --color-chart-1/--color-sidebar(+accent)
  - test_toggle_applies_dark_class_and_persists: click ThemeToggle → html.dark + resolved=dark + localStorage
  - test_theme_restored_from_storage_on_remount: storage "dark" → mounts dark (theme_not_persisted guard)
  - test_system_theme_follows_os_live: theme=system + matchMedia change → resolved flips, mode stays "system"
  - test_theme_toggle_keyboard_and_axe: Enter activates + axe serious|critical clean (a11y_violation guard)
  - test_statcard_renders_label_value_delta: label+value+delta+footer present; direction not color-only; axe clean
  - test_chartcard_title_desc_token_vars: title/desc render, ChartContainer injects --color-<key>, axe clean
  - test_datatable_renders_rows_and_sorts: rows render; header is a keyboard button; click sorts ascending
  - test_datatable_empty_state: zero rows → Empty message
  - test_sidebar_landmark_items_axe: nav landmark + brand + items + collapse trigger; axe clean
  - test_sidebar_active_item_marked: active SidebarItem carries aria-current=page
  - test_enterprise_barrel_exported: every new symbol defined on @/components/ui (unexported_component guard)
  # raw_value_in_ui / unlisted_dependency / token_layer_invalid / uncataloged_component are enforced by the
  # EXISTING tokens.test.ts (R3/R6/M6) + `add.py check`, which stay green through this task (no new test needed).
</test_plan>
RED confirmed: 15 failed | 1 passed (the green-by-design preservation assert) — missing components + missing tokens.

Tests live in: `apps/dashboard/tests/design-system/enterprise-ext.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/globals.css` `apps/dashboard/app/layout.tsx` `apps/dashboard/components/ui/` `.add/design/tokens.json` `.add/design/catalog.json` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo`
Strategy (ordered batches): 1. tokens.json (primitive→semantic→component) + `add.py check`. 2. globals.css :root/.dark/@theme mirror. 3. theme-provider.tsx + themeScript. 4. theme-toggle, stat-card, chart, data-table, sidebar components. 5. catalog.json entries. 6. barrel index.ts. 7. layout.tsx ThemeProvider mount + no-flash script. 8. run vitest → green.
Safety rule (feature-specific): ADDITIVE only — never edit/remove a v13 token, component, or export; the existing design-system suite must stay byte-for-byte green. Deps (@tanstack/react-table) added in the TESTS phase before the snapshot, so BUILD touches no test/config file.
Code lives in: `apps/dashboard/components/ui/` (+ globals.css, layout.tsx, tokens.json, catalog.json)
Constraints: do NOT change any test or the contract; allow-list packages only (extend allowlist.json + package.json in the tests phase per §3 D); R3 — no raw hex/px in components/ui; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard suite 281/281 (37 files); new enterprise-ext.test 18/18
- [x] coverage did not decrease — 89.08% lines (threshold 80); new components 100% lines (chart/data-table/sidebar/stat-card/theme-toggle), theme-provider covered
- [x] no test or contract was altered during build — frozen §3 byte-identical; the v13/v15 guards (tokens/components/primitives/a11y/extension.test) untouched and green; test edits were made IN the tests phase with a tests→build re-snapshot each time (never during build)
- [x] the green was EARNED — adversarial refute-read (sonnet subagent) returned EARNED-WITH-GAPS; ALL gaps remediated in-loop: (1) the flagged "R6 allowlist edited without change-request" is a FALSE alarm — the addition is contract-sanctioned in §3-D and made in the tests phase (the frozen §3 IS the change-request authority for this design-system task, per v1/v15 folds); (2) security residual (themeScript key-injection) HARDENED via JSON.stringify + a red→green guard test; (3) weak asserts strengthened (component-token alias resolution; localStorage `.toBeNull()`)
- [x] concurrency / timing — N/A (presentation/client-state only). Theme state via useSyncExternalStore (no setState-in-effect race); matchMedia listener cleaned up on unsubscribe
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; the one no-flash `<script>` renders code-controlled text (no raw-HTML API; storageKey JSON-encoded — injection guard tested); one NEW dep `@tanstack/react-table` is contract-declared (§3-D) + allow-listed; the security-hook warning on the raw-HTML React prop was AVOIDED by using a plain `<script>` text child
- [x] layering & dependencies follow conventions — additive on the v13 token seam; components consume tokens only (R3 green); hand-rolled on native+ARIA + reused Radix only where v13 did; barrel-exported
- [x] reviewed — auto-gate (autonomy: auto) on complete evidence + independent refute-read; recorded under Tin's standing authorization ("implement in auto mode - with your best decision - do not ask", 2026-06-15)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: ThemeProvider + themeScript wired into `app/layout.tsx`; all 16 new exports re-exported from `components/ui/index.ts` and exercised by `enterprise-ext.test.tsx` (render + behavior); tokens consumed via `bg-sidebar`/`text-chart-*` utilities resolved through `@theme inline` (next build compiled clean).
- [x] DEAD-CODE (code) — no orphan: the shared inventory (StatCard/ChartCard/DataTable/AppSidebar parts/ThemeToggle) is the FOUNDATION this milestone's tasks 2–6 consume (same freeze-first pattern as v13 design-system-foundation, whose primitives were consumed by later surfaces). Referenced today by the barrel + the red→green suite; no unused locals (eslint 0 errors).
- [x] SEMANTIC — read globals.css / tokens.json / catalog.json in full: additive only, every alias resolves (`add.py check` 37/0), `:root` + `.dark` both carry every new var, `@theme` bridges each.

### GATE RECORD
Outcome: PASS  (auto-resolved on evidence + independent refute-read; autonomy: auto, risk: normal)
Evidence: vitest 281/281 · coverage 89.08%L · eslint 0-err · tsc clean · next build ✓ (18 routes) · add.py check 37/0 · refute-read EARNED (post-remediation)
Residue / follow-ups (→ §7 deltas): TanStack Table `react-hooks/incompatible-library` eslint WARNING (library-inherent, visible-not-disabled); dark-theme true color-contrast = the standing v13/v15 browser-only a11y residue (not closed here).
Reviewed by: auto-gate (the run) under Tin Dang's standing auto-mode authorization · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
