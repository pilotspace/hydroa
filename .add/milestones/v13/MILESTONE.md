# MILESTONE: UI/UX refresh — highest-value dashboard journeys (usage/cost + key/budget governance)

goal: a tenant owner/developer experiences the usage/cost dashboard and the key & budget governance journeys as a polished, consistent, accessible, and responsive product — unified design system, clearer task flows, WCAG 2.2 AA, and tablet/mobile breakpoints — with NO change to the underlying data, BFF, or gateway contracts
rationale: sub-milestone of the production roadmap. Intake → `sub-milestone` (a UI/UX theme slice across several existing dashboard surfaces, too big for one task — a shared design-system contract + 2 surface redesigns + a verification pass, the same shape as the parity slices). Scoped to the HIGHEST-VALUE surfaces first (Tin, 2026-06-13): the usage/cost dashboard (`/usage` + `/spend`) and the key & budget governance journey (`/keys` + budgets) — the two flows a paying tenant lives in daily. The remaining surfaces (auth, model catalog, SSO/OIDC config, routing-admin, team-governance screens) are an explicit follow-up UI/UX milestone. All four UX lenses apply (Tin): visual/design-system consistency · usability & flow · accessibility (WCAG 2.2 AA) · responsive/mobile. This is DEPTH on the existing `apps/dashboard/` (Next.js 15 + shadcn/ui + Tremor + TanStack Query, dark-mode-first) — it polishes existing features, it does NOT add new data or endpoints.

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A UI/UX refresh of the two highest-value dashboard journeys, applying ALL FOUR lenses
     to each surface:
     - **Usage/cost dashboard** (`app/(dashboard)/usage`, `/spend`; components UsagePage,
       UsageStatsCards, UsageTable, BudgetWidget, BudgetEditForm, SpendPage): consistent
       Tremor chart styling + tokens, clearer cost/usage hierarchy and empty/loading/error
       states, accessible tables & charts (caption, scope, aria, keyboard), responsive
       layout down to mobile.
     - **Key & budget governance** (`app/(dashboard)/keys`; components KeysPage, KeyRow,
       CreateKeyDialog, KeyGovernanceEditor, PlaintextKeyBanner): clearer create→reveal-once
       →govern flow, consistent dialog/form/button variants, accessible dialogs (focus trap,
       ESC, labelled controls), responsive key list & forms.
     - **Shared shell** (`app/layout.tsx`, navigation): a unified design-token baseline,
       standard state patterns (loading/empty/error), responsive nav frame, and the a11y
       primitives (visible focus ring, skip-link, landmark roles) every surface inherits.
     The design system (tokens + component variants + state/responsive/a11y conventions) is
     frozen FIRST as a shared contract; the surface redesigns build against it. Behavior is
     PRESERVED: the BFF catch-all (`/api/gw/[...path]`), TanStack Query data hooks, field
     names, and gateway contracts are untouched — this is presentation/interaction only.
Out: NEW data, metrics, endpoints, or gateway/BFF contract changes (presentation-only
     milestone); the remaining surfaces — auth (login/signup), model catalog
     (ModelCatalogTable), SSO/OIDC config screens, routing-admin, team-governance UI —
     DEFERRED to a follow-up UI/UX milestone; a full design-tool (.pen) handoff /
     pixel-perfect rebrand (the refresh works within the existing shadcn/Tremor system, it
     does not replace it); dark/light theme toggle beyond the existing dark-mode-first
     default; i18n/localization; the v12 billing/ops follow-ups (separate milestone, runs
     BEFORE this one).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: **design token** — a named, themeable primitive (color/space/radius/type-scale)
  defined ONCE in the shared layer (Tailwind theme / CSS vars) and consumed by every
  surface; no surface hardcodes a raw hex/px that a token covers.
- GLOSSARY: **state pattern** — the standard loading · empty · error · success rendering a
  surface MUST handle (the v1 UDD rule, now given shared components so every surface renders
  them identically).
