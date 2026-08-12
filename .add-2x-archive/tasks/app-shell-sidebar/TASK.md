# TASK: Branded collapsible sidebar shell + responsive mobile sheet

slug: app-shell-sidebar · created: 2026-06-15 · stage: production
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
- `apps/dashboard/components/ui/app-shell.tsx` — `AppShell({ children, activePath?, role? })` + the module-level `NAV_ITEMS: NavItem[]` ({href,label,icon,minRole?}). The CURRENT shell: a flat `<nav aria-label="Primary">` that is a top bar on mobile and a fixed `lg:w-60` sidebar at `lg+`; hardcodes brand "Hydroa"; role-filters admin-only links (member hides Models/Teams/Routing); renders skip-link→`#main`, the Primary nav, and `<main id="main">`. This task REWRITES its internals to compose the v23 `Sidebar` parts + a collapsible desktop rail + a mobile sheet + `ThemeToggle`, while keeping its export, props, and every FROZEN-test invariant byte-compatible.
- `apps/dashboard/components/dashboard-shell.tsx` — `DashboardShell({ children, activePath? })` (client). Reuses `useCurrentUser()` (the `["current-user"]` query) and forwards `role` to AppShell; currently passes NO `activePath` (so nothing is marked active today). This task wires `usePathname()` here → `activePath`, and forwards the user identity (email/role) for the sidebar footer. NO new network call.
- `apps/dashboard/components/ui/sidebar.tsx` — the v23 reusable parts shipped by `design-system-enterprise-ext`, consumed here verbatim: `Sidebar` (`<nav>`, defaults `aria-label="Primary"`, `bg-sidebar`), `SidebarHeader`, `SidebarBrand({title,icon?})`, `SidebarContent`, `SidebarGroup`, `SidebarGroupLabel`, `SidebarItem({href,icon?,active?})` (`aria-current="page"` when active), `SidebarFooter`, `SidebarTrigger({onClick?})` (accessible-named collapse button, `PanelLeft` icon).
- `apps/dashboard/components/ui/theme-toggle.tsx` — `ThemeToggle({className?})`: keyboard-operable button cycling light→dark→system, accessible name "Toggle theme (current: X)". Placed in the sidebar footer + the mobile header.
- `apps/dashboard/components/ui/dialog.tsx` — Radix `Dialog`/`DialogTrigger`/`DialogContent`/`DialogTitle`/`DialogClose` (already allow-listed `@radix-ui/react-dialog`). The mobile sheet = a Dialog whose content holds the nav; Radix portals `DialogContent` ONLY when open, so the DEFAULT (closed) render has exactly one Primary nav (the desktop rail) — this is what keeps `getByRole("navigation",{name:/primary/i})` single.
- `apps/dashboard/lib/hooks/use-current-user.ts` — `useCurrentUser(): { data: { email, role, … } | null, isLoading, isError }`. Source of `role` (nav filter, fail-open) + `email` (footer).
- `apps/dashboard/components/ui/index.ts` — barrel; AppShell + all Sidebar parts + ThemeToggle + Dialog already exported (no new export needed unless the contract adds a symbol).
- `apps/dashboard/lib/cn.ts` — `cn()` class-merge used by every part. lucide-react icons (`PanelLeft`, brand icon, nav icons) already a dep.

Context (working folder):
- FROZEN tests that MUST stay green (this task changes AppShell internals, not its contract):
  - `apps/dashboard/tests/design-system/components.test.tsx` M5 "responsive a11y shell" — `getByRole("link",{name:/skip to (main )?content/i})` href `#main`; SINGLE `getByRole("navigation",{name:/primary/i})`; `document.getElementById("main")` is a `MAIN`.
  - `apps/dashboard/tests/design-system/a11y.test.tsx` R5 — `<AppShell>` axe has no violations.
  - `apps/dashboard/tests/ui-ux-verify.test.tsx` — `a[href="#main"]` "skip to main"; single Primary nav; `main#main`; axe serious|critical `=== []`; AND `container.querySelector('[class*="lg:flex-row"]')` NOT null (root flex keeps `lg:flex-row`).
