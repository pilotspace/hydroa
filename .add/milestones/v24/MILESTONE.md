# MILESTONE: UI polish & a11y follow-ups

goal: Resolve the three v23 review nits — Overview heading hierarchy (no h1→h3 skip), redundant SidebarTrigger aria-label default, and theme-script placement — so the dashboard's a11y and code hygiene match the rest of the surfaces, with every data seam byte-identical.
rationale: new-major (small) — a coherent batch of the three non-blocking nits surfaced by the v23 pre-merge review (frontend-expert subagent on PR #7). Not a change-request (no frozen contract changes; presentation/a11y/hygiene only) and too cross-file for a single in-place edit to leave undocumented. Confirmed by Tin 2026-06-16 ("yes" to "open a small v24 task for the 3 review nits").
stage: production · status: active · created: 2026-06-16

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
- Overview home (`/`) heading hierarchy: interpose `<h2>` (or wrap the chart + recent-activity blocks in `<section>` with `aria-labelledby`) so there is no `h1 → h3` skip — matching SpendPage/UsagePage's correct h1→h2→h3 structure.
- `SidebarTrigger` (`components/ui/sidebar.tsx`): remove (or explicitly document) the redundant hardcoded `aria-label="Toggle sidebar"` default that `{...props}` overrides.
- Theme no-flash script placement: move `<script>{themeScript()}</script>` out of the `"use client"` root layout into a server-component wrapper to avoid the React 19 dev-mode hydration warning.
Out: (anti-scope-creep)
- NO data-seam change (BFF routes/hooks/field names byte-identical — same v13/v15/v23 invariant).
- NO new visual redesign, NO new surfaces, NO token changes.
- NO change to auth behavior or any frozen contract.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Presentation/a11y/hygiene only: every existing per-surface + auth suite stays green with zero data-seam diff; the new red assertion targets ONLY the heading structure (e.g. `getByRole("heading",{level:2})` present; no level jump).
- a11y bar unchanged: jsdom-axe (serious|critical, contrast off) + the real-Chromium e2e-a11y pass must stay clean; this milestone tightens heading-order specifically.

## Shared / risky contracts (freeze these first)
- None — no network/data contract. The "contract" is the heading-outline + component-shape assertion, frozen in the single task's §3.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] overview-heading-a11y-fix   depends-on: none   — interpose h2 on the Overview chart + recent-activity sections (kill the h1→h3 skip); drop the redundant SidebarTrigger aria-label default; relocate themeScript to a server wrapper. Behavior + data seams byte-identical. → gate PASS 2026-06-16.

## Exit criteria (observable; map each to the task that delivers it)
- [x] The Overview page (`/`) has no heading-level skip — every section heading under the `<h1>` is reachable via an `<h2>` (verify: test apps/dashboard/tests-bff/overview-home.test.tsx + real-Chromium axe heading-order)   (← overview-heading-a11y-fix) — MET: ChartCard headingLevel={2} + CardTitle asChild → "Usage over time" + "Recent activity" now <h2>; test_overview_section_headings_are_level_2 + test_overview_outline_has_no_level_skip green; real-Chromium axe 5/5 (color-contrast ON).
- [x] `SidebarTrigger` no longer carries a redundant hardcoded `aria-label` default (or it is documented as intentional); the app-shell trigger still exposes its accessible name (verify: test apps/dashboard/tests/design-system/app-shell-sidebar.test.tsx)   (← overview-heading-a11y-fix) — MET: DS default kept as documented single source of truth, redundant consumer aria-label removed; test_sidebartrigger_name_from_ds_default + test_desktop_rail_collapses_keeps_names green.
- [x] The theme no-flash script renders from a server component (no `"use client"` root-layout `<script>`); theme still applies with no flash and `next build` stays clean (verify: command `cd apps/dashboard && npm run build`)   (← overview-heading-a11y-fix) — MET: themeScript extracted to non-client components/ui/theme-script.ts; app/layout.tsx is a Server Component; client context in app/providers.tsx; `next build` clean (18 routes); theme-script-server.test.ts (5 tests) green.