- Behavior-preserving / data-identical is non-negotiable: a redesigned surface calls the
  SAME BFF route + the SAME TanStack Query hook with the SAME field names; the redesign
  changes markup/styling/interaction ONLY. Existing BFF + RTL behavioral tests stay green
  (presentation refactors must not break a data-shape assertion).
- Accessibility floor = WCAG 2.2 AA, enforced (not just aspired): every redesigned surface
  passes an automated axe-core scan with zero serious/critical violations AND keyboard-only
  operability; contrast tokens meet AA against the dark-mode-first background.
- Scenario observables anchor WHERE text/state appears (the v1 UDD fold) — RTL assertions
  scope with `within(section)`, never a bare global `getByText`.

## Shared / risky contracts (freeze these first)
- The design-system contract — the shared token set (color/space/type/radius), the component
  variant inventory (button/card/dialog/table/badge/input states), the standard
  loading/empty/error/success state components, the responsive breakpoint scale, and the a11y
  primitives (focus ring, skip-link, landmark/ARIA conventions) — the single source every
  surface redesign consumes. Freezing it first prevents two surfaces from diverging into
  parallel ad-hoc styles → owning task `design-system-foundation` (FREEZE FIRST — both
  surface tasks build against it).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] design-system-foundation       depends-on: none                          — freeze the shared design tokens + component variants + standard state components (loading/empty/error/success) + responsive breakpoint scale + a11y primitives (focus ring, skip-link, landmarks) + the responsive shell/nav frame; prove the baseline renders and existing data-behavior tests stay green. FREEZE FIRST.
- [ ] usage-cost-ui                   depends-on: design-system-foundation      — refresh the usage/cost dashboard surfaces (`/usage` + `/spend`: UsagePage, UsageStatsCards, UsageTable, BudgetWidget, BudgetEditForm, SpendPage) applying all four lenses; Tremor charts re-styled to tokens, accessible tables/charts, responsive layout; same data hooks/field names.
- [ ] key-budget-governance-ui        depends-on: design-system-foundation      — refresh the key & budget governance surfaces (`/keys`: KeysPage, KeyRow, CreateKeyDialog, KeyGovernanceEditor, PlaintextKeyBanner, BudgetWidget/EditForm) applying all four lenses; clearer create→reveal-once→govern flow, accessible dialogs (focus trap/ESC/labels), responsive forms; same data hooks/field names.
- [ ] ui-ux-verify                    depends-on: usage-cost-ui, key-budget-governance-ui — verification pass across the two redesigned journeys: automated axe-core a11y scan (zero serious/critical), keyboard-only operability, responsive breakpoints (desktop/tablet/mobile), state patterns (loading/empty/error), and the existing BFF+RTL behavioral suites stay green; manual review of the live journeys.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A shared design-token + component-variant + state-pattern layer exists and is consumed by every redesigned surface — no surface hardcodes a value a token covers (← design-system-foundation)
- [ ] The usage/cost dashboard (`/usage` + `/spend`) is visually consistent (tokens), has clear cost/usage hierarchy with loading/empty/error states, accessible tables & charts, and is usable down to mobile breakpoints (← usage-cost-ui)
- [ ] The key & budget governance journey (`/keys` + budgets) has a clear create→reveal-once→govern flow, consistent accessible dialogs/forms, and responsive layout (← key-budget-governance-ui)
- [ ] Both journeys pass an automated axe-core scan with zero serious/critical WCAG 2.2 AA violations and are fully keyboard-operable (← ui-ux-verify)
- [ ] Behavior/data is unchanged: the same BFF routes + TanStack Query hooks + field names are used; the existing BFF + RTL behavioral suites stay green (← all tasks; gated by ui-ux-verify)
- [ ] The redesigned surfaces render correctly across desktop / tablet / mobile breakpoints (← ui-ux-verify)