- `apps/dashboard/vitest.config.*` — jsdom + coverage threshold lines:80 (the bar to hold). jsdom does NOT apply Tailwind CSS, so responsive `hidden`/`lg:` classes do NOT hide elements from `getByRole` — duplicate landmarks must be avoided by NOT mounting them (portal/`open`), never by CSS alone.
- `apps/dashboard/tests/design-system/allowlist.json` — frozen dep allow-list; `@radix-ui/react-dialog`, `lucide-react`, `@tanstack/react-table` already listed → this task needs NO new dependency.

Honors (patterns / conventions):
- UDD fold v13 (PROJECT.md): consume the frozen design system — token utilities only (R3: no raw `#hex`/bare `Npx` in `components/ui/*`); hand-roll on native + ARIA; reuse Radix only where v13 already adopted it (Dialog ✓). a11y = jsdom-axe serious|critical with color-contrast DISABLED + keyboard/focus PRESENCE; true contrast is the standing browser-only residue.
- v13 shell contract (the frozen tests above): skip-link FIRST · ONE Primary `<nav>` landmark · `<main id="main">` · responsive `lg:flex-row` root. Additive-only — never weaken these.
- Security/UX RBAC split (v18 fold): the nav role-filter is UX-only and FAILS OPEN (role null/loading ⇒ all links shown); the gateway is the RBAC source of truth — never a client lockout.

Anchors the contract cites: `apps/dashboard/components/ui/app-shell.tsx` (`AppShell` props `{children,activePath?,role?,userEmail?}` · `NAV_ITEMS`) · `apps/dashboard/components/dashboard-shell.tsx` (`DashboardShell` + `usePathname()` activePath wiring) · reused parts `components/ui/sidebar.tsx` (`Sidebar`/`SidebarBrand`/`SidebarItem`/`SidebarTrigger`/`SidebarFooter`/…) · `components/ui/theme-toggle.tsx` (`ThemeToggle`) · `components/ui/dialog.tsx` (`Dialog`/`DialogContent`/`DialogTitle` mobile sheet) · `lib/hooks/use-current-user.ts` (`useCurrentUser` → role/email) · invariants: skip-link→`#main` · single `nav[aria-label="Primary"]` · `main#main` · `lg:flex-row` root · axe serious|critical clean. New test file: `apps/dashboard/tests/design-system/app-shell-sidebar.test.tsx`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: The live enterprise app shell — `AppShell` rebuilt on the v23 `Sidebar` parts: a branded, collapsible desktop navigation rail; a responsive mobile sheet (hamburger → Dialog); a theme toggle; and a user-identity footer — composed WITHOUT breaking the frozen v13 shell contract (skip-link · single Primary nav · `main#main` · `lg:flex-row` root · axe-clean · role-filtered nav).

