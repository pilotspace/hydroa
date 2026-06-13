# Hydroa — Design (DESIGN.md)

> The design source of truth for **Hydroa** (production) — drafted 2026-06-13.
> The AI drafts every UI screen against this doc; the JSON foundation below is what
> the validators lint (`.add/design/`).

This doc is the **prose front-door** to the UDD foundation: it carries the design
*identity* and *intent* a human owns, then points at the named-set JSON
(`tokens.json` · `catalog.json` · `prototypes/`) the AI renders from. Frozen as the
v13 `design-system-foundation` contract (approved by Tin Dang, 2026-06-13).

## Identity

Design identity is **human-owned** (confirmed by Tin Dang at specify, 2026-06-13).

- **Brand color** — Indigo 600 `#4F46E5` (the single primary: primary buttons, links,
  focus rings, active nav, the first chart series).
- **Palette** — accent → indigo-600 (hover indigo-700, ring indigo-500) · surface → white
  / slate-50 · text → slate-900 (muted slate-500) · border → slate-200 · success → emerald-600 ·
  warning → amber-500 · danger → red-600. Neutral ramp = **slate** (cool grey).
- **Typeface** — **Inter** (`Inter, system-ui, sans-serif`) for both headings and body;
  weights actually used: 400 regular · 500 medium · 600 semibold · 700 bold.
- **Voice / tone** — precise · calm · trustworthy (a billing/governance console, not a toy).

These flow into the **semantic** layer of `tokens.json`; the AI wires the primitives
beneath them. **Theme:** light is the default and only mode shipped; the token layer is
themeable (CSS vars under `:root`, a `.dark` block scaffolded) — no toggle ships in v13.

## Principles

- **Primary user & their job** — a tenant owner/developer who logs in to watch spend,
  govern API keys & budgets, and trust the numbers. The one job: *see accurate cost and
  act on it without friction.*
- **Design principles** — one primary action per surface · never hide state (loading ·
  empty · error · success always rendered) · money & limits are unambiguous · the data
  never changes shape, only its presentation improves.
- **Accessibility floor** — WCAG 2.2 AA: AA contrast against the light surface · visible
  `focus-visible` ring on every interactive element · hit-target ≥ 44px · keyboard-operable
  dialogs (focus-trap + ESC) · landmarks (skip-link → `<nav>` → `<main id="main">`).

## Screens

One `.add/design/prototypes/<name>.json` per screen (a flat json-render tree the catalog validates).

| Screen | Prototype | Status |
|--------|-----------|--------|
| Foundation sampler (usage/cost shape) | `.add/design/prototypes/dashboard-foundation.json` | seed |
| Usage & cost (`/usage` + `/spend`) | `design/prototypes/usage-cost.json` | (v13 task usage-cost-ui) |
| Key & budget governance (`/keys`) | `design/prototypes/key-governance.json` | (v13 task key-budget-governance-ui) |

## Foundation

The named-set JSON the validators lint — under `.add/design/`:

- **Tokens** — `.add/design/tokens.json` · 3-layer (primitive · semantic · component).
  Dialect: `.add/tooling/templates/udd-tokens.md`.
- **Catalog** — `.add/design/catalog.json` · component catalog (typed props + token bindings).
  Render adapter: `.add/tooling/templates/udd-catalog.md`.
- **Prototypes** — `.add/design/prototypes/<name>.json` · flat json-render screen trees.

`python3 .add/tooling/add.py check` lints this named set in place — a layer/catalog/tree/
cross-file violation goes red with a named code; silent when there is no `design/` set.

## Implementation binding (this repo)

The token layer is wired into the running app as the SINGLE source:
`apps/dashboard/app/globals.css` `@theme` (light `:root` + scaffolded `.dark`) → consumed by
`apps/dashboard/components/ui/*` (shadcn/ui + Radix primitives, `lib/cn.ts` merge) and the
`AppShell` in `apps/dashboard/app/layout.tsx`. Charts use Recharts (themeable via the same vars).

## Render

To turn the catalog + a prototype into a live, clickable UI, follow the render recipe in
**`.add/tooling/templates/udd-catalog.md`** (`## Render recipe`): `catalog.json` →
`defineCatalog(...)`, then `catalog.validate(spec)` on the flat tree as-is.
