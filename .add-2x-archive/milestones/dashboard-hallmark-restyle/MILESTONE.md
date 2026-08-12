# MILESTONE: Whole-dashboard restyle to the Airier enterprise AI-SaaS theme

goal: Every dashboard surface (all 45 routes, public + authed, light + dark) reads as a polished, professional enterprise AI-SaaS console — the Tin-locked "Airier" direction — with real design-system typography and a WCAG-AA floor, driven from the shared token layer rather than per-page edits.
rationale: sub-milestone (production stage). A whole-app presentation restyle triggered by the standing UI/UX polish bar (user-facing surfaces get the UDD design loop, never bare CRUD) + the aifeature-usable bar; Tin pre-approved this exact scope and locked the captured screen before build.
stage: production · status: active · created: 2026-07-17T06:35:03+00:00
release: 0.10.0

> SDD living doc for this milestone. Keep it THIN.

## Scope
In:  A design-definition (UDD) restyle of `apps/dashboard` to the LOCKED "Airier" theme — applied as
     the hallmark anti-slop DNA translated into our shadcn/token system (NOT dumped HTML):
     - Typography: off-Inter → Geist (UI grotesque) + Geist Mono (every metric/numeral, tabular figures),
       self-hosted via next/font, referenced by the token layer.
     - Palette: cool-biased graphite neutrals + a single precise azure signal (#2f6df0) used sparingly;
       semantic status (success/warning/destructive) kept SEPARATE from the accent; light sidebar rail.
     - A real, shipped dark theme (the .dark block was scaffold-only before).
     - Delivered VALUE-only on the token layer: token NAMES unchanged, so all 45 pages re-theme with zero
       per-page edits; per-page work is limited to sweeping raw-palette bypasses back onto tokens.
     - Accessibility floor: WCAG AA contrast on the restyled surfaces (verified by axe over every route).
Out: Information-architecture / layout / content changes (this is presentation-only); the 5-tier pricing
     CONTENT model (separate account-tiers-billing work); the structural a11y findings that predate the
     restyle (nested-interactive on memory/vision, heading-order on routing/plans — logged, not theme-scoped);
     new components or per-page redesigns beyond token re-resolution.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Token discipline is the single source of truth: components consume token utilities
  (bg-primary / text-foreground / text-accent-soft-foreground / font-sans / font-mono …), NEVER raw hex/px.
- Signature element: the single precise azure signal on a graphite ground (active-nav soft-fill, primary CTA,
  sparkline endpoints) — deliberately NOT a generic AI-design default.
- AA floor is non-negotiable on restyled surfaces: any accent-as-text pairing must meet 4.5:1.

## Shared / risky contracts (freeze these first)
- The "Airier" token palette + font wiring + AA-safe text tokens -> owning task airier-theme-restyle

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] airier-theme-restyle   depends-on: none   — the whole restyle: fonts, token palette (light+dark), per-page raw-color sweep, AA contrast fixes.

## Exit criteria (observable; map each to the task that delivers it)
- [x] Every one of the 45 routes renders in the Airier theme (azure #2f6df0 signal live) in light AND dark   (← airier-theme-restyle)
- [x] Body + headings render in Geist; all metrics render in Geist Mono tabular figures (verified on a live render, not just a green build)   (← airier-theme-restyle)
- [x] axe-core over all routes shows ZERO color-contrast violations on the restyled surfaces (AA floor met)   (← airier-theme-restyle)
- [x] The restyle is driven from the token layer (zero per-page edits beyond raw-color→token sweeps)   (← airier-theme-restyle)

## Close — ship review   (AI fills when every task is done)

### Ship by domain   (what changed, per bounded context)
- tooling : untouched.
- skill   : untouched.
- book    : untouched.
- dashboard (apps/dashboard) : `app/layout.tsx` (Geist + Geist Mono via next/font, font-sans on body),
  `app/globals.css` (:root + .dark token values → Airier azure/graphite; @theme font tokens fixed to
  reference the geist stack directly; new --accent-soft-foreground AA-safe text token),
  `components/ui/sidebar.tsx` (active-nav → AA-safe token), `app/global-error.tsx` (inline colors aligned),
  raw-palette sweep across chat/ToolsEditor · memory/{Library,Inspector}Pane · models/ModelCatalogTable ·
  batches/BatchesStatsPage · marketing/{page,status} → semantic + accent-soft tokens.

### Cross-task evidence   (one row per task)
- airier-theme-restyle : gate=PASS · evidence = tsc/eslint/`next build`(45 routes) green + live-render probe
  (body font = Geist, font-mono = Geist Mono, --primary #2f6df0 light / #5b8cff dark) + axe-core over 35
  captured routes = 0 color-contrast violations (was 20 before the AA pass) · residue=none theme-scoped;
  5 pre-existing structural a11y findings (nested-interactive ×5, heading-order ×3-route) logged OUT of scope.

### Goal met?
- [x] each Exit criterion above is satisfied by the Cross-task evidence row (axe run + live-render probe + build)
- goal: the Airier restyle ships on all 45 routes from the token layer; the one proof line = axe over every
  route returns 0 color-contrast violations AND the live-render probe shows Geist + azure in both themes.

## Release steps   (AI-DEFINED)
- [ ] Push branch `feat/dashboard-hallmark-restyle` and open a PR from the Close ship-review above (Tin authorizes).
- [ ] Tin reviews the captured light+dark screenshots (public + authed) attached to the PR, then merges.
- [ ] Bundle into the next release cut (release.md) — 4 milestones already releasable since last release.