Framings weighed: rewrite `AppShell` internals in place to compose the v23 Sidebar parts + Radix Dialog sheet, keep export+props+frozen-invariants (chosen) · a brand-new `AppShellV2` component beside the old one (rejected: two shells to keep green, dead-code risk, the frozen tests already pin `AppShell`) · CSS-only responsive show/hide of two always-mounted navs (rejected: jsdom doesn't apply Tailwind → duplicate Primary-nav landmarks break `getByRole` single-match + axe landmark-unique)

Must:
<must>
  - Rebuild `AppShell` from the v23 `Sidebar` parts (`Sidebar`/`SidebarHeader`/`SidebarBrand`/`SidebarContent`/`SidebarGroup`/`SidebarItem`/`SidebarFooter`/`SidebarTrigger`) — a branded desktop rail (`SidebarBrand` title "Hydroa" + a token-styled brand icon) over the existing `NAV_ITEMS`, consuming `--sidebar-*` tokens only (R3).
  - PRESERVE the frozen v13 shell contract EXACTLY: a skip-link as the first focusable targeting `#main` (text matching /skip to (main )?content/i); EXACTLY ONE `nav[aria-label="Primary"]` landmark in the default (mobile-sheet-closed) render; a `<main id="main">` landmark; the root flex wrapper keeps a `lg:flex-row` class; axe reports no serious|critical violations.
  - Mark the active route: `AppShell` accepts `activePath?` and the matching `SidebarItem` carries `aria-current="page"` (no item active when `activePath` is undefined).
  - Role-filter the nav as today and FAIL OPEN: when `role === "member"`, items with `minRole:"admin"` (Models/Teams/Routing) are hidden; for any other value INCLUDING `null`/`undefined` (loading or failed identity) ALL links show. The gateway remains the RBAC source of truth — never a client lockout.
  - Provide a collapsible desktop rail: a `SidebarTrigger` with an accessible name toggles an expanded↔collapsed state; collapsed hides the textual labels VISUALLY while every nav item keeps an accessible name (link text stays the accessible name via `aria-label`/sr-only, never removed). Default state = expanded.
  - Provide a responsive mobile sheet: a hamburger control (accessible name) opens a Radix `Dialog` whose content holds the SAME nav (role-filtered, active-marked) and a labelled `DialogTitle`; it is closed by default (so `DialogContent` is not mounted → no second Primary nav), traps focus, and closes on Escape / overlay / a close control.
  - Mount a `ThemeToggle` in the shell (sidebar footer on desktop, mobile header) — keyboard-operable with an accessible name; it does not alter nav semantics.
  - Show a user-identity footer: `AppShell` accepts `userEmail?` and renders it (with the role when present) in `SidebarFooter`; absent identity renders the footer without crashing (no email shown).
  - Wire the live shell: `DashboardShell` computes `activePath` from `usePathname()` and forwards `role` + `userEmail` from the existing `useCurrentUser()` (`["current-user"]`) query — NO new network call, no new dependency.
  - Additive-only on deps + design set: no token edited, no new package (Dialog/lucide/react-table already allow-listed); `add.py check` and the full v13/v15 design-system suite stay green.
</must>
Reject:
<reject>
  - more than one `nav[aria-label="Primary"]` in the default render (e.g. an always-mounted mobile nav) -> "duplicate_primary_landmark"   (single-Primary-nav test + axe landmark-unique)
  - the skip-link removed/displaced from first-focusable or no longer targeting `#main`, or `#main` not a `<main>` -> "shell_landmark_regression"   (frozen M5 / ui-ux-verify)
  - the root flex wrapper losing its `lg:flex-row` responsive class -> "responsive_regression"   (frozen ui-ux-verify class assert)
  - a `member` seeing an admin-only link, OR a null/loading role hiding any link (fail-closed) -> "rbac_nav_leak_or_lockout"
  - a collapsed rail dropping a nav item's accessible name (icon-only with no name) -> "a11y_violation"   (axe serious|critical / accessible-name)
  - a raw `#hex` or bare `Npx` literal introduced in any `components/ui/*` file touched -> "raw_value_in_ui"   (R3 guard)
  - a new package in package.json absent from allowlist.json -> "unlisted_dependency"   (R6 guard)
</reject>
After:
<after>
  - The dashboard renders a branded, collapsible, token-driven sidebar with active-route marking, a working mobile sheet, a theme toggle, and a user footer — and every frozen v13/v15 test plus `add.py check` is still green with zero token edited and zero new dependency.
  - `DashboardShell` marks the current route active via `usePathname()` and shows the signed-in user, reusing the existing identity query with no new fetch.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] the mobile sheet reuses the existing Radix `Dialog` (not a new "sheet" lib) and relies on Radix portaling `DialogContent` ONLY when open to keep the default render at one Primary nav — lowest confidence because if a future forceMount or an always-rendered variant is needed, two Primary landmarks reappear and the frozen single-nav test breaks. If wrong: label the sheet nav distinctly (e.g. `aria-label="Primary (mobile)"`) and keep only the desktop rail as "Primary" — a one-line aria-label change, no API/token change.
  ⚠ [contract] collapse is desktop-only client state (`useState`, default expanded) that hides labels visually while preserving accessible names — lowest confidence because "collapsed" a11y is easy to get wrong (an icon-only link must still expose its name). If wrong: keep labels always-visible and make the trigger a no-op/remove it — drops the collapse affordance only, no contract-shape change.
  - [x] brand stays "Hydroa" (matches current `AppShell` + root metadata) → no copy decision needed.
  - [x] `activePath` derives from `usePathname()` in `DashboardShell`; `AppShell` stays PURE (props only, no router hook) so the frozen tests render it without a router → no test-harness change.
  - [x] no persistence of collapse/theme beyond what v23 already ships (theme persists via ThemeProvider; collapse is ephemeral) → no storage contract here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: the shell renders the branded sidebar over the nav items
  Given AppShell rendered with role "owner"
  When it mounts
  Then the brand "Hydroa" is shown in the sidebar
  And every NAV_ITEMS link (Usage, Spend, API Keys, Models, Teams, Routing, Settings) is present and keyboard-reachable

