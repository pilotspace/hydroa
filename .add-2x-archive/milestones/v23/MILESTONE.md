# MILESTONE: Enterprise UI Overhaul

goal: A user navigates an enterprise-grade dashboard — branded collapsible sidebar, an at-a-glance Overview home, a light/dark theme toggle, and consistently restyled surfaces + auth pages matching the shadcn reference — with every existing data seam byte-identical.
rationale: new-major — an enterprise visual overhaul of `apps/dashboard/` to the shadcn dashboard-01 + component-showcase aesthetic (two reference images). A NEW product theme (visual identity / enterprise polish) no active milestone's goal covers, too big for one task — distinct from the closed v13 (design-system foundation) and v15 (feature coverage). Not a change-request: per the v13/v15 fold, presentation refactors keep the data seam byte-identical, and token additions go through the v13 sanctioned extension seam — so this changes no frozen contract. Confirmed by Tin 2026-06-15 (full breadth · Overview home · light+dark · adopt shadcn blocks · auth pages added per Tin's "login too simple" note).
stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
- Extend the FROZEN v13 token set via its sanctioned extension seam — chart color palette + sidebar/elevation tokens — and wire the existing `.dark` block into a working light/dark/system theme toggle that persists.
- Rebuild the app shell: a collapsible sidebar (brand/org header, grouped nav sections, user-profile footer, theme toggle) with a responsive mobile sheet.
- A NEW Overview home at `/` — KPI stat cards with trend deltas · usage-over-time chart with range toggles · recent-activity table.
- Restyle ALL 7 existing surfaces (Usage · Spend · Keys · Models · Teams · Routing · Settings) to the shared shadcn blocks (DataTable, ChartCard, StatCard).
- Redesign login + signup — split-screen brand panel, Card-wrapped token-styled fields, styled SSO.
- Adopt shadcn blocks: sidebar primitive + TanStack Table data-table + Recharts charts (new npm deps, lockfile-reviewed).

Out: (anti-scope-creep)
- NO change to BFF routes, hooks, or field names (presentation-only). The ONE possible data-layer exception — an Overview metrics read — is decided at the `overview-home` freeze, not assumed here.
- NO new backend features, NO auth/authz logic change, NO new RBAC.
- NO real-browser axe / color-contrast pass — that stays the standing v13/v15 browser-only residue; the dark theme ADDS a contrast surface to it, does not close it.
- NO i18n / localization.

## Shared decisions & glossary deltas   (living — every task must honor these)
- New glossary terms: **Overview** (the aggregate landing) · **StatCard / KPI card** · **ChartCard** · **AppSidebar** · **theme mode** (light·dark·system).
- Every redesigned surface CONSUMES the extended token set — no surface hardcodes a value a token covers (the v13 `tests/design-system` lint stays green).
- Presentation-only invariant: every redesigned surface keeps its BFF route + TanStack Query hook + field names BYTE-IDENTICAL (v13/v15 fold). Verified by the existing per-surface suites staying green with zero data-seam diff.
- New npm deps are reviewed via the lockfile (Node deps are NOT governed by `dependencies.allowlist` — v1 fold).
- a11y WCAG 2.2 AA holds on every surface: jsdom axe (serious|critical, color-contrast disabled) + keyboard/focus PRESENCE proxies; the dark theme's true contrast is browser-only residue.

## Shared / risky contracts (freeze these first)
- **Design-system extension** (new tokens + dark-mode mechanism + shared-component inventory & props: AppSidebar · DataTable · ChartCard · StatCard · ThemeToggle) -> owning task `design-system-enterprise-ext`. Tasks 2–6 build against it once frozen.
- **Overview metrics data contract** (shape + source: client-side aggregate from existing endpoints vs a new read-only gateway endpoint) -> owning task `overview-home`. Resolves milestone flag #1 at its §3 freeze.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] design-system-enterprise-ext   depends-on: none                          — extend v13 tokens (chart/sidebar/elevation) + ThemeProvider/toggle (light·dark·system, persisted) + freeze the shared-component inventory (AppSidebar, DataTable, ChartCard, StatCard, ThemeToggle).
- [ ] app-shell-sidebar              depends-on: design-system-enterprise-ext   — branded collapsible sidebar (org header, grouped nav, user-profile footer, theme toggle) + responsive mobile sheet; replaces the current AppShell nav.
- [ ] overview-home                  depends-on: design-system-enterprise-ext, app-shell-sidebar — new `/` Overview: ≥4 KPI cards with trend deltas + usage-over-time chart with range toggles + recent-activity table (defines + freezes the metrics data contract).
- [ ] console-surfaces-redesign      depends-on: design-system-enterprise-ext   — restyle Usage · Spend · Keys to StatCard/DataTable/ChartCard; data seams byte-identical.
- [ ] admin-surfaces-redesign        depends-on: design-system-enterprise-ext   — restyle Models · Teams · Routing · Settings to the shared components; data seams byte-identical.
- [ ] auth-pages-redesign            depends-on: design-system-enterprise-ext   — enterprise login + signup (split-screen brand panel, Card-wrapped token-styled fields, styled SSO); behavior byte-identical (same POST routes, validation, redirects).

## Exit criteria (observable; map each to the task that delivers it)
- [x] The token set gains chart + sidebar/elevation tokens AND a working dark theme, all `add.py check`-linted, with an extension record; toggling theme flips the rendered tokens.   (← design-system-enterprise-ext) (verify: test apps/dashboard/tests/design-system/enterprise-ext.test.tsx + command `python3 .add/tooling/add.py check`) ✓ gate PASS; check 40/0; enterprise-ext suite green
- [x] The app shell renders a collapsible branded sidebar (grouped nav + user-profile footer) and a theme toggle that persists across reloads; below `lg` the nav collapses to a mobile sheet.   (← app-shell-sidebar) (verify: test apps/dashboard/tests-bff/app-shell-sidebar.test.tsx) ✓ gate PASS; app-shell-sidebar suite green
- [x] Visiting `/` shows ≥4 KPI cards with trend deltas + a usage-over-time chart with working range toggles + a recent-activity table.   (← overview-home) (verify: test apps/dashboard/tests-bff/overview-home.test.tsx) ✓ gate PASS; overview-home suite green
- [x] Usage · Spend · Keys render via the shared StatCard/DataTable/ChartCard with BFF routes/hooks/field names byte-identical (existing suites green, zero data-seam diff).   (← console-surfaces-redesign) (verify: command `cd apps/dashboard && npm test`) ✓ gate PASS; vitest run 329/329
- [x] Models · Teams · Routing · Settings render via the shared components, data seams byte-identical.   (← admin-surfaces-redesign) (verify: command `cd apps/dashboard && npm test`) ✓ gate PASS; vitest run 329/329
- [x] Login & signup render the split-screen enterprise layout with token-styled Card forms + styled SSO; same POST routes, validation, and redirects.   (← auth-pages-redesign) (verify: test apps/dashboard/tests/login.test.tsx + test apps/dashboard/tests/signup.test.tsx) ✓ gate PASS; login+signup suites green (refute-read EARNED)