Scenario: the frozen v13 shell contract still holds
  Given AppShell rendered with page content
  When it mounts (mobile sheet closed)
  Then a skip-link with text /skip to (main )?content/ targets #main and is the first focusable
  And there is exactly one navigation landmark named "Primary"
  And a <main id="main"> landmark exists
  And the root flex wrapper carries a lg:flex-row class
  And axe reports no serious or critical violations

Scenario: the active route is marked
  Given AppShell rendered with activePath="/spend"
  When it mounts
  Then the Spend item carries aria-current="page"
  And no other item is marked current

Scenario: no item is current without an active path
  Given AppShell rendered with activePath undefined
  When it mounts
  Then no nav item carries aria-current="page"

Scenario: a member does not see admin-only links
  Given AppShell rendered with role "member"
  When it mounts
  Then Models, Teams, and Routing are absent
  And Usage, Spend, API Keys, and Settings remain present

Scenario: an unknown/loading role fails open
  Given AppShell rendered with role null
  When it mounts
  Then all seven nav links are shown (no link hidden)

Scenario: the desktop rail collapses and keeps accessible names
  Given AppShell rendered expanded with a collapse trigger
  When the collapse trigger is activated
  Then the rail enters the collapsed state
  And every nav item still exposes its accessible name (Usage…Settings)

Scenario: the mobile sheet opens, holds the nav, and closes on Escape
  Given AppShell rendered with a hamburger control (sheet closed by default)
  When the hamburger is activated
  Then a dialog opens with a labelled title and the same role-filtered nav links
  And pressing Escape closes the dialog

Scenario: the theme toggle is present and keyboard-operable
  Given AppShell rendered
  When the theme toggle is focused and activated via keyboard
  Then a control with an accessible name "Toggle theme (current: …)" is present and the theme changes

Scenario: the user identity is shown in the footer
  Given AppShell rendered with userEmail="ada@hydroa.io" and role "owner"
  When it mounts
  Then the footer shows ada@hydroa.io
  And given no userEmail, the footer renders without an email and without crashing

Scenario: the live shell marks the route from the URL
  Given DashboardShell wrapping content with the current path "/keys"
  When it renders
  Then it forwards activePath="/keys" to AppShell so API Keys is aria-current="page"
  And it reuses the ["current-user"] query (no new fetch)

# ── Reject scenarios (each names what must remain unchanged) ──
Scenario: a second always-mounted nav duplicates the Primary landmark — rejected
  Given AppShell in its default render
  When the DOM is queried for navigation landmarks named "Primary"
  Then there is exactly one (duplicate_primary_landmark)
  And the single desktop rail remains the Primary landmark

Scenario: a regressed skip-link/main landmark — rejected
  Given AppShell rendered
  When the skip-link or main landmark is checked
  Then the skip-link is first-focusable, targets #main, and #main is a <main> (shell_landmark_regression)
  And the frozen M5 / ui-ux-verify assertions still pass

Scenario: the responsive root class removed — rejected
  Given AppShell rendered
  When the root flex wrapper is inspected
  Then it still carries lg:flex-row (responsive_regression)
  And the mobile-stacked → desktop-row layout intent is preserved

Scenario: an RBAC nav leak or lockout — rejected
  Given a member role (must hide admin links) or a null role (must show all)
  When the nav is filtered
  Then a member never sees Models/Teams/Routing and a null role hides nothing (rbac_nav_leak_or_lockout)
  And the gateway remains the RBAC source of truth

Scenario: a collapsed icon-only link with no name — rejected
  Given the collapsed rail
  When axe / the accessible-name check runs
  Then every item still has an accessible name (a11y_violation)
  And labelled, keyboard-operable controls remain the contract

Scenario: a raw value in a touched ui component — rejected
  Given a components/ui/* file changed by this task
  When the R3 guard runs
  Then it contains no raw #hex or bare Npx (raw_value_in_ui)
  And token-named utilities remain the only styling path

Scenario: a new unlisted dependency — rejected
  Given package.json
  When the R6 guard runs
  Then no new package is introduced absent from allowlist.json (unlisted_dependency)
  And the frozen allow-list set is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a UI/COMPONENT contract (no HTTP surface). The frozen shape is the `AppShell`
signature + its observable invariants, the `DashboardShell` wiring, the parts it composes,
and the reject→guard mapping. ADDITIVE only: no v13 token, no allow-listed dep, no frozen
test is changed; `AppShell`'s export path and the v13 shell invariants are byte-compatible.

### A · `AppShell` — `apps/dashboard/components/ui/app-shell.tsx` (rewrite internals; same export)
```
export interface AppShellProps {
  children: React.ReactNode
  activePath?: string          // marks the matching nav item aria-current="page"; undefined ⇒ none active
  role?: string | null         // "member" hides minRole:"admin" items; ANY other value incl null/undefined ⇒ all shown (fail-open)
  userEmail?: string | null    // shown in the sidebar footer when present; absent ⇒ footer renders w/o email
}
export function AppShell(props: AppShellProps): JSX.Element
// module-level NAV_ITEMS: { href; label; icon; minRole?: "admin" }[]  — unchanged set:
//   /usage Usage · /spend Spend · /keys "API Keys" · /models Models[admin] · /teams Teams[admin] · /routing Routing[admin] · /settings Settings

Composition (presentational; consumes --sidebar-* tokens only, R3):
  - desktop rail = Sidebar(aria-label "Primary") > SidebarHeader(SidebarBrand title "Hydroa" + brand icon, SidebarTrigger)
      > SidebarContent(SidebarGroup > SidebarItem per filtered NAV_ITEMS, active = activePath===href)
      > SidebarFooter(userEmail + role when present, ThemeToggle)
  - mobile header (visible < lg) = brand + a hamburger button (accessible name e.g. "Open navigation") + ThemeToggle
  - mobile sheet = Dialog (closed by default) > DialogContent > DialogTitle("Navigation") + the SAME filtered/active nav links
  - collapsible: desktop SidebarTrigger toggles a local expanded↔collapsed state (default expanded);
      collapsed hides label TEXT visually but each item keeps its accessible name (never icon-only-without-name)

Invariants PRESERVED (the frozen v13 shell contract — never weakened):
  I1 skip-link is the FIRST focusable, text /skip to (main )?content/i, href "#main"
  I2 EXACTLY ONE nav[aria-label="Primary"] in the default (sheet-closed) render
  I3 a <main id="main"> landmark wraps {children}
  I4 the root flex wrapper carries a `lg:flex-row` class
  I5 axe (serious|critical, color-contrast disabled) === [] for the default render
```

### B · `DashboardShell` — `apps/dashboard/components/dashboard-shell.tsx` (wire the live shell)
```
export function DashboardShell({ children }: { children: React.ReactNode }): JSX.Element
  - activePath = usePathname()                       // next/navigation, client
  - { data } = useCurrentUser()                      // existing ["current-user"] query — NO new fetch
  - renders <AppShell activePath={activePath} role={data?.role ?? null} userEmail={data?.email ?? null}>
```
(The `(dashboard)/layout.tsx` already renders `<DashboardShell>` — unchanged.)

### C · Parts composed (consumed verbatim — NO change to these files)
```
components/ui/sidebar.tsx     Sidebar, SidebarHeader, SidebarBrand, SidebarContent, SidebarGroup,
                              SidebarGroupLabel, SidebarItem, SidebarFooter, SidebarTrigger
components/ui/theme-toggle.tsx ThemeToggle
components/ui/dialog.tsx       Dialog, DialogTrigger, DialogContent, DialogTitle, DialogClose
lib/hooks/use-current-user.ts useCurrentUser   (role + email)
```
No new export, no new dependency (Dialog/lucide already allow-listed). `add.py check` stays green.

### D · Reject → enforcing guard (a response for every §1 Reject code)
```
duplicate_primary_landmark -> app-shell-sidebar.test.tsx : getAllByRole("navigation",{name:/primary/i}).length === 1 (default render)
shell_landmark_regression  -> app-shell-sidebar.test.tsx + frozen M5/ui-ux-verify : skip-link first→#main, main#main present
responsive_regression      -> app-shell-sidebar.test.tsx + frozen ui-ux-verify : root has [class*="lg:flex-row"]
rbac_nav_leak_or_lockout   -> app-shell-sidebar.test.tsx : member hides admin items; null shows all 7
a11y_violation             -> app-shell-sidebar.test.tsx : axe serious|critical === [] (expanded AND collapsed) + every item named
raw_value_in_ui            -> tokens.test.ts R3 (existing) : offenders === []
unlisted_dependency        -> tokens.test.ts R6 (existing) : stray === []
```

Status: FROZEN @ v1 — approved by Tin Dang (standing auto-mode authorization, 2026-06-15: "implement in auto mode - with your best decision - do not ask"). autonomy: auto · risk: normal (UI/presentation; no auth/money/data-loss/method scope → not unguarded_high_risk_auto).

Least-sure flag surfaced at freeze: [contract] the mobile sheet reuses Radix `Dialog` and depends on `DialogContent` being portal-mounted ONLY when open to keep I2 (single Primary nav) in the default render — if a forceMount/always-rendered variant is ever needed, two Primary landmarks reappear and the frozen single-nav test breaks; if wrong: label the sheet nav distinctly (`aria-label="Primary (mobile)"`), a one-line change, no API/token change. ALSO [contract] collapse is desktop-only ephemeral `useState` (default expanded) that hides label text but preserves each item's accessible name — if collapsed a11y proves fragile, keep labels always-visible and drop the trigger (affordance only), no contract-shape change.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 80% (the v13/v15 dashboard bar) on the rewritten `app-shell.tsx` + `dashboard-shell.tsx`.
Plan (one test per scenario, asserting OBSERVABLE behavior — roles/aria/markers, never class strings except the frozen `lg:flex-row` probe) — `tests/design-system/app-shell-sidebar.test.tsx`:
<test_plan>
  - test_shell_renders_branded_nav: render AppShell role="owner" → brand "Hydroa" present + all 7 nav links present (role=link)
  - test_frozen_v13_shell_contract_holds: skip-link is first focusable, text /skip to (main )?content/i, href "#main"; main#main is a MAIN; axe serious|critical === []
  - test_single_primary_landmark: default render → getAllByRole("navigation",{name:/primary/i}).length === 1  (duplicate_primary_landmark)
  - test_responsive_root_class: root has [class*="lg:flex-row"]  (responsive_regression)
  - test_active_route_marked: activePath="/spend" → Spend link aria-current="page" AND no other link is current
  - test_no_active_without_path: activePath undefined → zero links with aria-current="page"
  - test_member_hides_admin_links: role="member" → Models/Teams/Routing absent; Usage/Spend/"API Keys"/Settings present  (rbac_nav_leak_or_lockout)
  - test_null_role_fails_open: role={null} → all 7 links shown  (rbac_nav_leak_or_lockout, fail-open)
  - test_undefined_role_fails_open: no role prop (undefined) → all 7 links shown  (contract names undefined fail-open; pins strict `=== "member"`) [refute-read add]
  - test_desktop_rail_collapses_keeps_names: click /toggle sidebar/i → rail data-state flips expanded→collapsed, trigger aria-expanded flips, every nav link still has its accessible name WITHIN the rail; axe serious|critical === [] collapsed  (a11y_violation)
  - test_mobile_sheet_opens_and_escape: click /open navigation/i → dialog with title "Navigation" + the role-filtered nav links; Escape closes it
  - test_theme_toggle_present_keyboard: AppShell inside ThemeProvider(light) → a /toggle theme/i control; focus+Enter flips html.dark
  - test_user_identity_footer: userEmail="ada@hydroa.io" → email shown; rerender without userEmail → no crash, email absent
  - test_dashboard_shell_marks_route_and_reuses_query: mock usePathname()="/keys" + useCurrentUser()={role:"owner",email} → AppShell gets activePath so "API Keys" aria-current="page"; email shown; useCurrentUser mock called (reuses ["current-user"], no new fetch)
  # raw_value_in_ui / unlisted_dependency are enforced by the EXISTING tokens.test.ts (R3/R6) which stays green;
  # the frozen components.test.tsx (M5) + ui-ux-verify.test.tsx independently re-assert the shell-landmark + lg:flex-row invariants.
</test_plan>
RED expectation: the suite fails because `AppShell` does not yet render a branded `Sidebar`/collapse-trigger/mobile-sheet/theme-toggle/footer and `DashboardShell` does not yet wire `usePathname()`/`userEmail` — assertions on brand, collapse data-state, the sheet dialog, the theme toggle, and the active-route-from-URL all fail until Build. (The pre-existing flat shell already satisfies a few invariants — skip-link/main/Primary nav — so those specific asserts are green-by-design; the suite as a whole is red.)
RED confirmed: 5 failed | 8 passed — the 5 reds are exactly the missing v23 behaviors (collapse data-state, mobile sheet dialog, theme toggle, user footer, DashboardShell route-from-URL); the 8 greens are the v13 invariants the flat shell already honors (skip-link · main#main · single Primary nav · lg:flex-row · activePath-prop marking · member-hides-admin · null-fails-open · branded links).
Refute-read strengthening (verify→tests step-back, re-snapshotted): the independent refute-read returned EARNED-WITH-GAPS (all I1–I5 + RBAC fail-open genuinely satisfied in code; no frozen test weakened). 4 MINOR coverage gaps closed in the NEW suite (now 14 tests, all green vs the built code): +test_undefined_role_fails_open; sheet asserts ALL 7 links (was 2); collapse names scoped within(rail); DashboardShell "no new fetch" tightened to toHaveBeenCalledTimes(1). Gap #5 (frozen a11y.test.tsx R5 doesn't pass color-contrast:disabled) left UNTOUCHED — it is a FROZEN v13 guard (editing it trips the tamper tripwire); it passes today and is carried as §7 residue.

Tests live in: `apps/dashboard/tests/design-system/app-shell-sidebar.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/ui/app-shell.tsx` `apps/dashboard/components/dashboard-shell.tsx` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo`
Strategy (ordered batches): 1. rewrite `app-shell.tsx` internals — keep `NAV_ITEMS` + `AppShellProps` (add `userEmail?`); compose `Sidebar`/`SidebarBrand`(Hydroa+icon)/`SidebarTrigger`(aria-expanded+data-state)/`SidebarContent`/`SidebarItem`(active) + `SidebarFooter`(email/role + `ThemeToggle`); add mobile header (brand + hamburger `DialogTrigger` "Open navigation" + `ThemeToggle`) + mobile sheet (`Dialog`/`DialogContent`/`DialogTitle` "Navigation", distinct nav label, closed by default); PRESERVE skip-link→#main, single Primary `<nav>`, `<main id="main">`, `lg:flex-row` root. 2. rewrite `dashboard-shell.tsx` — `usePathname()`→activePath + forward `data?.role`/`data?.email`. 3. run vitest (new + frozen) → green; 4. eslint + tsc + next build + `add.py check`.
Safety rule (feature-specific): ADDITIVE/REFACTOR only — same `AppShell` export + frozen v13 invariants byte-compatible; consume `--sidebar-*` tokens only (R3, no raw hex/px); reuse already-allow-listed Radix Dialog + lucide (NO new dependency, NO token edit). A collapsed item must NEVER lose its accessible name.
Code lives in: `apps/dashboard/components/ui/app-shell.tsx` + `apps/dashboard/components/dashboard-shell.tsx`
Constraints: do NOT change any test or the contract; allow-list packages only; R3 — no raw hex/px in components/ui; the mobile sheet stays closed-by-default so the default render keeps ONE Primary nav; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full dashboard suite 295/295 (38 files); new app-shell-sidebar.test 14/14; frozen shell guards (components.test M5 · a11y.test R5 · ui-ux-verify) all green
- [x] coverage did not decrease — 89.24% lines (threshold 80); app-shell.tsx 100% L (branch 90; 161-163 = the role-present sub-line in the footer, browser-only), dashboard-shell.tsx 100% L
- [x] no test or contract was altered during build — frozen §3 byte-identical; the v13/v15 guards untouched; the ONLY test edit (strengthening the NEW suite post-refute-read) was made AFTER stepping back to the tests phase with a tests→build re-snapshot — never during build
- [x] the green was EARNED — independent adversarial refute-read (sonnet subagent) returned EARNED-WITH-GAPS: it confirmed I1–I5 + the RBAC fail-open rule are genuinely satisfied IN CODE (not just asserted) and NO frozen test was weakened; the 4 actionable MINOR coverage gaps it found were remediated in-loop (undefined-role fail-open test added; sheet asserts all 7 links; collapse names scoped within(rail); "no new fetch" tightened to toHaveBeenCalledTimes(1)); the 5th (frozen a11y.test R5 not passing color-contrast:disabled) is a FROZEN guard left untouched (passes today) → §7 residue
- [x] concurrency / timing — N/A (presentation/client-state only). Collapse is local useState; the mobile sheet is Radix Dialog (focus-trap + Escape handled by the already-vetted primitive); no shared mutable state, no async race
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; userEmail/role render as PLAIN TEXT children (no raw-HTML prop); NO new dependency (Radix Dialog + lucide already allow-listed → R6 green); R3 green (token utilities only, no raw hex/px in app-shell.tsx)
- [x] layering & dependencies follow conventions — AppShell stays presentational/pure (props only, no router hook) so frozen tests render it without a router; DashboardShell is the client wrapper that owns usePathname()+useCurrentUser; composes the v13 design-system parts; barrel unchanged
- [x] reviewed — auto-gate (autonomy: auto) on complete evidence + independent refute-read (EARNED post-remediation); recorded under Tin's standing authorization ("implement in auto mode - with your best decision - do not ask", 2026-06-15)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `AppShell` rewrite composes Sidebar/SidebarBrand/SidebarTrigger/SidebarItem/SidebarFooter + ThemeToggle + Dialog (all imported from the v23 inventory); `DashboardShell` (rendered by `(dashboard)/layout.tsx`) wires `usePathname()` + `useCurrentUser()` into `<AppShell>`; the new `userEmail` prop is consumed in the footer; exercised by the 14-test suite (render + click + keyboard) AND the 3 frozen shell suites; next build compiled all 18 routes.
- [x] DEAD-CODE (code) — no orphan: `AppShell`'s export path + `NAV_ITEMS` unchanged (still the live shell via the layout); `NavLinks` helper used twice (rail + sheet); no unused import/local (eslint 0 errors). No `AppShellV2` shadow component (rejected in §1 framings exactly to avoid dead code).
- [x] SEMANTIC — read the rewritten app-shell.tsx + dashboard-shell.tsx in full: invariants I1–I5 present (skip-link first→#main · single `nav[aria-label="Primary"]` · `main#main` · `lg:flex-row` root · axe clean), mobile sheet nav labelled "Site" + closed-by-default, collapse keeps accessible names.

### GATE RECORD
Outcome: PASS  (auto-resolved on evidence + independent refute-read; autonomy: auto, risk: normal)
Evidence: vitest 295/295 (38 files) · coverage 89.24%L (app-shell + dashboard-shell 100%L) · eslint 0-err (1 carried TanStack warning in data-table.tsx) · tsc clean · next build ✓ (18 routes) · add.py check 37/0 · refute-read EARNED (post-remediation)
Residue / follow-ups (→ §7 deltas): frozen a11y.test.tsx R5 runs axe WITHOUT color-contrast:disabled (passes today only because jsdom can't resolve CSS-var colors — a latent fragility on an axe-core upgrade); dark-theme true color-contrast = standing v13/v15 browser-only a11y residue; desktop collapse to icon-rail is a browser-only visual (jsdom verifies the state machine: data-state + aria-expanded + preserved accessible names).
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
